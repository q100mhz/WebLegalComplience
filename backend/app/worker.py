import asyncio
import logging
import time

from arq.connections import RedisSettings
from sqlalchemy import update

from .config import settings
from .db import Scan, SessionLocal, utcnow
from .scanner.crawler import crawl
from .scanner.report import build_report

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("worker")


async def _update(scan_id: str, **values) -> None:
    async with SessionLocal() as db:
        await db.execute(update(Scan).where(Scan.id == scan_id).values(**values))
        await db.commit()


async def run_scan(ctx, scan_id: str) -> None:
    async with SessionLocal() as db:
        scan = await db.get(Scan, scan_id)
    if not scan:
        return
    await _update(scan_id, status="running")
    last = 0.0

    async def progress(n: int, url: str) -> None:
        nonlocal last
        if time.monotonic() - last > 1 or n == 1:
            last = time.monotonic()
            await _update(scan_id, pages_scanned=n, current_url=url[:2000])

    try:
        site = await asyncio.wait_for(crawl(scan.url, progress), timeout=settings.scan_timeout_s)
        report = build_report(site)
        await _update(
            scan_id, status="done", result=report, score=report["score"],
            pages_scanned=len(site.pages), current_url=None, finished_at=utcnow(),
        )
        log.info("scan %s %s done: score %s, %s pages", scan_id, scan.url, report["score"], len(site.pages))
    except Exception as e:  # noqa: BLE001
        log.exception("scan %s failed", scan_id)
        msg = "Превышено время сканирования" if isinstance(e, TimeoutError) else str(e)[:500]
        if "net::ERR_NAME_NOT_RESOLVED" in msg or "ERR_CONNECTION" in msg:
            msg = "Сайт недоступен"
        await _update(scan_id, status="failed", error=msg, finished_at=utcnow())


class WorkerSettings:
    functions = [run_scan]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = settings.worker_concurrency
    job_timeout = settings.scan_timeout_s + 60
    max_tries = 1
