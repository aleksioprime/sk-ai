#!/bin/bash
#
# Вход на сервер по SSH (адрес и ключ — из deploy/config.sh).
#
#     deploy/ssh.sh                       — открыть терминал на сервере
#     deploy/ssh.sh school-ai check       — выполнить команду на сервере
#
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/config.sh
exec ssh -t "${SSH_OPTS[@]}" "$SERVER" "$@"
