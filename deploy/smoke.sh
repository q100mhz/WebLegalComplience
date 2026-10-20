#!/usr/bin/env bash
# Сквозная проверка после деплоя: API, сканирование, админка.
#   bash deploy/smoke.sh [https://welegalcompliance.itb2bhost.ru]
set -euo pipefail
BASE="${1:-https://welegalcompliance.itb2bhost.ru}"
cd "$(dirname "$0")/.."
set -a; [ -f .env ] && . ./.env; set +a
J=$(mktemp); trap 'rm -f $J' EXIT

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
assert d["status"] == "done", d.get("error")
r = d["report"]
print("score", r["score"], r["counts"], "| тип:", r["profile"]["primary_type"], "| findings:", len(r["findings"]))
PY
step "админка"
C=$(mktemp)
curl -fsS -c "$C" -X POST "$BASE/api/admin/login" -H 'Content-Type: application/json' \
  -d "{\"login\":\"$ADMIN_LOGIN\",\"password\":\"$ADMIN_PASSWORD\"}" >/dev/null
curl -fsS -b "$C" "$BASE/api/admin/stats" | python3 -c 'import sys,json;d=json.load(sys.stdin);print("totals", d["totals"]);print("РКН", d["rkn"])'
curl -fsS -b "$C" "$BASE/api/admin/rules" | python3 -c 'import sys,json;print("правил в реестре:", len(json.load(sys.stdin)))'
rm -f "$C"
echo; echo "SMOKE OK"
