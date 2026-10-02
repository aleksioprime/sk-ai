#!/bin/bash
#
# Записывает в настройки Open WebUI (CORS_ALLOW_ORIGIN) все адреса, по которым
# к серверу могут прийти браузеры: localhost, имя school-ai.local и текущие IP —
# через Caddy (без порта) и напрямую (порт 8080, через SSH-туннель).
# Без этого чат Open WebUI не работает, если открыт по адресу не из списка.
#
# Запускается от root на сервере: install.sh и deploy/network.sh вызывают его сами
# (поэтому после смены IP достаточно deploy/sync.sh или deploy/network.sh open).
#
set -euo pipefail
OWUI_ENV=/opt/school-ai/config/open-webui.env
[[ -f "$OWUI_ENV" ]] || exit 0

port="$(grep -E '^PORT=' "$OWUI_ENV" | cut -d= -f2 || true)"; port="${port:-8080}"
hosts=(localhost 127.0.0.1 "$(scutil --get LocalHostName 2>/dev/null || hostname -s).local")
for iface in $(ifconfig -l); do
    ip="$(ipconfig getifaddr "$iface" 2>/dev/null || true)"
    [[ -n "$ip" ]] && hosts+=("$ip")
done

# через Caddy (порт 80, без номера порта) и напрямую (порт Open WebUI)
origins="$(printf "http://%s;" "${hosts[@]}")$(printf "http://%s:$port;" "${hosts[@]}")"
origins="${origins%;}"

if grep -q '^CORS_ALLOW_ORIGIN=' "$OWUI_ENV"; then
    sed -i '' "s|^CORS_ALLOW_ORIGIN=.*|CORS_ALLOW_ORIGIN=$origins|" "$OWUI_ENV"
else
    echo "CORS_ALLOW_ORIGIN=$origins" >> "$OWUI_ENV"
fi
echo "$origins"
