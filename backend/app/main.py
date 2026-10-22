import copy
from contextlib import asynccontextmanager
from datetime import timedelta
from urllib.parse import urlsplit

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from .admin import router as admin_router
from .admin import rules_map
from .config import settings
from .db import Feedback, Scan, SessionLocal, init_db, utcnow
from .registry import format_penalty
from .scanner.urls import UrlError, normalize_input, resolve_public, site_key


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    app.state.redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    yield
    await app.state.redis.close()


app = FastAPI(title="WebLegalCompliance API", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(admin_router)


class ScanIn(BaseModel):
    url: str


def client_ip(request: Request) -> str:
    # API слушает только localhost, перед ним nginx — доверяем X-Real-IP
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")


async def check_rate_limit(request: Request, ip: str, scope: str = "rl", limit: int | None = None) -> None:
    redis = request.app.state.redis
    key = f"{scope}:{ip}"
    n = await redis.incr(key)
    if n == 1:
        await redis.expire(key, 3600)
    if n > (limit or settings.rate_limit_per_hour):
        raise HTTPException(429, "Слишком много запросов. Попробуйте через час.")


def public_view(scan: Scan, rules: dict[str, dict] | None = None) -> dict:
    """Открытая версия отчёта. Полные доказательства — в будущем личном кабинете."""
    data = {
        "id": scan.id,
        "url": scan.url,
        "host": scan.host,
        "status": scan.status,
        "pages_scanned": scan.pages_scanned,
        "max_pages": settings.max_pages,
        "current_url": scan.current_url,
        "created_at": scan.created_at.isoformat(),
        "finished_at": scan.finished_at.isoformat() if scan.finished_at else None,
        "error": scan.error,
        "report": None,
    }
    if scan.result and scan.status == "done":
        r = copy.deepcopy(scan.result)
        rules = rules or {}
        r["findings"] = [
            {
                **f,
                "evidence": f["evidence"][:3],
                "evidence_total": len(f["evidence"]),
                # штрафы всегда по актуальному реестру из админки
                "penalty": format_penalty(rules[f["id"]]) if f["id"] in rules and f["status"] in ("fail", "warn")
                else f.get("penalty", ""),
            }
            for f in r["findings"]
        ]
        for k in ("errors", "timing", "stats"):
            r.pop(k, None)
        for p in r.get("pages", []):
            p.pop("screenshot", None)
        for f in (r.get("profile") or {}).get("forms", []):
            f.pop("screenshot", None)
        data["report"] = r
    return data


@app.get("/api/health")
async def health():
    return {"ok": True}


@app.post("/api/scans", status_code=201)
async def create_scan(body: ScanIn, request: Request):
    try:
        url = normalize_input(body.url)
    except UrlError as e:
        raise HTTPException(422, str(e)) from e
    host = urlsplit(url).hostname or ""
    ok, _ = await resolve_public(host)
    if not ok:
        raise HTTPException(422, "Не удалось найти сайт по этому адресу")

    async with SessionLocal() as db:
        # Повторный запрос того же сайта за последние N минут — отдаём существующую проверку
        since = utcnow() - timedelta(minutes=settings.dedup_minutes)
        existing = (
            await db.execute(
                select(Scan)
                .where(Scan.host == site_key(host), Scan.created_at >= since, Scan.status != "failed")
                .order_by(Scan.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing:
            return {"id": existing.id, "cached": True}

        ip = client_ip(request)
        await check_rate_limit(request, ip)
        scan = Scan(url=url, host=site_key(host), client_ip=ip)
        db.add(scan)
        await db.commit()

    await request.app.state.redis.enqueue_job("run_scan", scan.id, _job_id=f"scan:{scan.id}")
    return {"id": scan.id, "cached": False}


@app.get("/api/scans/{scan_id}")
async def get_scan(scan_id: str):
    async with SessionLocal() as db:
        scan = await db.get(Scan, scan_id)
    if not scan:
        raise HTTPException(404, "Проверка не найдена")
    return public_view(scan, await rules_map())


class FeedbackIn(BaseModel):
    finding_id: str = Field(max_length=64)
    kind: str = Field(pattern="^(false_positive|rkn_claim|other)$")
    comment: str = Field(default="", max_length=2000)
    contact: str = Field(default="", max_length=255)
    consent: bool = False


@app.post("/api/scans/{scan_id}/feedback", status_code=201)
async def create_feedback(scan_id: str, body: FeedbackIn, request: Request):
    if body.contact.strip() and not body.consent:
        raise HTTPException(422, "Чтобы оставить контакт, нужно согласие на обработку персональных данных")
    if body.kind == "rkn_claim" and not body.comment.strip():
        raise HTTPException(422, "Укажите номер уведомления или регистрационный номер в реестре")
    ip = client_ip(request)
    await check_rate_limit(request, ip, scope="fb", limit=30)
    async with SessionLocal() as db:
        if not await db.get(Scan, scan_id):
            raise HTTPException(404, "Проверка не найдена")
        db.add(Feedback(scan_id=scan_id, finding_id=body.finding_id, kind=body.kind, comment=body.comment.strip(),
                        contact=body.contact.strip(), client_ip=ip))
        await db.commit()
    return {"ok": True}

