import asyncio
import copy
import logging
import shutil
import time
from pathlib import Path

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import update

from . import batches, rkn, runtime
from .config import scan_timeout, settings, shots_dir
from .db import Scan, SessionLocal, utcnow
from .scanner import snapshot
from .scanner.checks.pdn import find_inns
from .scanner.crawler import BlockedError, crawl
from .scanner.report import build_report, partial_view

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("worker")


async def _update(scan_id: str, **values) -> None:
    async with SessionLocal() as db:
        await db.execute(update(Scan).where(Scan.id == scan_id).values(**values))
        await db.commit()


class Gate:
    """Сколько проверок (браузеров) работает одновременно. Лимит меняется в админке на лету:
    arq берёт задания с запасом (max_jobs), а запускаются они здесь — по очереди поступления."""

    def __init__(self, limit: int):
        self.limit = limit
        self.running = 0
        self._cond = asyncio.Condition()

    async def acquire(self) -> None:
        async with self._cond:
            await self._cond.wait_for(lambda: self.running < self.limit)
            self.running += 1

    async def release(self) -> None:
        async with self._cond:
            self.running -= 1
            self._cond.notify_all()

    async def set_limit(self, limit: int) -> None:
        async with self._cond:
            if limit != self.limit:
                log.info("scan concurrency: %s -> %s", self.limit, limit)
            self.limit = limit
            self._cond.notify_all()


gate = Gate(settings.worker_concurrency)


async def _watch_limit() -> None:
    while True:
        try:
            await gate.set_limit((await runtime.get_settings())["scan_concurrency"])
        except Exception:  # noqa: BLE001 — база может быть недоступна какое-то время
            log.warning("cannot read runtime settings", exc_info=True)
        await asyncio.sleep(5)


async def run_scan(ctx, scan_id: str) -> None:
    async with SessionLocal() as db:
        scan = await db.get(Scan, scan_id)
    if not scan or scan.status != "queued":  # отменена или уже обработана (старое задание после перезапуска)
        return
    await gate.acquire()
    try:
        if await _claim(scan):
            await _scan(ctx, scan)
    finally:
        await gate.release()
        if scan.batch_id:
            await batches.pump(ctx["redis"])


async def _claim(scan: Scan) -> bool:
    """queued → running атомарно: одну проверку выполняет только одно задание.
    Глубина фиксируется в проверке — смена настройки в админке не влияет на уже идущие."""
    max_pages = (await runtime.get_settings())["max_pages"]
    async with SessionLocal() as db:
        res = await db.execute(update(Scan).where(Scan.id == scan.id, Scan.status == "queued")
                               .values(status="running", started_at=utcnow(), max_pages=max_pages))
        await db.commit()
    scan.max_pages = max_pages
    return res.rowcount == 1


async def _scan(ctx, scan: Scan) -> None:
    scan_id = scan.id
    last = 0.0

    async def progress(n: int, url: str) -> None:
        nonlocal last
        if time.monotonic() - last > 1 or n == 1:
            last = time.monotonic()
            await _update(scan_id, pages_scanned=n, current_url=url[:2000])

    async def on_quick(site) -> None:
        """Предварительный отчёт по главной и первым юридически значимым страницам — посетитель
        видит основные нарушения через ~25 секунд, пока проверка идёт дальше."""
        try:
            part = copy.copy(site)
            part.pages = list(site.pages)
            part.profile = {"rkn": await rkn.lookup(find_inns(site))}
            report = partial_view(build_report(part))
            await _update(scan_id, result=report)
        except Exception:  # noqa: BLE001 — предварительный отчёт не обязателен
            log.exception("quick report failed for %s", scan_id)

    try:
        site = await asyncio.wait_for(crawl(scan.url, progress, shots_dir(scan_id), on_quick, scan.max_pages),
                                      timeout=scan_timeout(scan.max_pages))
        site.profile["rkn"] = await rkn.lookup(find_inns(site))
        try:  # снимок до построения отчёта: в профиле только данные РКН, остальное пересчитывается
            snapshot.save(site, shots_dir(scan_id))
        except Exception:  # noqa: BLE001 — снимок нужен для аудита, проверку из-за него не роняем
            log.exception("snapshot save failed for %s", scan_id)
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
        await _update(scan_id, status="failed", error=msg, finished_at=utcnow(), result={"diagnostics": diag},
                      score=None)


async def cleanup_shots(ctx) -> None:
    root = Path(settings.data_dir) / "shots"
    if not root.exists():
        return
    limit = time.time() - settings.screenshots_keep_days * 86400
    for d in root.iterdir():
        if d.is_dir() and d.stat().st_mtime < limit:
            shutil.rmtree(d, ignore_errors=True)


async def pump_batches(ctx) -> None:
    await batches.pump(ctx["redis"])  # страховка: подача пакета не должна останавливаться


async def startup(ctx) -> None:
    ctx["limit_watcher"] = asyncio.create_task(_watch_limit())
    try:
        await batches.recover_after_restart(ctx["redis"])
    except Exception:  # noqa: BLE001 — таблицы может ещё создавать API; подачу подхватит cron
        log.warning("batch recovery skipped", exc_info=True)


class WorkerSettings:
    functions = [run_scan]
    cron_jobs = [
        cron(cleanup_shots, hour=4, minute=30),
        cron(pump_batches, minute=set(range(0, 60, 5))),
    ]
    on_startup = startup
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    # Берём задания с запасом: реальное число одновременных проверок ограничивает Gate (настройка в админке)
    max_jobs = settings.max_concurrency + 4
    job_timeout = 6 * 3600  # включает ожидание своей очереди в Gate; сама проверка ограничена scan_timeout()
    max_tries = 1
