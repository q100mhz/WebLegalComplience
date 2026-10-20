#!/usr/bin/env bash
# Запускается на VPS из GitHub Actions: обновляет код и пересобирает контейнеры.
set -euo pipefail
cd "${APP_DIR:-/opt/welegalcompliance}"
set -a; [ -f .env ] && . ./.env; set +a

git fetch --prune origin main
git reset --hard origin/main

docker compose build --pull
docker compose up -d --remove-orphans
docker image prune -f >/dev/null

# Проверяем, что API поднялось
for i in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${API_PORT:-8100}/api/health" >/dev/null; then
    echo "Deploy OK"; exit 0
  fi
  sleep 2
done
echo "API не ответило после деплоя" >&2
docker compose logs --tail=50 api worker >&2
exit 1
