"""API админки: дашборд, проверки, сайты, реестр нарушений, обратная связь, реестр РКН."""

import csv
import hashlib
import hmac
import io
import re
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import Date, cast, func, or_, select, text

from . import rkn
from .config import settings, shots_dir
from .db import Feedback, Rule, Scan, SessionLocal, kv_get, utcnow

router = APIRouter(prefix="/api/admin")
COOKIE = "wlc_admin"
TOKEN_TTL = 7 * 86400


# ---------- Авторизация ----------

def _sign(exp: int) -> str:
    return hmac.new(settings.secret_key.encode(), f"admin:{exp}".encode(), hashlib.sha256).hexdigest()


def _verify(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    exp_s, sig = token.split(".", 1)
    return exp_s.isdigit() and int(exp_s) > time.time() and hmac.compare_digest(sig, _sign(int(exp_s)))


def require_admin(wlc_admin: str | None = Cookie(default=None)) -> None:
    if not settings.admin_password or not _verify(wlc_admin):
        raise HTTPException(401, "Требуется вход")


class LoginIn(BaseModel):
    login: str
    password: str


@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response):
    redis = request.app.state.redis
    ip = request.headers.get("x-real-ip") or (request.client.host if request.client else "")
    key = f"admin-login:{ip}"
    if int(await redis.get(key) or 0) >= 10:
        raise HTTPException(429, "Слишком много попыток. Попробуйте через 15 минут.")
    ok = bool(settings.admin_password) and hmac.compare_digest(body.login, settings.admin_login) and hmac.compare_digest(
        body.password, settings.admin_password
    )
    if not ok:
        await redis.incr(key)
        await redis.expire(key, 900)
        raise HTTPException(401, "Неверный логин или пароль")
    exp = int(time.time()) + TOKEN_TTL
    response.set_cookie(COOKIE, f"{exp}.{_sign(exp)}", max_age=TOKEN_TTL, httponly=True,
                        secure=settings.cookie_secure, samesite="strict", path="/api/admin")
    return {"ok": True}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(COOKIE, path="/api/admin")
    return {"ok": True}


@router.get("/me", dependencies=[Depends(require_admin)])
async def me():
    return {"login": settings.admin_login}


# ---------- Дашборд ----------

@router.get("/stats", dependencies=[Depends(require_admin)])
async def stats():
    now = utcnow()
    async with SessionLocal() as db:
        async def count(*where) -> int:
            return (await db.execute(select(func.count()).select_from(Scan).where(*where))).scalar_one()

        totals = {
            "scans_total": await count(),
            "scans_today": await count(Scan.created_at >= now.replace(hour=0, minute=0, second=0, microsecond=0)),
            "scans_7d": await count(Scan.created_at >= now - timedelta(days=7)),
            "scans_30d": await count(Scan.created_at >= now - timedelta(days=30)),
            "failed_7d": await count(Scan.created_at >= now - timedelta(days=7), Scan.status == "failed"),
            "unique_sites": (await db.execute(select(func.count(func.distinct(Scan.host))))).scalar_one(),
            "unique_ips_30d": (await db.execute(select(func.count(func.distinct(Scan.client_ip)))
                                                .where(Scan.created_at >= now - timedelta(days=30)))).scalar_one(),
            "avg_score": (await db.execute(select(func.avg(Scan.score)).where(Scan.status == "done"))).scalar_one(),
            "feedback_new": (await db.execute(select(func.count()).select_from(Feedback)
                                              .where(Feedback.status == "new"))).scalar_one(),
            "rules_unverified": (await db.execute(select(func.count()).select_from(Rule)
                                                  .where(Rule.verified.is_(False)))).scalar_one(),
        }
        day = cast(Scan.created_at, Date)
        per_day = (await db.execute(
            select(day, func.count()).where(Scan.created_at >= now - timedelta(days=30)).group_by(day).order_by(day)
        )).all()
        grades = (await db.execute(text(
            "SELECT result->>'grade' g, count(*) FROM scans WHERE status='done' GROUP BY g"
        ))).all()
        # Частота нарушений по последним 1000 проверкам
        top = (await db.execute(text("""
            SELECT f->>'id' id, f->>'title' title,
                   count(*) FILTER (WHERE f->>'status'='fail') fails,
                   count(*) FILTER (WHERE f->>'status'='warn') warns
            FROM (SELECT result FROM scans WHERE status='done' ORDER BY created_at DESC LIMIT 1000) s,
                 jsonb_array_elements(s.result->'findings') f
            GROUP BY 1, 2 HAVING count(*) FILTER (WHERE f->>'status' IN ('fail','warn')) > 0
            ORDER BY fails DESC, warns DESC LIMIT 15
        """))).all()
        types = (await db.execute(text("""
            SELECT coalesce(result->'profile'->>'primary_type', '—') t, count(*) FROM scans
            WHERE status='done' GROUP BY 1 ORDER BY 2 DESC LIMIT 10
        """))).all()
        platforms = (await db.execute(text("""
            SELECT coalesce(result->'profile'->'platform'->>'name', 'Не определена') p, count(*) FROM scans
            WHERE status='done' GROUP BY 1 ORDER BY 2 DESC LIMIT 10
        """))).all()
        rkn_meta = await kv_get(db, rkn.KV_KEY)
    return {
        "totals": {**totals, "avg_score": round(totals["avg_score"]) if totals["avg_score"] is not None else None},
        "per_day": [{"date": d.isoformat(), "count": c} for d, c in per_day],
        "grades": {g or "—": c for g, c in grades},
        "top_violations": [{"id": i, "title": t, "fails": f, "warns": w} for i, t, f, w in top],
        "site_types": [{"name": t, "count": c} for t, c in types],
        "platforms": [{"name": p, "count": c} for p, c in platforms],
        "rkn": rkn_meta,
    }


# ---------- Проверки и сайты ----------

def _scan_row(s: Scan) -> dict:
    r = s.result or {}
    return {
        "id": s.id, "url": s.url, "host": s.host, "status": s.status, "score": s.score,
        "grade": r.get("grade"), "counts": r.get("counts"), "pages_scanned": s.pages_scanned,
        "type": (r.get("profile") or {}).get("primary_type"),
        "platform": ((r.get("profile") or {}).get("platform") or {}).get("name"),
        "client_ip": s.client_ip, "error": s.error,
        "created_at": s.created_at.isoformat(), "finished_at": s.finished_at.isoformat() if s.finished_at else None,
    }


@router.get("/scans", dependencies=[Depends(require_admin)])
async def scans(q: str = "", status: str = "", page: int = 1, per_page: int = 50):
    per_page = min(max(per_page, 1), 200)
    query = select(Scan)
    if q:
        query = query.where(or_(Scan.host.ilike(f"%{q}%"), Scan.url.ilike(f"%{q}%"), Scan.client_ip == q))
    if status:
        query = query.where(Scan.status == status)
    async with SessionLocal() as db:
        total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
        rows = (await db.execute(
            query.order_by(Scan.created_at.desc()).offset((max(page, 1) - 1) * per_page).limit(per_page)
        )).scalars().all()
    return {"total": total, "items": [_scan_row(s) for s in rows]}


@router.get("/scans/{scan_id}", dependencies=[Depends(require_admin)])
async def scan_detail(scan_id: str):
    async with SessionLocal() as db:
        scan = await db.get(Scan, scan_id)
        if not scan:
            raise HTTPException(404, "Проверка не найдена")
        fb = (await db.execute(select(Feedback).where(Feedback.scan_id == scan_id)
                               .order_by(Feedback.created_at.desc()))).scalars().all()
    return {**_scan_row(scan), "result": scan.result, "feedback": [_fb_row(f) for f in fb]}


@router.get("/scans/{scan_id}/shots/{name}", dependencies=[Depends(require_admin)])
async def scan_shot(scan_id: str, name: str):
    if not re.fullmatch(r"[0-9a-f-]{36}", scan_id) or not re.fullmatch(r"[0-9a-f]{16}\.jpg", name):
        raise HTTPException(404)
    path = shots_dir(scan_id) / name
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg")


@router.post("/scans/{scan_id}/rescan", dependencies=[Depends(require_admin)])
async def rescan(scan_id: str, request: Request):
    async with SessionLocal() as db:
        old = await db.get(Scan, scan_id)
        if not old:
            raise HTTPException(404)
        scan = Scan(url=old.url, host=old.host, client_ip="admin")
        db.add(scan)
        await db.commit()
    await request.app.state.redis.enqueue_job("run_scan", scan.id, _job_id=f"scan:{scan.id}")
    return {"id": scan.id}


@router.get("/sites", dependencies=[Depends(require_admin)])
async def sites(q: str = "", page: int = 1, per_page: int = 50):
    per_page = min(max(per_page, 1), 200)
    where = "WHERE host ILIKE :q" if q else ""
    async with SessionLocal() as db:
        rows = (await db.execute(text(f"""
            SELECT DISTINCT ON (host) host, id, score, status, created_at,
                   result->'profile'->>'primary_type' type,
                   result->'profile'->'platform'->>'name' platform,
                   result->'counts' counts,
                   count(*) OVER (PARTITION BY host) scans
            FROM scans {where}
            ORDER BY host, created_at DESC
        """), {"q": f"%{q}%"})).mappings().all()
    items = sorted(rows, key=lambda r: r["created_at"], reverse=True)
    start = (max(page, 1) - 1) * per_page
    return {
        "total": len(items),
        "items": [{**dict(r), "created_at": r["created_at"].isoformat()} for r in items[start:start + per_page]],
    }


# ---------- Реестр нарушений ----------

RULE_FIELDS = ("category", "title", "law", "koap", "fine_citizen", "fine_official", "fine_ip", "fine_legal",
               "fine_repeat", "confidence", "note", "verified")


def _rule_row(r: Rule) -> dict:
    return {"id": r.id, **{f: getattr(r, f) for f in RULE_FIELDS},
            "verified_at": r.verified_at.isoformat() if r.verified_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None}


@router.get("/rules", dependencies=[Depends(require_admin)])
async def rules():
    async with SessionLocal() as db:
        rows = (await db.execute(select(Rule).order_by(Rule.category, Rule.id))).scalars().all()
    return [_rule_row(r) for r in rows]


class RuleIn(BaseModel):
    category: str
    title: str
    law: str
    koap: str = ""
    fine_citizen: str = ""
    fine_official: str = ""
    fine_ip: str = ""
    fine_legal: str = ""
    fine_repeat: str = ""
    confidence: str = "low"
    note: str = ""
    verified: bool = False


@router.put("/rules/{rule_id}", dependencies=[Depends(require_admin)])
async def update_rule(rule_id: str, body: RuleIn):
    async with SessionLocal() as db:
        rule = await db.get(Rule, rule_id)
        if not rule:
            raise HTTPException(404)
        was_verified = rule.verified
        for f in RULE_FIELDS:
            setattr(rule, f, getattr(body, f))
        if body.verified and not was_verified:
            rule.verified_at = utcnow()
        if not body.verified:
            rule.verified_at = None
        await db.commit()
        await db.refresh(rule)
    invalidate_rules_cache()
    return _rule_row(rule)


@router.get("/rules.csv", dependencies=[Depends(require_admin)])
async def rules_csv():
    data = await rules()
    buf = io.StringIO()
    cols = ["id", *RULE_FIELDS, "verified_at"]
    w = csv.DictWriter(buf, fieldnames=cols, delimiter=";", extrasaction="ignore")
    w.writeheader()
    w.writerows(data)
    # BOM — чтобы Excel открыл кириллицу без танцев
    return StreamingResponse(iter(["﻿" + buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename=registry.csv"})


_rules_cache: tuple[float, dict] = (0, {})


def invalidate_rules_cache() -> None:
    global _rules_cache
    _rules_cache = (0, {})


async def rules_map() -> dict[str, dict]:
    """Актуальный реестр для отображения штрафов в отчётах (кэш на минуту)."""
    global _rules_cache
    if time.monotonic() - _rules_cache[0] < 60:
        return _rules_cache[1]
    async with SessionLocal() as db:
        rows = (await db.execute(select(Rule))).scalars().all()
    _rules_cache = (time.monotonic(), {r.id: _rule_row(r) for r in rows})
    return _rules_cache[1]


# ---------- Обратная связь ----------

def _fb_row(f: Feedback) -> dict:
    return {"id": f.id, "scan_id": f.scan_id, "finding_id": f.finding_id, "kind": f.kind, "comment": f.comment,
            "contact": f.contact, "status": f.status, "admin_note": f.admin_note, "client_ip": f.client_ip,
            "created_at": f.created_at.isoformat()}


@router.get("/feedback", dependencies=[Depends(require_admin)])
async def feedback(status: str = "", page: int = 1, per_page: int = 50):
    query = select(Feedback, Scan.host).join(Scan, Scan.id == Feedback.scan_id, isouter=True)
    if status:
        query = query.where(Feedback.status == status)
    async with SessionLocal() as db:
        total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
        rows = (await db.execute(query.order_by(Feedback.created_at.desc())
                                 .offset((max(page, 1) - 1) * per_page).limit(per_page))).all()
    return {"total": total, "items": [{**_fb_row(f), "host": h} for f, h in rows]}


class FeedbackUpdate(BaseModel):
    status: str
    admin_note: str = ""


@router.patch("/feedback/{fb_id}", dependencies=[Depends(require_admin)])
async def update_feedback(fb_id: int, body: FeedbackUpdate):
    if body.status not in ("new", "accepted", "rejected", "fixed"):
        raise HTTPException(422, "Некорректный статус")
    async with SessionLocal() as db:
        fb = await db.get(Feedback, fb_id)
        if not fb:
            raise HTTPException(404)
        fb.status, fb.admin_note = body.status, body.admin_note
        await db.commit()
        return _fb_row(fb)


# ---------- Реестр РКН ----------

@router.get("/rkn", dependencies=[Depends(require_admin)])
async def rkn_status():
    async with SessionLocal() as db:
        return await kv_get(db, rkn.KV_KEY)


@router.post("/rkn/import", dependencies=[Depends(require_admin)])
async def rkn_import(request: Request):
    job = await request.app.state.redis.enqueue_job("import_rkn", _job_id=f"import_rkn:{datetime.now():%Y%m%d%H%M}")
    return {"queued": job is not None}


@router.get("/rkn/search", dependencies=[Depends(require_admin)])
async def rkn_search(inn: str):
    if not re.fullmatch(r"\d{10}|\d{12}", inn):
        raise HTTPException(422, "ИНН должен содержать 10 или 12 цифр")
    return await rkn.lookup([inn])
