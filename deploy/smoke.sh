#!/usr/bin/env bash
# Сквозная проверка после деплоя: API, сканирование, админка.
#   bash deploy/smoke.sh [https://weblegalcompliance.itb2bhost.ru]
set -euo pipefail
BASE="${1:-https://weblegalcompliance.itb2bhost.ru}"
cd "$(dirname "$0")/.."
set -a; [ -f .env ] && . ./.env; set +a
J=$(mktemp); C=$(mktemp)  # C — cookie сессии админки: удаляется при любом исходе, даже если тест упал
trap 'rm -f "$J" "$J.zip" "$C"' EXIT

step() { printf '\n== %s\n' "$*"; }
step "health";  curl -fsS "$BASE/api/health"
step "главная"; curl -fsS -o /dev/null -w '%{http_code}\n' "$BASE/"
step "запуск сканирования"
ID=$(curl -fsS -X POST "$BASE/api/scans" -H 'Content-Type: application/json' -d '{"url":"tilda.ru"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
echo "scan $ID"
for i in $(seq 1 100); do
  curl -fsS "$BASE/api/scans/$ID" > "$J"
  st=$(python3 -c 'import sys,json;d=json.load(open(sys.argv[1]));print(d["status"], d["pages_scanned"])' "$J")
  echo "  $st"; case "$st" in done*|failed*) break;; esac; sleep 3
done
python3 - "$J" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
if d["status"] != "done":
    print("СКАНИРОВАНИЕ НЕ УДАЛОСЬ:", d.get("error"))
    print("Подробности: docker compose logs --tail=50 worker")
    sys.exit(1)
r = d["report"]
print("score", r["score"], r["counts"], "| тип:", r["profile"]["primary_type"], "| findings:", len(r["findings"]))
PY
step "админка"
# Вход теми же логином и паролем, что и у человека (из .env на сервере). Пароль передаётся через стандартный
# ввод, а не аргументом команды — аргументы видны другим пользователям сервера в списке процессов.
python3 -c 'import json, os; print(json.dumps({"login": os.environ["ADMIN_LOGIN"], "password": os.environ["ADMIN_PASSWORD"]}))' \
  | curl -fsS -c "$C" -X POST "$BASE/api/admin/login" -H 'Content-Type: application/json' --data @- >/dev/null
curl -fsS -b "$C" "$BASE/api/admin/stats" | python3 -c 'import sys,json;d=json.load(sys.stdin);print("totals", d["totals"]);print("РКН", d["rkn"])'
curl -fsS -b "$C" "$BASE/api/admin/rules" | python3 -c 'import sys,json;print("правил в реестре:", len(json.load(sys.stdin)))'
step "пакетная проверка (2 сайта) и выгрузка архива"
B=$(curl -fsS -b "$C" -X POST "$BASE/api/admin/batches" -H 'Content-Type: application/json' \
  -d '{"name":"smoke-test","urls":"tilda.ru\nexample.com"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
echo "batch $B"
for i in $(seq 1 120); do
  st=$(curl -fsS -b "$C" "$BASE/api/admin/batches/$B" | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d["state"], d["finished"], "/", d["total"], d["counts"])')
  echo "  $st"; case "$st" in done*) break;; esac; sleep 5
done
curl -fsS -b "$C" "$BASE/api/admin/export.zip?batch=$B" -o "$J.zip"
python3 - "$J.zip" <<'PY'
import json, sys, zipfile
z = zipfile.ZipFile(sys.argv[1]); scans = json.loads(z.read("scans.json"))
snaps = [n for n in z.namelist() if n.endswith("site.json.gz")]
print("в архиве проверок:", len(scans), "| снимков данных:", len(snaps), "| файлов:", len(z.namelist()))
assert len(scans) == 2 and snaps, "в архиве нет снимков данных"
PY
echo; echo "SMOKE OK"
