#!/usr/bin/env bash
# Диагностика VPS перед установкой. Ничего не меняет — только читает.
#   bash check-server.sh
DOMAIN="${DOMAIN:-weblegalcompliance.itb2bhost.ru}"
PORTS="${PORTS:-80 443 3100 8100}"

ok()   { printf '  \033[32m✔\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m✘\033[0m %s\n' "$*"; }
info() { printf '  • %s\n' "$*"; }

echo "== Система"
info "$(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME") · ядро $(uname -r) · $(uname -m)"
info "CPU: $(nproc) · RAM: $(free -h | awk '/Mem:/{print $2" всего, "$7" свободно"}') · диск /: $(df -h / | awk 'NR==2{print $4" свободно из "$2}')"
[ "$(free -m | awk '/Mem:/{print $2}')" -ge 1900 ] && ok "памяти достаточно" || bad "нужно минимум 2 ГБ RAM (браузер для сканирования)"

echo "== Программы"
for c in git curl docker nginx certbot; do
  if command -v $c >/dev/null 2>&1; then ok "$c: $($c --version 2>&1 | head -1)"; else bad "$c не установлен"; fi
done
docker compose version >/dev/null 2>&1 && ok "docker compose: $(docker compose version --short)" || bad "плагин docker compose не установлен"
systemctl is-active --quiet nginx && ok "nginx запущен" || bad "nginx не запущен"
id -nG "$USER" | grep -qw docker && ok "пользователь $USER в группе docker" || info "пользователь $USER не в группе docker (для деплоя без sudo: sudo usermod -aG docker $USER)"

echo "== Порты"
for p in $PORTS; do
  line=$(sudo ss -ltnpH "sport = :$p" 2>/dev/null | head -1)
  if [ -z "$line" ]; then
    if [ "$p" = 80 ] || [ "$p" = 443 ]; then bad "порт $p свободен — nginx его не слушает"; else ok "порт $p свободен"; fi
  else
    proc=$(echo "$line" | grep -o 'users:(("[^"]*"' | cut -d'"' -f2)
    if [ "$p" = 80 ] || [ "$p" = 443 ]; then ok "порт $p занят: ${proc:-?} (ожидаемо nginx)"; else bad "порт $p занят: ${proc:-?} — нужно выбрать другой в .env"; fi
  fi
done

echo "== DNS"
SERVER_IP=$(curl -4 -s --max-time 5 https://ifconfig.me || curl -4 -s --max-time 5 https://api.ipify.org)
DNS_IP=$(getent ahostsv4 "$DOMAIN" | awk 'NR==1{print $1}')
info "внешний IP сервера: ${SERVER_IP:-не определён}"
if [ -z "$DNS_IP" ]; then bad "$DOMAIN не резолвится — проверьте A-запись"
elif [ "$DNS_IP" = "$SERVER_IP" ]; then ok "$DOMAIN → $DNS_IP (совпадает с сервером)"
else bad "$DOMAIN → $DNS_IP, а сервер $SERVER_IP — A-запись указывает не сюда"; fi

echo "== nginx"
ls /etc/nginx/sites-enabled/ 2>/dev/null | sed 's/^/  • сайт: /'
grep -rl "server_name.*$DOMAIN" /etc/nginx/ 2>/dev/null | sed 's/^/  • уже есть конфиг для домена: /'

echo "== Доступ к Роскомнадзору (для реестра операторов ПДн)"
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 https://rkn.gov.ru/opendata/)
[ "$code" = 200 ] && ok "rkn.gov.ru доступен" || bad "rkn.gov.ru недоступен (код $code)"
