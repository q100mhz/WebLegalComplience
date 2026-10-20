from contextlib import asynccontextmanager
from datetime import timedelta
from urllib.parse import urlsplit

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from .config import settings
from .db import Scan, SessionLocal, init_db, utcnow
from .scanner.urls import UrlError, normalize_input, resolve_public, site_key


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    app.state.redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    yield
    await app.state.redis.close()


app = FastAPI(title="WeLegalCompliance API", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")


class ScanIn(BaseModel):
    url: str


def client_ip(request: Request) -> str:
    # API слушает только localhost, перед ним nginx — доверяем X-Real-IP
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")


async def check_rate_limit(request: Request, ip: str) -> None:
    redis = request.app.state.redis
    key = f"rl:{ip}"
    n = await redis.incr(key)
    if n == 1:
        await redis.expire(key, 3600)
    if n > settings.rate_limit_per_hour:
        raise HTTPException(429, "Слишком много проверок. Попробуйте через час.")


def public_view(scan: Scan) -> dict:
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
    if scan.result:
        r = dict(scan.result)
        r["findings"] = [
            {**f, "evidence": f["evidence"][:3], "evidence_total": len(f["evidence"])} for f in r["findings"]
        ]
        r.pop("errors", None)
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
    return public_view(scan)

