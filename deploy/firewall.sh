#!/bin/bash
#
# Сетевой фильтр: запрещает сервисам School AI выход в интернет.
#
#     deploy/firewall.sh on       — включить (и включать после каждой перезагрузки)
#     deploy/firewall.sh off      — выключить
#     deploy/firewall.sh status   — состояние
#     deploy/firewall.sh test     — проверить: интернет закрыт, локальные сервисы работают
#     deploy/firewall.sh diagnose — включить на 10 секунд, собрать сведения для разбора проблем
#
# Фильтр действует только на процессы пользователя service (Ollama, Open WebUI,
# School AI). Сам Mac Studio, обновления macOS и Homebrew работают как обычно.
# На время deploy/sync.sh фильтр автоматически снимается (нужно скачивать пакеты
# и модели) и включается обратно в конце.
#
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/config.sh

APPLY="sudo bash /opt/school-ai/app/deploy/firewall-apply.sh"

case "${1:-status}" in
    on)
        ssh -t "${SSH_OPTS[@]}" "$SERVER" "sudo touch /opt/school-ai/config/firewall.on && $APPLY apply-confirm 90 && $APPLY status" || exit 1
        echo
        echo "Проверка с этого компьютера:"
        sleep 3
        failed=0
        for url in "http://$SERVER_HOST:$WEB_PORT/api/health" "http://$SERVER_HOST:$OWUI_PORT/health"; do
            if curl -s -m 8 -o /dev/null "$url"; then echo "  доступно:   $url"; else echo "  НЕДОСТУПНО: $url"; failed=1; fi
        done
        health="$(curl -s -m 8 "http://$SERVER_HOST:$WEB_PORT/api/health" || true)"
        echo "  состояние School AI: ${health:-нет ответа}"
        if [[ $failed -eq 1 || "$health" != *'"ollama":true'*'"db":true'* ]]; then
            echo
            echo "✗ С фильтром система работает неправильно — подтверждение НЕ отправлено."
            echo "  Фильтр будет снят автоматически в течение 90 секунд (пароль не нужен)."
            echo "  (если доступ из сети закрыт — deploy/network.sh close — проверка по сети невозможна)"
            exit 1
        fi
        ssh "${SSH_OPTS[@]}" "$SERVER" "touch /tmp/school-ai-firewall-confirmed"
        echo "✓ Сервисы работают, выход в интернет для них закрыт. Фильтр закреплён."
        echo "  Подробная проверка: deploy/firewall.sh test"
        ;;
    off)
        ssh -t "${SSH_OPTS[@]}" "$SERVER" "sudo rm -f /opt/school-ai/config/firewall.on && $APPLY apply && $APPLY status" ;;
    status)
        ssh -t "${SSH_OPTS[@]}" "$SERVER" "$APPLY status" ;;
    diagnose)
        # скрипт диагностики берём из репозитория (на сервере может быть старая версия)
        scp -q "${SSH_OPTS[@]}" deploy/firewall-apply.sh deploy/pf-school-ai.conf "$SERVER:/tmp/"
        ssh -t "${SSH_OPTS[@]}" "$SERVER" "sudo sed -i '' 's#^RULES=.*#RULES=/tmp/pf-school-ai.conf#' /tmp/firewall-apply.sh && sudo bash /tmp/firewall-apply.sh diagnose"
        ;;
    test)
        ssh -t "${SSH_OPTS[@]}" "$SERVER" '
            check() { if sudo -u service -H curl -s -m 8 -o /dev/null "$1"; then echo "  доступно:   $1"; else echo "  недоступно: $1"; fi; }
            echo "От имени service (так работают Ollama, Open WebUI, School AI):"
            check https://ollama.com
            check https://huggingface.co
            check https://api.openai.com
            check http://127.0.0.1:11434/api/version
            check http://127.0.0.1:8080/health
            check http://127.0.0.1:8000/api/health
            echo "От имени администратора (сам Mac Studio):"
            if curl -s -m 8 -o /dev/null https://ollama.com; then echo "  доступно:   https://ollama.com"; else echo "  недоступно: https://ollama.com"; fi'
        echo
        echo "Ожидается при включённом фильтре: внешние адреса для service недоступны,"
        echo "локальные (127.0.0.1) доступны; у администратора интернет есть."
        ;;
    *) echo "Использование: deploy/firewall.sh on|off|status|test|diagnose"; exit 1 ;;
esac
