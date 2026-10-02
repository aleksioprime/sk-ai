#!/bin/bash
#
# Открывает интерфейсы School AI на этом компьютере через SSH-туннель:
#
#     http://localhost:8080  — Open WebUI (чат для пользователей)
#     http://localhost:8000  — «Документы» (файлы и статус индексации) и проверка ответов
#
# Пока скрипт работает, адреса доступны. Остановить: Ctrl+C.
#
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/config.sh

echo "Open WebUI:        http://localhost:$OWUI_PORT"
echo "Документы:         http://localhost:$WEB_PORT"
echo "(Ctrl+C — закрыть)"
(sleep 2 && open "http://localhost:$OWUI_PORT") >/dev/null 2>&1 &
exec ssh "${SSH_OPTS[@]}" -N \
    -L "$OWUI_PORT:127.0.0.1:$OWUI_PORT" \
    -L "$WEB_PORT:127.0.0.1:$WEB_PORT" \
    "$SERVER"
