"""Аудит правил по выгрузке истории из админки (Проверки → «Скачать историю»).

    cd backend && python -m scripts.audit ../audit/wlc-history-....zip > ../audit/report.md

Для каждой проверки со снимком данных (site.json.gz) заново строит отчёт ТЕКУЩИМИ правилами и сравнивает
со статусами, сохранёнными в момент проверки. Плюс сводка по правилам и жалобам «Это ошибка?».
"""

import gzip
import io
import json
import sys
import zipfile
from collections import Counter, defaultdict

from app.scanner import snapshot
from app.scanner.report import build_report

ORDER = ("fail", "warn", "info", "pass")


def main(path: str) -> None:
    zf = zipfile.ZipFile(path)
    scans = json.loads(zf.read("scans.json"))
    names = set(zf.namelist())
    by_rule: dict[str, Counter] = defaultdict(Counter)
    complaints: dict[str, list] = defaultdict(list)
    changes = []
    rerun = 0

    print(f"# Аудит правил: {path.rsplit('/', 1)[-1]}\n")
    print(f"Проверок в выгрузке: {len(scans)}; успешных: {sum(s['status'] == 'done' for s in scans)}; "
          f"со снимком данных: {sum(s.get('has_snapshot', False) for s in scans)}.\n")

    for s in scans:
        res = s.get("result") or {}
        for f in res.get("findings", []):
            by_rule[f["id"]][f["status"]] += 1
        for fb in s.get("feedback", []):
            complaints[fb["finding_id"]].append(f"{s['host']}: {fb['kind']} — {fb['comment'][:150]}")
        snap = f"files/{s['id']}/{snapshot.SNAPSHOT_NAME}"
        if s["status"] != "done" or snap not in names:
            continue
        data = json.loads(gzip.open(io.BytesIO(zf.read(snap)), "rt", encoding="utf-8").read())["site"]
        site = snapshot.from_dict(data)
        site.profile = {"rkn": (data.get("profile") or {}).get("rkn", {})}
        new = build_report(site)
        rerun += 1
        old_st = {f["id"]: f["status"] for f in res.get("findings", [])}
        new_st = {f["id"]: f for f in new["findings"]}
        for rid in sorted(set(old_st) | set(new_st)):
            a, b = old_st.get(rid, "—"), new_st[rid]["status"] if rid in new_st else "—"
            if a != b:
                changes.append((s["host"], rid, a, b, new_st[rid]["summary"] if rid in new_st else ""))

    print("## Сводка по правилам (как было в отчётах)\n")
    print("| Правило | Нарушение | Риск | Инфо | Ок | Жалоб «ошибка» |")
    print("|---|---|---|---|---|---|")
    for rid in sorted(by_rule, key=lambda r: (-by_rule[r]["fail"], -by_rule[r]["warn"], r)):
        c = by_rule[rid]
        print(f"| `{rid}` | {c['fail']} | {c['warn']} | {c['info']} | {c['pass']} | {len(complaints.get(rid, []))} |")

    print(f"\n## Повторный прогон текущими правилами ({rerun} проверок со снимком)\n")
    if not changes:
        print("Изменений нет.\n")
    else:
        print("| Сайт | Правило | Было | Стало | Сейчас |")
        print("|---|---|---|---|---|")
        for host, rid, a, b, summary in changes:
            print(f"| {host} | `{rid}` | {a} | {b} | {summary[:120]} |")

    if complaints:
        print("\n## Жалобы пользователей «Это ошибка?»\n")
        for rid, items in complaints.items():
            print(f"**`{rid}`**")
            for i in items:
                print(f"- {i}")
            print()

    failed = [s for s in scans if s["status"] == "failed"]
    if failed:
        print("\n## Неудачные проверки\n")
        for s in failed:
            diag = (s.get("result") or {}).get("diagnostics") or {}
            print(f"- {s['host']} ({s['created_at'][:16]}): {s.get('error')} — {json.dumps(diag, ensure_ascii=False)[:300]}")


if __name__ == "__main__":
    main(sys.argv[1])
