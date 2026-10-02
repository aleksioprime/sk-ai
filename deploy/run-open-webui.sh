#!/bin/bash
# Запуск Open WebUI с настройками из /opt/school-ai/config/open-webui.env.
# Вызывается сервисом ru.school-ai.open-webui (launchd не умеет читать env-файлы).
set -euo pipefail

# Читаем строки КЛЮЧ=значение (значение может содержать пробелы; кавычки снимаются)
while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
    key="${line%%=*}"
    value="${line#*=}"
    value="${value%\"}"; value="${value#\"}"
    export "$key=$value"
done < /opt/school-ai/config/open-webui.env

cd /opt/school-ai/open-webui
exec /opt/school-ai/open-webui/.venv/bin/open-webui serve --host "${HOST:-127.0.0.1}" --port "${PORT:-8080}"
