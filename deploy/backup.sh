#!/bin/bash
#
# Резервная копия С СЕРВЕРА на этот компьютер (в git не попадает):
#
#     local/data/                         точная копия документов (/opt/school-ai/data)
#     local/backups/school_ai-ДАТА.dump   база School AI (PostgreSQL)
#     local/backups/open-webui-ДАТА.db    пользователи и чаты Open WebUI
#
#     deploy/backup.sh
#
# Хранятся 10 последних копий баз. Восстановление — docs/03-maintenance.md.
#
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/config.sh

stamp="$(date +%Y%m%d-%H%M)"
mkdir -p local/backups local/data

echo "==> База school_ai"
ssh "${SSH_OPTS[@]}" "$SERVER" \
    "/opt/homebrew/opt/postgresql@17/bin/pg_dump -h /tmp -Fc school_ai" > "local/backups/school_ai-$stamp.dump"

echo "==> Документы и база Open WebUI (введите пароль администратора сервера)"
ssh -t "${SSH_OPTS[@]}" "$SERVER" "set -e; mkdir -p ~/$REMOTE_SRC/backup; \
    sudo rsync -a --delete --exclude .DS_Store /opt/school-ai/data/ ~/$REMOTE_SRC/backup/data/; \
    sudo sqlite3 /opt/school-ai/open-webui/data/webui.db \".backup '\$HOME/$REMOTE_SRC/backup/open-webui.db'\" 2>/dev/null || true; \
    sudo chown -R \$(id -un) ~/$REMOTE_SRC/backup"
rsync -az --delete -e "ssh ${SSH_OPTS[*]}" --exclude .DS_Store "$SERVER:$REMOTE_SRC/backup/data/" local/data/
rsync -az -e "ssh ${SSH_OPTS[*]}" "$SERVER:$REMOTE_SRC/backup/open-webui.db" "local/backups/open-webui-$stamp.db" 2>/dev/null \
    || echo "  (база Open WebUI не найдена — пропускаю)"

for pattern in 'school_ai-*.dump' 'open-webui-*.db'; do
    ls -1t local/backups/$pattern 2>/dev/null | tail -n +11 | xargs rm -f
done

echo "Готово:"
echo "  документов: $(find local/data -type f ! -name '.*' | wc -l | tr -d ' ') файлов в local/data"
ls -lh local/backups | tail -n +2 | head -4
