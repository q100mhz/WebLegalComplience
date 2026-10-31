"""Настройки, которые меняются в админке без перезапуска сервиса, и сведения о ресурсах сервера."""

import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select

from .config import settings
from .db import Scan, SessionLocal, kv_get, kv_set, utcnow

KEY = "runtime"
DEFAULT_DURATION = 90.0  # сек на проверку, пока нет статистики
PER_SCAN_GB = 0.8  # браузер + разбор страниц на одну одновременную проверку
RESERVED_GB = 1.2  # Postgres, Redis, API, фронтенд, система


def server_resources() -> dict:
    mem_gb = None
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    mem_gb = round(int(line.split()[1]) / 1024 / 1024, 1)
                    break
    except OSError:
        pass
    cpus = os.cpu_count() or 1
    by_mem = int(max(mem_gb - RESERVED_GB, PER_SCAN_GB) / PER_SCAN_GB) if mem_gb else 1
    return {"cpus": cpus, "mem_gb": mem_gb, "recommended": max(1, min(cpus, by_mem, settings.max_concurrency))}


async def get_settings() -> dict:
    async with SessionLocal() as db:
        data = await kv_get(db, KEY)
    return {
        "scan_concurrency": int(data.get("scan_concurrency", settings.worker_concurrency)),
        "max_pages": int(data.get("max_pages", settings.max_pages)),
    }


async def update_settings(scan_concurrency: int | None = None, max_pages: int | None = None) -> dict:
    changes = {}
    if scan_concurrency is not None:
        changes["scan_concurrency"] = max(1, min(int(scan_concurrency), settings.max_concurrency))
    if max_pages is not None:
        changes["max_pages"] = max(settings.min_pages_limit, min(int(max_pages), settings.max_pages_limit))
    async with SessionLocal() as db:
        data = await kv_get(db, KEY)
        await kv_set(db, KEY, {**data, **changes})
        await db.commit()
    return await get_settings()


_avg_cache: tuple[float, float] = (0.0, DEFAULT_DURATION)


async def avg_duration() -> float:
    """Средняя длительность проверки (сек) по последним 30 успешным, кеш на минуту."""
    global _avg_cache
    if time.monotonic() - _avg_cache[0] < 60:
        return _avg_cache[1]
    async with SessionLocal() as db:
        rows = (await db.execute(
            select(Scan.started_at, Scan.finished_at)
            .where(Scan.status == "done", Scan.started_at.is_not(None), Scan.finished_at.is_not(None))
            .order_by(Scan.finished_at.desc()).limit(30)
        )).all()
    durations = [(f - s).total_seconds() for s, f in rows if f > s]
    value = sum(durations) / len(durations) if durations else DEFAULT_DURATION
    _avg_cache = (time.monotonic(), value)
    return value


async def eta_seconds(scan: Scan) -> int | None:
    """Сколько примерно осталось до готового отчёта (для посетителя — без слова «очередь»)."""
    if scan.status not in ("queued", "running"):
        return None
    avg = await avg_duration()
    now = utcnow()
    if scan.status == "running":
        elapsed = (now - (scan.started_at or now)).total_seconds()
        if scan.pages_scanned >= 3:
            total = elapsed / scan.pages_scanned * (scan.max_pages or settings.max_pages)
            remaining = min(total, avg * 2) - elapsed
        else:
            remaining = avg - elapsed
        return int(max(remaining, 10))
    cfg = await get_settings()
    limit = cfg["scan_concurrency"]
    # длительность прошлых проверок пересчитываем на текущую глубину
    avg = avg * cfg["max_pages"] / max(settings.max_pages, 1) if cfg["max_pages"] != settings.max_pages else avg
    async with SessionLocal() as db:
        ahead = (await db.execute(select(func.count()).select_from(Scan).where(
            Scan.status == "queued", Scan.created_at < scan.created_at,
            Scan.created_at > now - timedelta(hours=6)))).scalar_one()
        running = (await db.execute(select(func.count()).select_from(Scan).where(Scan.status == "running"))).scalar_one()
    position = ahead + running  # сколько проверок должно освободить место до старта этой
    waves = 0 if position < limit else (position - limit) // limit + 1
    # освобождения ждём в среднем полпроверки, дальше — целыми проверками
    wait = 0 if not waves else avg / 2 + (waves - 1) * avg
    return int(wait + avg)


# ---------- Нагрузка в динамике ----------

@dataclass
class LoadScan:
    created: datetime
    started: datetime | None
    finished: datetime | None
    status: str
    batch: bool

    def running_at(self, t: datetime, now: datetime) -> bool:
        return bool(self.started) and self.started <= t < (self.finished or now)

    def waiting_at(self, t: datetime, now: datetime) -> bool:
        # ждут старта только проверки посетителей: пакет ждёт своей очереди намеренно
        if self.batch or self.status in ("pending", "cancelled"):
            return False
        return self.created <= t < (self.started or self.finished or now)


def load_series(scans: list[LoadScan], now: datetime, span_s: int, bucket_s: int, step_s: int) -> list[dict]:
    """Столбцы по bucket_s секунд: пиковое число одновременно выполнявшихся и ожидавших старта проверок
    (с выборкой каждые step_s секунд), сколько стартовало, ожидание и длительность."""
    n = span_s // bucket_s
    start = now - timedelta(seconds=n * bucket_s)
    out = []
    for b in range(n):
        b0 = start + timedelta(seconds=b * bucket_s)
        b1 = b0 + timedelta(seconds=bucket_s)
        max_run = max_wait = 0
        t = b0
        while t < b1 and t <= now:
            max_run = max(max_run, sum(s.running_at(t, now) for s in scans))
            max_wait = max(max_wait, sum(s.waiting_at(t, now) for s in scans))
            t += timedelta(seconds=step_s)
        started = [s for s in scans if s.started and b0 <= s.started < b1]
        waits = [(s.started - s.created).total_seconds() for s in started if not s.batch]
        durs = [(s.finished - s.started).total_seconds() for s in started if s.finished and s.finished > s.started]
        out.append({
            "t": b0.isoformat(), "started": len(started), "started_batch": sum(s.batch for s in started),
            "max_running": max_run, "max_waiting": max_wait,
            "avg_wait_s": round(sum(waits) / len(waits)) if waits else None,
            "max_wait_s": round(max(waits)) if waits else None,
            "avg_duration_s": round(sum(durs) / len(durs)) if durs else None,
        })
    return out
