#!/bin/bash
#
# Загружает документы с этого компьютера на сервер (индексирует их служба на сервере).
#
#     deploy/push-data.sh                 — добавить/обновить файлы из local/data
#     deploy/push-data.sh --mirror        — сделать сервер ТОЧНОЙ копией local/data
#                                           (файлы, которых нет локально, будут УДАЛЕНЫ)
#
# Главная копия документов — на сервере. Этот скрипт нужен, чтобы:
#   * загрузить документы на новый сервер из резервной копии (local/data);
#   * загрузить сразу много файлов (обычно удобнее страница «Документы»).
# Положите файлы в local/data/documents/<папка>/ и запустите скрипт.
#
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/config.sh

DELETE=""
[[ "${1:-}" == "--mirror" ]] && DELETE="--delete"

if [[ -z "$(find local/data -type f ! -name '.*' 2>/dev/null | head -1)" ]]; then
    echo "✗ Папка local/data пуста — загружать нечего."
    exit 1
fi
if [[ -n "$DELETE" ]]; then
    read -r -p "Файлы на сервере, которых нет в local/data, будут удалены. Продолжить? [y/N] " answer
    [[ "$answer" == y || "$answer" == Y ]] || exit 1
fi

echo "==> local/data → $SERVER:~/$REMOTE_SRC/upload"
rsync -az --delete -e "ssh ${SSH_OPTS[*]}" --exclude .DS_Store local/data/ "$SERVER:$REMOTE_SRC/upload/"

echo "==> Копирую в /opt/school-ai/data (введите пароль администратора сервера)"
ssh -t "${SSH_OPTS[@]}" "$SERVER" "sudo rsync -a $DELETE --exclude .DS_Store ~/$REMOTE_SRC/upload/ /opt/school-ai/data/ \
    && sudo chown -R service:staff /opt/school-ai/data"
echo "Готово. Служба индексации подхватит изменения за несколько секунд —"
echo "статус виден на странице «Документы» (deploy/tunnel.sh → http://localhost:8000)."
