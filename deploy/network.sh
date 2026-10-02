#!/bin/bash
#
# Открывает или закрывает доступ к School AI из сети школы.
#
#     deploy/network.sh open     — запустить Caddy: http://school-ai.local (чат)
#                                  и http://school-ai.local/documents/ («Документы»)
#     deploy/network.sh close    — остановить Caddy: доступ только через deploy/tunnel.sh
#     deploy/network.sh status   — показать текущее состояние
#
# Open WebUI (8080) и «Документы» (8000) всегда слушают только 127.0.0.1 —
# в сеть смотрит только Caddy (порт 80). Вход — по учётным записям Open WebUI.
# Открывайте доступ ТОЛЬКО после регистрации администратора в Open WebUI.
#
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/config.sh

server_addresses() {
    ssh "${SSH_OPTS[@]}" "$SERVER" 'echo "$(scutil --get LocalHostName).local"; for i in $(ifconfig -l); do ipconfig getifaddr $i 2>/dev/null; done; true' | sort -u
}

case "${1:-status}" in
    open|close) ;;
    status)
        ssh "${SSH_OPTS[@]}" "$SERVER" "netstat -an -p tcp | awk '/LISTEN/ && /\\.(80|8000|8080) / {print \$4}'" \
            | sed -e 's/^/слушает: /'
        echo "(*.80 — Caddy открыт в сеть; 127.0.0.1.8000/8080 — сервисы только локально)"
        exit 0 ;;
    *) echo "Использование: deploy/network.sh open|close|status"; exit 1 ;;
esac

# Сервисы всегда только локально; в сеть — только Caddy
REMOTE="set -e
    sudo sed -i '' 's/^HOST=.*/HOST=127.0.0.1/' /opt/school-ai/config/open-webui.env
    if sudo grep -q '^WEB_HOST=' /opt/school-ai/config/.env; then
        sudo sed -i '' 's/^WEB_HOST=.*/WEB_HOST=127.0.0.1/' /opt/school-ai/config/.env
    fi
    sudo bash /opt/school-ai/app/deploy/update-cors.sh >/dev/null
    sudo launchctl kickstart -k system/ru.school-ai.open-webui
    sudo launchctl kickstart -k system/ru.school-ai.web"

if [[ "$1" == open ]]; then
    echo "==> Открываю доступ из сети через Caddy (введите пароль администратора сервера)"
    ssh -t "${SSH_OPTS[@]}" "$SERVER" "$REMOTE
        sudo touch /opt/school-ai/config/network.open
        sudo launchctl bootout system/ru.school-ai.caddy 2>/dev/null && sleep 2 || true
        sudo install -m 644 -o root -g wheel /opt/school-ai/app/deploy/launchd/ru.school-ai.caddy.plist /Library/LaunchDaemons/
        sudo launchctl bootstrap system /Library/LaunchDaemons/ru.school-ai.caddy.plist"
    echo "==> Проверка (Open WebUI перезапускается — до минуты)"
    for _ in $(seq 1 60); do
        curl -s -m 3 -o /dev/null "http://$SERVER_HOST/health" && break
        sleep 2
    done
    for url in "http://$SERVER_HOST/health" "http://$SERVER_HOST/documents/api/health"; do
        if curl -s -m 5 -o /dev/null "$url"; then echo "  доступно:   $url"; else echo "  НЕДОСТУПНО: $url"; fi
    done
    echo
    echo "Адреса для сотрудников:"
    for a in $(server_addresses); do
        echo "  чат: http://$a/      документы: http://$a/documents/"
    done
else
    echo "==> Закрываю доступ из сети (введите пароль администратора сервера)"
    ssh -t "${SSH_OPTS[@]}" "$SERVER" "$REMOTE
        sudo rm -f /opt/school-ai/config/network.open
        sudo launchctl bootout system/ru.school-ai.caddy 2>/dev/null || true"
    echo "Готово. Доступ только через deploy/tunnel.sh."
fi
