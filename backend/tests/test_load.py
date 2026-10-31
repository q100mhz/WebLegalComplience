from datetime import datetime, timedelta, timezone

from app.runtime import LoadScan, load_series

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


def at(minutes_ago: float) -> datetime:
    return NOW - timedelta(minutes=minutes_ago)


def test_burst_of_visitors_is_visible():
    # 10 минут назад трое пришли одновременно, проверки по 2 минуты, лимит 1 → двое ждали
    scans = [
        LoadScan(at(10), at(10), at(8), "done", False),
        LoadScan(at(10), at(8), at(6), "done", False),
        LoadScan(at(10), at(6), at(4), "done", False),
        LoadScan(at(9), at(9), at(7), "done", True),  # пакетная: выполняется, но в «ожидающих» не считается
    ]
    series = load_series(scans, NOW, 3600, 300, 30)
    peak = max(series, key=lambda x: x["max_waiting"])
    assert peak["max_waiting"] == 2
    assert max(x["max_running"] for x in series) == 2  # одна проверка посетителя + одна пакетная
    assert peak["max_wait_s"] == 240  # третий ждал 4 минуты
    assert sum(x["started"] for x in series) == 4 and sum(x["started_batch"] for x in series) == 1


def test_currently_waiting_scan_counts_until_now():
    scans = [LoadScan(at(3), None, None, "queued", False), LoadScan(at(5), at(5), None, "running", False)]
    last = load_series(scans, NOW, 600, 300, 30)[-1]
    assert last["max_waiting"] == 1 and last["max_running"] == 1
