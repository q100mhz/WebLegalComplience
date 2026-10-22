import asyncio
import logging
import shutil
import time
from pathlib import Path

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import update

from . import rkn
from .config import settings, shots_dir
from .db import Scan, SessionLocal, kv_get, utcnow
from .scanner.checks.pdn import find_inns
from .scanner.crawler import BlockedError, crawl
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
        site = await asyncio.wait_for(crawl(scan.url, progress, shots_dir(scan_id)), timeout=settings.scan_timeout_s)
        site.profile["rkn"] = await rkn.lookup(find_inns(site))
        report = build_report(site)
        await _update(
            scan_id, status="done", result=report, score=report["score"],
            pages_scanned=len(site.pages), current_url=None, finished_at=utcnow(),
        )
        log.info("scan %s %s done: score %s, %s pages", scan_id, scan.url, report["score"], len(site.pages))
    except Exception as e:  # noqa: BLE001
        log.exception("scan %s failed", scan_id)
        msg = "Превышено время сканирования" if isinstance(e, TimeoutError) else str(e)[:500]
        if "ERR_HTTP2" in msg or "ERR_CERT" in msg or "ERR_SSL" in msg:
            msg = "Не удалось установить защищённое соединение с сайтом"
        if "net::ERR_NAME_NOT_RESOLVED" in msg or "ERR_CONNECTION" in msg:
            msg = "Сайт недоступен"
        if msg.startswith("http") and ": Page.goto" in msg:
            msg = "Главная страница сайта не загрузилась за отведённое время"
        # Подробности для админки (в публичный отчёт не попадают)
        diag = e.diag if isinstance(e, BlockedError) else {"exception": f"{type(e).__name__}: {str(e)[:1000]}"}
        await _update(scan_id, status="failed", error=msg, finished_at=utcnow(), result={"diagnostics": diag})


async def cleanup_shots(ctx) -> None:
    root = Path(settings.data_dir) / "shots"
    if not root.exists():
        return
    limit = time.time() - settings.screenshots_keep_days * 86400
    for d in root.iterdir():
        if d.is_dir() and d.stat().st_mtime < limit:
            shutil.rmtree(d, ignore_errors=True)


async def startup(ctx) -> None:
    # Первый запуск: реестр РКН ещё не загружен — ставим импорт в очередь
    try:
        async with SessionLocal() as db:
            meta = await kv_get(db, rkn.KV_KEY)
    except Exception:  # noqa: BLE001 — таблицы ещё создаёт API; импорт запустится по расписанию
        log.warning("database not ready, skipping initial RKN import")
        return
    if not meta.get("rows") and meta.get("status") != "running":
        await ctx["redis"].enqueue_job("import_rkn", _job_id="import_rkn")


async def import_rkn(ctx) -> dict:
    return await rkn.import_registry(ctx)


class WorkerSettings:
    functions = [run_scan, import_rkn]
    cron_jobs = [
        cron(import_rkn, hour=3, minute=30, timeout=3600, unique=True),
        cron(cleanup_shots, hour=4, minute=30),
    ]
    on_startup = startup
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = settings.worker_concurrency + 1
    job_timeout = 3600
    max_tries = 1
