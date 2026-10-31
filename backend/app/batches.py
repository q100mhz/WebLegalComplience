"""Пакетные проверки: подача сайтов из пакетов в очередь по одному.

В работе одновременно не больше одной проверки из пакетов: следующая ставится в очередь, когда
закончилась предыдущая. Проверка посетителя сайта, пришедшая в это время, ждёт максимум одну
пакетную проверку, а не весь пакет.
"""

import logging
import time
from datetime import timedelta

from redis.exceptions import LockError
from sqlalchemy import func, select, update

from .db import Batch, Scan, SessionLocal, utcnow

log = logging.getLogger(__name__)
ACTIVE = ("queued", "running")


async def enqueue(redis, scan_id: str) -> None:
    # к id задания добавляем время: arq хранит результат прошлого задания и не примет тот же id повторно
    await redis.enqueue_job("run_scan", scan_id, _job_id=f"scan:{scan_id}:{int(time.time())}")


async def pump(redis) -> str | None:
    """Ставит в очередь следующую проверку из пакетов, если сейчас ни одна не выполняется."""
    try:
        return await _pump(redis)
    except LockError:  # подачу прямо сейчас выполняет другой процесс
        return None


async def _pump(redis) -> str | None:
    async with redis.lock("batch-pump", timeout=30, blocking_timeout=10):
        async with SessionLocal() as db:
            active = (await db.execute(
                select(func.count()).select_from(Scan).where(Scan.batch_id.is_not(None), Scan.status.in_(ACTIVE))
            )).scalar_one()
            if active:
                return None
            nxt = (await db.execute(
                select(Scan).join(Batch, Batch.id == Scan.batch_id)
                .where(Scan.status == "pending", Batch.paused.is_(False))  # пакеты на паузе пропускаем
                .order_by(Scan.created_at, Scan.id).limit(1)
            )).scalar_one_or_none()
            if not nxt:
                return None
            nxt.status = "queued"
            await db.commit()
        await enqueue(redis, nxt.id)
        log.info("batch pump: queued %s %s", nxt.id, nxt.host)
        return nxt.id


async def recover_after_restart(redis) -> None:
    """После перезапуска воркера задания в памяти потеряны: «зависшие» проверки закрываем,
    пакетные из очереди возвращаем в ожидание и запускаем подачу заново."""
    async with SessionLocal() as db:
        await db.execute(update(Scan).where(Scan.status == "running").values(
            status="failed", error="Проверка прервана перезапуском сервиса", finished_at=utcnow()))
        await db.execute(update(Scan).where(Scan.batch_id.is_not(None), Scan.status == "queued").values(status="pending"))
        # проверки посетителей: недавние — заново в очередь (задание могло потеряться), старые — закрываем
        recent = utcnow() - timedelta(hours=2)
        await db.execute(update(Scan).where(Scan.batch_id.is_(None), Scan.status == "queued", Scan.created_at < recent)
                         .values(status="failed", error="Проверка прервана перезапуском сервиса", finished_at=utcnow()))
        requeue = (await db.execute(select(Scan.id).where(Scan.batch_id.is_(None), Scan.status == "queued")
                                    .order_by(Scan.created_at))).scalars().all()
        await db.commit()
    for scan_id in requeue:  # повторный запуск безопасен: run_scan выполняет только проверки в статусе queued
        await enqueue(redis, scan_id)
    await pump(redis)
