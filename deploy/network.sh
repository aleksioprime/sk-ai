#!/bin/bash
#
# Открывает или закрывает доступ к School AI из сети школы.
#
#     deploy/network.sh open     — Open WebUI (8080) и «Документы» (8000) доступны по адресу сервера
#     deploy/network.sh close    — только с самого сервера и через deploy/tunnel.sh
#     deploy/network.sh status   — показать текущее состояние
#
# Открывайте доступ ТОЛЬКО после регистрации администратора в Open WebUI.
# Вход на обе страницы — по учётным записям Open WebUI. API /v1 и база
# данных в сеть не открываются никогда.
#
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/config.sh

case "${1:-status}" in
    open) host=0.0.0.0 ;;
    close) host=127.0.0.1 ;;
    status)
        ssh "${SSH_OPTS[@]}" "$SERVER" "lsof -nP -iTCP:$OWUI_PORT -iTCP:$WEB_PORT -sTCP:LISTEN 2>/dev/null | awk 'NR>1 {print \$9}' | sort -u" \
            | sed 's/^/слушает: /'
        echo "(127.0.0.1 — только локально и через туннель; *: — открыто в сеть)"
        exit 0 ;;
    *) echo "Использование: deploy/network.sh open|close|status"; exit 1 ;;
esac

echo "==> HOST=$host для Open WebUI и страницы «Документы» (введите пароль администратора сервера)"
ssh -t "${SSH_OPTS[@]}" "$SERVER" "set -e
    sudo sed -i '' 's/^HOST=.*/HOST=$host/' /opt/school-ai/config/open-webui.env
    if sudo grep -q '^WEB_HOST=' /opt/school-ai/config/.env; then
        sudo sed -i '' 's/^WEB_HOST=.*/WEB_HOST=$host/' /opt/school-ai/config/.env
    else
        echo 'WEB_HOST=$host' | sudo tee -a /opt/school-ai/config/.env >/dev/null
    fi
    sudo bash /opt/school-ai/app/deploy/update-cors.sh >/dev/null
    sudo launchctl kickstart -k system/ru.school-ai.open-webui
    sudo launchctl kickstart -k system/ru.school-ai.web"

if [[ "$host" == 0.0.0.0 ]]; then
    # фактические адреса сервера в сети
    addrs="$(ssh "${SSH_OPTS[@]}" "$SERVER" 'echo "$(scutil --get LocalHostName).local"; for i in $(ifconfig -l); do ipconfig getifaddr $i 2>/dev/null; done' | sort -u)"
    echo "Готово. Адреса для сотрудников:"
    for a in $addrs; do
        echo "  чат: http://$a:$OWUI_PORT    документы: http://$a:$WEB_PORT"
    done
else
    echo "Готово. Доступ только через deploy/tunnel.sh."
fi
