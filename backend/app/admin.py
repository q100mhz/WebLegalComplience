"""API админки: дашборд, проверки, сайты, реестр нарушений, обратная связь, реестр РКН."""

import csv
import hashlib
import hmac
import io
import json
import re
import time
import zipfile
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import Date, cast, func, or_, select, text, update

from . import batches, rkn, runtime
from .config import settings, shots_dir
from .documents import documents_for, to_docx
from .documents.howto import howto
from .zipstream import stream_zip
from .db import Batch, Feedback, RknCheck, Rule, Scan, SessionLocal, kv_get, utcnow
from .scanner.urls import UrlError, normalize_input, site_key

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


# ---------- Нагрузка и одновременные проверки ----------

class RuntimeIn(BaseModel):
    scan_concurrency: int | None = None
    max_pages: int | None = None


def _runtime_view(cfg: dict) -> dict:
    return {**cfg, **runtime.server_resources(), "max": settings.max_concurrency,
            "pages_min": settings.min_pages_limit, "pages_max": settings.max_pages_limit,
            "pages_default": settings.max_pages}


@router.get("/settings", dependencies=[Depends(require_admin)])
async def get_runtime_settings():
    return _runtime_view(await runtime.get_settings())


@router.put("/settings", dependencies=[Depends(require_admin)])
async def put_runtime_settings(body: RuntimeIn):
    if body.scan_concurrency is not None and not 1 <= body.scan_concurrency <= settings.max_concurrency:
        raise HTTPException(422, f"Одновременных проверок — от 1 до {settings.max_concurrency}")
    if body.max_pages is not None and not settings.min_pages_limit <= body.max_pages <= settings.max_pages_limit:
        raise HTTPException(422, f"Глубина проверки — от {settings.min_pages_limit} до {settings.max_pages_limit} страниц")
    return _runtime_view(await runtime.update_settings(body.scan_concurrency, body.max_pages))


LOAD_RANGES = {6: (300, 30), 24: (900, 60), 168: (7200, 600)}  # часы → (размер столбца, шаг выборки), сек


@router.get("/load", dependencies=[Depends(require_admin)])
async def load(hours: int = 24):
    """Нагрузка в динамике: сколько проверок выполнялось одновременно и сколько посетителей ждали старта.
    Считается по времени создания, старта и завершения каждой проверки."""
    hours = min(LOAD_RANGES, key=lambda h: abs(h - hours))
    bucket_s, step_s = LOAD_RANGES[hours]
    now = utcnow()
    since = now - timedelta(hours=hours)
    async with SessionLocal() as db:
        rows = (await db.execute(
            select(Scan.created_at, Scan.started_at, Scan.finished_at, Scan.status, Scan.batch_id)
            .where(or_(Scan.created_at >= since - timedelta(hours=2), Scan.status.in_(("queued", "running"))))
        )).all()
    scans = [runtime.LoadScan(c, st, f, status, b is not None) for c, st, f, status, b in rows]
    out = runtime.load_series(scans, now, hours * 3600, bucket_s, step_s)
    async with SessionLocal() as db:
        running_now = (await db.execute(select(func.count()).select_from(Scan).where(Scan.status == "running"))).scalar_one()
        waiting_now = (await db.execute(select(func.count()).select_from(Scan).where(
            Scan.status == "queued", Scan.batch_id.is_(None)))).scalar_one()
        pending_batch = (await db.execute(select(func.count()).select_from(Scan).where(Scan.status == "pending"))).scalar_one()
    limit = (await runtime.get_settings())["scan_concurrency"]
    peaks = sorted((x for x in out if x["max_waiting"] or x["max_running"] > 1),
                   key=lambda x: (-x["max_waiting"], -(x["max_wait_s"] or 0)))[:5]
    return {
        "hours": hours, "bucket_s": bucket_s, "series": out, "peaks": peaks,
        "now": {"running": running_now, "waiting": waiting_now, "pending_batch": pending_batch, "limit": limit,
                "avg_duration_s": round(await runtime.avg_duration())},
    }


# ---------- Проверки и сайты ----------

def _scan_row(s: Scan) -> dict:
    r = s.result or {}
    return {
        "id": s.id, "url": s.url, "host": s.host, "status": s.status, "score": s.score,
        "grade": r.get("grade"), "counts": r.get("counts"), "pages_scanned": s.pages_scanned,
        "type": (r.get("profile") or {}).get("primary_type"),
        "platform": ((r.get("profile") or {}).get("platform") or {}).get("name"),
        "client_ip": s.client_ip, "error": s.error, "batch_id": s.batch_id,
        "created_at": s.created_at.isoformat(), "finished_at": s.finished_at.isoformat() if s.finished_at else None,
    }


@router.get("/scans", dependencies=[Depends(require_admin)])
async def scans(q: str = "", status: str = "", page: int = 1, per_page: int = 50, batch: str = ""):
    per_page = min(max(per_page, 1), 200)
    query = select(Scan)
    if batch:
        query = query.where(Scan.batch_id == batch)
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
    result = scan.result
    if result and result.get("findings"):
        platform = ((result.get("profile") or {}).get("platform") or {}).get("name")
        result = {**result, "findings": [{**f, "howto": howto(f["id"], platform) if f["status"] in ("fail", "warn")
                                          else None} for f in result["findings"]]}
    return {**_scan_row(scan), "result": result, "feedback": [_fb_row(f) for f in fb]}


@router.get("/scans/{scan_id}/shots/{name}", dependencies=[Depends(require_admin)])
async def scan_shot(scan_id: str, name: str):
    if not re.fullmatch(r"[0-9a-f-]{36}", scan_id) or not re.fullmatch(r"[0-9a-f]{16}\.jpg", name):
        raise HTTPException(404)
    path = shots_dir(scan_id) / name
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg")


# ---------- Пакетные проверки ----------

MAX_BATCH = 500


class BatchIn(BaseModel):
    name: str = ""
    urls: str


@router.post("/batches", dependencies=[Depends(require_admin)])
async def create_batch(body: BatchIn, request: Request):
    raw = [u for u in re.split(r"[\s,;]+", body.urls) if u.strip()]
    valid: dict[str, str] = {}
    invalid: list[str] = []
    for u in raw:
        try:
            url = normalize_input(u)
        except UrlError:
            invalid.append(u[:200])
            continue
        valid.setdefault(site_key(urlsplit(url).hostname or ""), url)  # один сайт — одна проверка
    if not valid:
        raise HTTPException(422, "В списке нет ни одного корректного адреса сайта")
    if len(valid) > MAX_BATCH:
        raise HTTPException(422, f"Не больше {MAX_BATCH} сайтов в одном пакете (в списке {len(valid)})")
    async with SessionLocal() as db:
        batch = Batch(name=body.name.strip()[:255] or f"Пакет от {utcnow():%d.%m.%Y %H:%M}", total=len(valid))
        db.add(batch)
        await db.flush()
        base = utcnow()
        for i, (host, url) in enumerate(valid.items()):
            # порядок подачи — порядок в списке
            db.add(Scan(url=url, host=host, status="pending", client_ip="admin-batch", batch_id=batch.id,
                        created_at=base + timedelta(microseconds=i)))
        await db.commit()
    await batches.pump(request.app.state.redis)
    return {"id": batch.id, "total": len(valid), "invalid": invalid, "duplicates": len(raw) - len(valid) - len(invalid)}


async def _batch_rows(db, ids: list[str]) -> dict[str, dict]:
    duration = func.avg(func.extract("epoch", Scan.finished_at - Scan.started_at))
    rows = (await db.execute(
        select(Scan.batch_id, Scan.status, func.count(), func.avg(Scan.score), duration)
        .where(Scan.batch_id.in_(ids)).group_by(Scan.batch_id, Scan.status)
    )).all()
    out: dict[str, dict] = {i: {"counts": {}, "avg_score": None, "avg_duration": None} for i in ids}
    for bid, st, n, avg, dur in rows:
        o = out[bid]
        o["counts"][st] = n
        if st == "done":
            o["avg_score"] = round(avg) if avg is not None else None
            o["avg_duration"] = float(dur) if dur is not None else None
    return out


def _batch_view(b: Batch, agg: dict) -> dict:
    c = agg["counts"]
    finished = c.get("done", 0) + c.get("failed", 0) + c.get("cancelled", 0)
    left = c.get("pending", 0) + c.get("queued", 0) + c.get("running", 0)
    eta = round(agg["avg_duration"] * left / 60) if left and agg["avg_duration"] else None
    state = "done" if not left else "paused" if b.paused else "running"
    return {"id": b.id, "name": b.name, "total": b.total, "created_at": b.created_at.isoformat(), "counts": c,
            "finished": finished, "left": left, "avg_score": agg["avg_score"], "eta_minutes": eta, "state": state}


@router.get("/batches", dependencies=[Depends(require_admin)])
async def list_batches():
    async with SessionLocal() as db:
        items = (await db.execute(select(Batch).order_by(Batch.created_at.desc()).limit(100))).scalars().all()
        agg = await _batch_rows(db, [b.id for b in items])
    return [_batch_view(b, agg[b.id]) for b in items]


@router.get("/batches/{batch_id}", dependencies=[Depends(require_admin)])
async def batch_detail(batch_id: str):
    async with SessionLocal() as db:
        b = await db.get(Batch, batch_id)
        if not b:
            raise HTTPException(404, "Пакет не найден")
        agg = await _batch_rows(db, [b.id])
        scans = (await db.execute(select(Scan).where(Scan.batch_id == b.id).order_by(Scan.created_at, Scan.id))).scalars().all()
    return {**_batch_view(b, agg[b.id]), "scans": [_scan_row(s) for s in scans],
            # список в том же виде, в каком его вставляют при создании пакета
            "urls_text": "\n".join(s.url for s in scans)}


async def _get_batch(db, batch_id: str) -> Batch:
    b = await db.get(Batch, batch_id)
    if not b:
        raise HTTPException(404, "Пакет не найден")
    return b


@router.post("/batches/{batch_id}/pause", dependencies=[Depends(require_admin)])
async def pause_batch(batch_id: str):
    """Пауза: текущий сайт доработает, следующие не начнутся."""
    async with SessionLocal() as db:
        b = await _get_batch(db, batch_id)
        b.paused = True
        # поставленный в очередь, но ещё не начатый сайт возвращаем в ожидание (его задание будет пропущено)
        await db.execute(update(Scan).where(Scan.batch_id == batch_id, Scan.status == "queued").values(status="pending"))
        await db.commit()
    return {"ok": True}


@router.post("/batches/{batch_id}/resume", dependencies=[Depends(require_admin)])
async def resume_batch(batch_id: str, request: Request):
    async with SessionLocal() as db:
        b = await _get_batch(db, batch_id)
        b.paused = False
        await db.commit()
    await batches.pump(request.app.state.redis)
    return {"ok": True}


@router.post("/batches/{batch_id}/retry-failed", dependencies=[Depends(require_admin)])
async def retry_failed(batch_id: str, request: Request):
    """Повторить сайты, которые не удалось проверить или которые были отменены."""
    async with SessionLocal() as db:
        b = await _get_batch(db, batch_id)
        res = await db.execute(update(Scan).where(Scan.batch_id == batch_id, Scan.status.in_(("failed", "cancelled")))
                               .values(status="pending", error=None, result=None, score=None, pages_scanned=0,
                                       started_at=None, finished_at=None, current_url=None))
        b.paused = False
        await db.commit()
    await batches.pump(request.app.state.redis)
    return {"requeued": res.rowcount}


@router.post("/batches/{batch_id}/restart", dependencies=[Depends(require_admin)])
async def restart_batch(batch_id: str, request: Request):
    """Начать заново: новый пакет с тем же списком. Прежний остаётся для сравнения «было/стало»;
    его незавершённые сайты отменяются, чтобы не проверять их дважды."""
    async with SessionLocal() as db:
        b = await _get_batch(db, batch_id)
        urls = (await db.execute(select(Scan.url).where(Scan.batch_id == batch_id)
                                 .order_by(Scan.created_at, Scan.id))).scalars().all()
        await db.execute(update(Scan).where(Scan.batch_id == batch_id, Scan.status.in_(("pending", "queued")))
                         .values(status="cancelled", error="Пакет запущен заново", finished_at=utcnow()))
        name = re.sub(r"\s+\(повтор.*\)$", "", b.name)
        await db.commit()
    return await create_batch(BatchIn(name=f"{name} (повтор от {utcnow():%d.%m %H:%M})", urls="\n".join(urls)), request)


def _write_docs(zf: zipfile.ZipFile, scan: Scan, folder: str) -> int:
    if scan.status != "done" or not (scan.result or {}).get("findings"):
        return 0
    try:
        docs = documents_for(scan.result, scan.url, (scan.finished_at or scan.created_at).isoformat())
    except Exception:  # noqa: BLE001 — документ одного сайта не должен ломать архив
        return 0
    for doc in docs:
        zf.writestr(f"{folder}/{doc.filename}", to_docx(doc))
    return len(docs)


@router.get("/batches/{batch_id}/documents.zip", dependencies=[Depends(require_admin)])
async def batch_documents(batch_id: str):
    """Документы для всех проверенных сайтов пакета: папка на сайт — удобно отправлять клиентам."""
    async with SessionLocal() as db:
        b = await _get_batch(db, batch_id)
        scans = (await db.execute(select(Scan).where(Scan.batch_id == batch_id, Scan.status == "done")
                                  .order_by(Scan.created_at))).scalars().all()
    name = b.name

    def build(zf: zipfile.ZipFile) -> None:
        n = sum(_write_docs(zf, s, s.host) for s in scans)
        zf.writestr("README.txt", f"Документы для сайтов пакета «{name}»: {len(scans)} сайтов, {n} документов.\n"
                                  "Черновики сформированы по результатам проверки. Поля в квадратных скобках заполните, "
                                  "тексты согласуйте с юристом.\n")

    return StreamingResponse(stream_zip(build), media_type="application/zip",
                             headers={"Content-Disposition": f"attachment; filename=documents-{batch_id[:8]}.zip"})


@router.post("/batches/{batch_id}/cancel", dependencies=[Depends(require_admin)])
async def cancel_batch(batch_id: str):
    async with SessionLocal() as db:
        res = await db.execute(update(Scan).where(Scan.batch_id == batch_id, Scan.status == "pending")
                               .values(status="cancelled", error="Отменено", finished_at=utcnow()))
        await db.commit()
    return {"cancelled": res.rowcount}


@router.get("/export.zip", dependencies=[Depends(require_admin)])
async def export_zip(days: int = 30, status: str = "", limit: int = 500, batch: str = ""):
    """История проверок для аудита правил: scans.json (полные результаты, без IP и контактов пользователей),
    rules.json (реестр), скриншоты и снимки собранных данных (site.json.gz) каждой проверки."""
    since = utcnow() - timedelta(days=max(1, min(days, 365)))
    query = select(Scan).order_by(Scan.created_at.desc())
    if status:
        query = query.where(Scan.status == status)
    # пакет выгружается целиком, без ограничения по дате; иначе — история за период
    query = query.where(Scan.batch_id == batch) if batch else query.where(Scan.created_at >= since).limit(max(1, min(limit, 2000)))
    async with SessionLocal() as db:
        scans = (await db.execute(query)).scalars().all()
        fb = (await db.execute(select(Feedback).where(Feedback.scan_id.in_([s.id for s in scans])))).scalars().all()
        rules_rows = (await db.execute(select(Rule))).scalars().all()

    feedback_by_scan: dict[str, list] = {}
    for f in fb:  # персональные данные пользователей (контакт, IP) в выгрузку не попадают
        feedback_by_scan.setdefault(f.scan_id, []).append(
            {"finding_id": f.finding_id, "kind": f.kind, "comment": f.comment, "status": f.status,
             "admin_note": f.admin_note, "created_at": f.created_at.isoformat()})
    items = []
    for s in scans:
        row = _scan_row(s)
        row.pop("client_ip", None)
        d = shots_dir(s.id)
        items.append({**row, "result": s.result, "feedback": feedback_by_scan.get(s.id, []),
                      "has_snapshot": (d / "site.json.gz").is_file()})

    rules_json = json.dumps([_rule_row(r) for r in rules_rows], ensure_ascii=False, indent=1)

    def build(zf: zipfile.ZipFile) -> None:
        zf.writestr("scans.json", json.dumps(items, ensure_ascii=False, indent=1))
        zf.writestr("rules.json", rules_json)
        zf.writestr("README.txt", f"WebLegalCompliance — выгрузка {utcnow():%Y-%m-%d %H:%M} UTC, проверок: {len(items)}, "
                                  f"за {days} дн.\nscans.json — результаты; files/<id>/ — скриншоты, site.json.gz "
                                  "(снимок данных, по которым работали правила) и documents/ (документы под сайт).\n")
        for s in scans:
            _write_docs(zf, s, f"files/{s.id}/documents")
            d = shots_dir(s.id)
            if d.is_dir():
                for f in d.iterdir():
                    if f.is_file():
                        zf.write(f, f"files/{s.id}/{f.name}", compress_type=zipfile.ZIP_STORED
                                 if f.suffix in (".jpg", ".gz") else zipfile.ZIP_DEFLATED)

    name = f"wlc-history-{utcnow():%Y%m%d-%H%M}.zip"
    return StreamingResponse(stream_zip(build), media_type="application/zip",
                             headers={"Content-Disposition": f"attachment; filename={name}"})


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
        meta = await kv_get(db, rkn.KV_KEY)
        total = (await db.execute(select(func.count()).select_from(RknCheck))).scalar_one()
        found = (await db.execute(select(func.count()).select_from(RknCheck).where(RknCheck.found.is_(True)))).scalar_one()
        recent = (await db.execute(select(RknCheck).order_by(RknCheck.checked_at.desc()).limit(20))).scalars().all()
    return {
        **meta, "cached": total, "cached_found": found,
        "recent": [{"inn": r.inn, "found": r.found, "records": r.records, "checked_at": r.checked_at.isoformat()}
                   for r in recent],
    }


@router.get("/rkn/search", dependencies=[Depends(require_admin)])
async def rkn_search(inn: str):
    if not re.fullmatch(r"\d{10}|\d{12}", inn):
        raise HTTPException(422, "ИНН должен содержать 10 или 12 цифр")
    return await rkn.check_inn(inn, use_cache=False)
