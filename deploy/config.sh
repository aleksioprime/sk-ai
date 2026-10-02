# Параметры подключения к серверу. Используются скриптами deploy/*.sh
# на ВАШЕМ компьютере.
#
# Адрес сервера по умолчанию — его имя в локальной сети school-ai.local
# (Bonjour, работает без настройки, не зависит от IP-адреса).
# Если в сети гимназии имя не находится — укажите IP одним из способов:
#   * разово:     SCHOOL_AI_HOST=10.1.2.3 deploy/tunnel.sh
#   * постоянно:  файл deploy/config.local.sh (в git не попадает) со строкой
#                 SERVER_HOST=10.1.2.3

SERVER_USER=asemochkin                          # администратор macOS на сервере
SERVER_HOST="${SCHOOL_AI_HOST:-school-ai.local}"  # имя или IP Mac Studio
SSH_KEY="$HOME/.ssh/school-ai"                  # SSH-ключ
REMOTE_SRC=school-ai-src                        # копия репозитория на сервере (в домашней папке)
WEB_PORT=8000                                   # «Документы» и API School AI
OWUI_PORT=8080                                  # Open WebUI

# Локальные переопределения (не в git)
[[ -f "$(dirname "${BASH_SOURCE[0]}")/config.local.sh" ]] && source "$(dirname "${BASH_SOURCE[0]}")/config.local.sh"
[[ -n "${SCHOOL_AI_HOST:-}" ]] && SERVER_HOST="$SCHOOL_AI_HOST"

# HostKeyAlias: ключ сервера проверяется под постоянным именем «school-ai»,
# поэтому смена IP-адреса не вызывает предупреждений SSH
SSH_OPTS=(-i "$SSH_KEY" -o HostKeyAlias=school-ai -o ServerAliveInterval=30 -o ConnectTimeout=10)
SERVER="$SERVER_USER@$SERVER_HOST"
