#!/bin/bash
#
# Отправляет КОД с этого компьютера на сервер и обновляет установку.
#
#     deploy/sync.sh                 — код + переустановка + индексация новых документов
#     deploy/sync.sh --no-index      — без индексации
#     deploy/sync.sh --reindex       — переиндексировать все документы заново
#     deploy/sync.sh --upload-only   — только скопировать файлы, ничего не запускать
#
# Документы этот скрипт НЕ трогает: главная копия документов — на сервере.
# Загрузить документы: deploy/push-data.sh, резервная копия: deploy/backup.sh.
#
# На сервере понадобится пароль администратора (sudo) — его вводите вы сами.
#
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/config.sh

UPLOAD_ONLY=0
INSTALL_ARGS=()
for arg in "$@"; do
    case "$arg" in
        --upload-only) UPLOAD_ONLY=1 ;;
        *) INSTALL_ARGS+=("$arg") ;;
    esac
done

echo "==> Копирую код на $SERVER:~/$REMOTE_SRC"
rsync -az --delete -e "ssh ${SSH_OPTS[*]}" \
    --exclude .git --exclude .venv --exclude local --exclude __pycache__ \
    --exclude '*.egg-info' --exclude .DS_Store \
    ./ "$SERVER:$REMOTE_SRC/"

if [[ $UPLOAD_ONLY -eq 1 ]]; then
    echo "Готово (только копирование)."
    exit 0
fi

echo "==> Запускаю установку на сервере (введите пароль администратора сервера)"
ssh -t "${SSH_OPTS[@]}" "$SERVER" "sudo bash ~/$REMOTE_SRC/deploy/install.sh ${INSTALL_ARGS[*]:-}"
