#!/bin/bash
#
# Установка / обновление School AI на Mac (Apple Silicon).
#
# Запускается НА СЕРВЕРЕ из копии репозитория, от администратора через sudo:
#
#     sudo bash ~/school-ai-src/deploy/install.sh
#
# Обычно его вызывает deploy/sync.sh с вашего компьютера.
#
# Скрипт идемпотентный: на «чистом» Mac он ставит всё с нуля, на уже
# настроенном — только обновляет код/документы и перезапускает сервисы.
#
# Параметры:
#     --no-models    не скачивать модели Ollama
#     --reindex      переиндексировать все документы заново
#
# Документы (/opt/school-ai/data) этот скрипт НЕ перезаписывает: главная копия
# документов — на сервере. Резервная копия — deploy/backup.sh.
#
set -euo pipefail

# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------
ROOT=/opt/school-ai
SERVICE_USER=service
BREW=/opt/homebrew/bin/brew
PYTHON=/opt/homebrew/bin/python3.13
PYTHON_OWUI=/opt/homebrew/bin/python3.12     # Open WebUI пока требует Python < 3.13
OWUI_VERSION=0.11.4                          # версия Open WebUI (обновлять осознанно)
OWUI=$ROOT/open-webui
PG_BIN=/opt/homebrew/opt/postgresql@17/bin
PG_DATA=/opt/homebrew/var/postgresql@17
OLLAMA=/opt/homebrew/bin/ollama
LAUNCHD=/Library/LaunchDaemons
SRC="$(cd "$(dirname "$0")/.." && pwd)"
cd /  # текущая папка администратора недоступна пользователю service

DO_MODELS=1
REINDEX=""
for arg in "$@"; do
    case "$arg" in
        --no-index) ;;  # оставлено для совместимости: индексирует служба ru.school-ai.indexer
        --no-models) DO_MODELS=0 ;;
        --reindex) REINDEX="--force" ;;
        *) echo "Неизвестный параметр: $arg"; exit 1 ;;
    esac
done

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
ok()   { printf '    \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '    \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\n\033[31m✗ %s\033[0m\n' "$*"; exit 1; }

as_service() { sudo -u "$SERVICE_USER" -H env SCHOOL_AI_ENV="$ROOT/config/.env" "$@"; }

[[ $EUID -eq 0 ]] || die "Запустите через sudo: sudo bash $0"
ADMIN_USER="${SUDO_USER:-}"
[[ -n "$ADMIN_USER" && "$ADMIN_USER" != root ]] || die "Запускайте через sudo от администратора (не из-под root)"
as_admin() { sudo -u "$ADMIN_USER" -H "$@"; }

# Сетевой фильтр (deploy/firewall.sh) на время установки снимаем: нужно скачивать
# пакеты и модели. В конце — даже при ошибке — он включается снова.
if [[ -f "$ROOT/config/firewall.on" ]]; then
    bash "$SRC/deploy/firewall-apply.sh" suspend
    trap 'bash "$ROOT/app/deploy/firewall-apply.sh" apply >/dev/null 2>&1 || true' EXIT
    warn "сетевой фильтр снят на время установки (включится в конце)"
fi

# ---------------------------------------------------------------------------
step "1. Homebrew и пакеты"
# ---------------------------------------------------------------------------
[[ -x "$BREW" ]] || die "Homebrew не установлен. Установите его (см. docs/01-installation.md) и запустите скрипт снова."

for pkg in python@3.13 python@3.12 postgresql@17 pgvector ollama; do
    if as_admin "$BREW" list --versions "$pkg" >/dev/null 2>&1; then
        ok "$pkg уже установлен"
    else
        as_admin "$BREW" install "$pkg"
        ok "$pkg установлен"
    fi
done

# ---------------------------------------------------------------------------
step "2. Пользователь $SERVICE_USER (от него работают сервисы)"
# ---------------------------------------------------------------------------
if id "$SERVICE_USER" >/dev/null 2>&1; then
    ok "пользователь существует"
else
    # Пароль случайный и нигде не сохраняется: под этим пользователем не входят в систему
    sysadminctl -addUser "$SERVICE_USER" -fullName "School AI service" \
        -password "$(openssl rand -base64 32)" -home "/Users/$SERVICE_USER" >/dev/null 2>&1
    createhomedir -c -u "$SERVICE_USER" >/dev/null 2>&1 || true
    ok "пользователь создан"
fi
dscl . -create "/Users/$SERVICE_USER" IsHidden 1   # не показывать на экране входа

# ---------------------------------------------------------------------------
step "3. Каталоги $ROOT"
# ---------------------------------------------------------------------------
mkdir -p "$ROOT"/{app,config,logs,models/ollama,data/documents,data/reports} "$OWUI/data"
chown "$SERVICE_USER":staff "$ROOT"
chmod 750 "$ROOT"
chown -R "$SERVICE_USER":staff "$ROOT"/{app,config,data,models} "$OWUI"
chmod 700 "$ROOT/models" "$ROOT/config"
chmod 755 "$ROOT/logs"
ok "готово"

# ---------------------------------------------------------------------------
step "4. Код приложения → $ROOT/app"
# ---------------------------------------------------------------------------
rsync -a --delete \
    --exclude .git --exclude .venv --exclude local --exclude data \
    --exclude __pycache__ --exclude '*.egg-info' --exclude dev.env \
    "$SRC/" "$ROOT/app/"
chown -R "$SERVICE_USER":staff "$ROOT/app"
ok "скопирован из $SRC"

# ---------------------------------------------------------------------------
step "5. Python-окружение"
# ---------------------------------------------------------------------------
if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
    as_service "$PYTHON" -m venv "$ROOT/.venv"
    ok "создано $ROOT/.venv"
fi
chown -R "$SERVICE_USER":staff "$ROOT/.venv"
as_service "$ROOT/.venv/bin/pip" install -q --upgrade pip
as_service "$ROOT/.venv/bin/pip" install -q -e "$ROOT/app"
ok "зависимости установлены"

# Команда school-ai для администратора (сама переключается на пользователя service)
mkdir -p /usr/local/bin
cat > /usr/local/bin/school-ai <<EOF
#!/bin/bash
# Обёртка: запускает school-ai от пользователя $SERVICE_USER
if [[ "\$(id -un)" == "$SERVICE_USER" ]]; then
    exec env SCHOOL_AI_ENV=$ROOT/config/.env $ROOT/.venv/bin/school-ai "\$@"
fi
exec sudo -u $SERVICE_USER -H env SCHOOL_AI_ENV=$ROOT/config/.env $ROOT/.venv/bin/school-ai "\$@"
EOF
chmod 755 /usr/local/bin/school-ai
ok "команда /usr/local/bin/school-ai"

# ---------------------------------------------------------------------------
step "6. Файл настроек $ROOT/config/.env"
# ---------------------------------------------------------------------------
ENV_FILE="$ROOT/config/.env"
if [[ -f "$ENV_FILE" ]]; then
    ok "уже существует — не трогаю"
else
    password="$(openssl rand -hex 24)"
    sed "s/__DB_PASSWORD__/$password/" "$SRC/config/env.example" > "$ENV_FILE"
    ok "создан со случайным паролем БД"
fi
chown "$SERVICE_USER":staff "$ENV_FILE"
chmod 600 "$ENV_FILE"

# Ключ для OpenAI-совместимого API (через него Open WebUI обращается к нашему RAG)
if ! grep -qE '^API_KEY=.+' "$ENV_FILE"; then
    sed -i '' '/^API_KEY=/d' "$ENV_FILE"
    printf '\n# Ключ API для Open WebUI\nAPI_KEY=%s\n' "$(openssl rand -hex 32)" >> "$ENV_FILE"
    ok "добавлен API_KEY"
fi

env_value() { { grep -E "^$1=" "$ENV_FILE" || true; } | tail -1 | cut -d= -f2- ; }
DB_NAME="$(env_value DB_NAME)"; DB_NAME="${DB_NAME:-school_ai}"
DB_USER="$(env_value DB_USER)"; DB_USER="${DB_USER:-school_ai}"
DB_PASSWORD="$(env_value DB_PASSWORD)"
[[ -n "$DB_PASSWORD" ]] || die "В $ENV_FILE не задан DB_PASSWORD"

# ---------------------------------------------------------------------------
# Установка/обновление LaunchDaemon. Перезапускает сервис, если plist изменился.
#   install_daemon <имя> [restart]
# ---------------------------------------------------------------------------
install_daemon() {
    local name="$1" restart="${2:-}"
    local target="$LAUNCHD/$name.plist"
    local rendered
    rendered="$(mktemp)"
    sed "s/__ADMIN_USER__/$ADMIN_USER/g" "$SRC/deploy/launchd/$name.plist" > "$rendered"

    if [[ -f "$target" ]] && cmp -s "$rendered" "$target" && launchctl print "system/$name" >/dev/null 2>&1; then
        if [[ "$restart" == restart ]]; then
            launchctl kickstart -k "system/$name"
            ok "$name перезапущен"
        else
            ok "$name работает"
        fi
    else
        launchctl bootout "system/$name" >/dev/null 2>&1 && sleep 2 || true
        install -m 644 -o root -g wheel "$rendered" "$target"
        launchctl bootstrap system "$target" 2>/dev/null || { sleep 3; launchctl bootstrap system "$target"; }
        ok "$name установлен и запущен"
    fi
    rm -f "$rendered"
}

# ---------------------------------------------------------------------------
step "7. PostgreSQL"
# ---------------------------------------------------------------------------
if [[ ! -f "$PG_DATA/PG_VERSION" ]]; then
    as_admin "$PG_BIN/initdb" --locale=en_US.UTF-8 -E UTF8 -D "$PG_DATA" >/dev/null
    ok "кластер БД создан в $PG_DATA"
fi

# Пользовательский сервис Homebrew не нужен — используем системный LaunchDaemon
as_admin "$BREW" services stop postgresql@17 >/dev/null 2>&1 || true
install_daemon ru.school-ai.postgresql

for _ in $(seq 1 30); do
    "$PG_BIN/pg_isready" -q -h /tmp && break
    sleep 1
done
"$PG_BIN/pg_isready" -q -h /tmp || die "PostgreSQL не запустился, см. $ROOT/logs/postgresql-error.log"
ok "PostgreSQL принимает подключения"

psql_admin() { as_admin "$PG_BIN/psql" -h /tmp -d postgres -v ON_ERROR_STOP=1 -qAt "$@"; }

if [[ "$(psql_admin -c "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'")" != 1 ]]; then
    psql_admin -c "CREATE ROLE $DB_USER LOGIN"
    ok "роль $DB_USER создана"
fi
# Пароль роли всегда синхронизируется с .env
psql_admin -c "ALTER ROLE $DB_USER WITH LOGIN PASSWORD '$DB_PASSWORD'"

if [[ "$(psql_admin -c "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'")" != 1 ]]; then
    psql_admin -c "CREATE DATABASE $DB_NAME OWNER $DB_USER"
    ok "база $DB_NAME создана"
fi
as_admin "$PG_BIN/psql" -h /tmp -d "$DB_NAME" -v ON_ERROR_STOP=1 -qAt -c "CREATE EXTENSION IF NOT EXISTS vector"
ok "база $DB_NAME, роль $DB_USER, расширение vector"

# Доступ: по сети (127.0.0.1) — только с паролем; через сокет — только «свой» пользователь
HBA="$PG_DATA/pg_hba.conf"
if grep -qE '^(local|host)[[:space:]].*trust[[:space:]]*$' "$HBA"; then
    cp "$HBA" "$HBA.bak-$(date +%Y%m%d-%H%M%S)"
    sed -E -i '' \
        -e 's/^(local[[:space:]].*)trust[[:space:]]*$/\1peer/' \
        -e 's/^(host[[:space:]].*)trust[[:space:]]*$/\1scram-sha-256/' "$HBA"
    psql_admin -c "SELECT pg_reload_conf()" >/dev/null
    ok "pg_hba.conf: trust заменён на peer/scram-sha-256 (копия сохранена рядом)"
else
    ok "pg_hba.conf уже без trust"
fi

# ---------------------------------------------------------------------------
step "8. Ollama"
# ---------------------------------------------------------------------------
as_admin "$BREW" services stop ollama >/dev/null 2>&1 || true
install_daemon ru.school-ai.ollama

for _ in $(seq 1 30); do
    curl -sf http://127.0.0.1:11434/api/version >/dev/null && break
    sleep 1
done
curl -sf http://127.0.0.1:11434/api/version >/dev/null || die "Ollama не запустилась, см. $ROOT/logs/ollama-error.log"
ok "Ollama $("$OLLAMA" --version 2>/dev/null | awk '{print $NF}') отвечает"

if [[ $DO_MODELS -eq 1 ]]; then
    chat_model="$(env_value OLLAMA_CHAT_MODEL)"
    embed_model="$(env_value OLLAMA_EMBED_MODEL)"
    for model in "${chat_model:-qwen3:14b}" "${embed_model:-qwen3-embedding:4b}"; do
        if OLLAMA_HOST=127.0.0.1:11434 "$OLLAMA" list | awk 'NR>1 {print $1}' | grep -x "$model" >/dev/null; then
            ok "модель $model уже скачана"
        else
            echo "    скачиваю $model (это может занять время)..."
            OLLAMA_HOST=127.0.0.1:11434 "$OLLAMA" pull "$model"
            ok "модель $model скачана"
        fi
    done
fi

# ---------------------------------------------------------------------------
step "9. Схема БД"
# ---------------------------------------------------------------------------
as_service "$ROOT/.venv/bin/school-ai" init-db
ok "таблицы созданы/обновлены"

# ---------------------------------------------------------------------------
step "10. Страница «Документы», API и служба индексации"
# ---------------------------------------------------------------------------
for log in web web-error indexer indexer-error; do
    touch "$ROOT/logs/$log.log"
    chown "$SERVICE_USER":staff "$ROOT/logs/$log.log"
done
install_daemon ru.school-ai.web restart
# Служба индексации при запуске сама проверяет всю папку документов
install_daemon ru.school-ai.indexer restart


# ---------------------------------------------------------------------------
step "11. Open WebUI $OWUI_VERSION (интерфейс для пользователей)"
# ---------------------------------------------------------------------------
if [[ ! -x "$OWUI/.venv/bin/python" ]]; then
    as_service "$PYTHON_OWUI" -m venv "$OWUI/.venv"
    ok "создано $OWUI/.venv"
fi
installed="$(as_service "$OWUI/.venv/bin/pip" show open-webui 2>/dev/null | awk '/^Version:/ {print $2}' || true)"
if [[ "$installed" != "$OWUI_VERSION" ]]; then
    echo "    устанавливаю open-webui==$OWUI_VERSION (несколько минут)..."
    as_service "$OWUI/.venv/bin/pip" install -q --upgrade pip
    as_service "$OWUI/.venv/bin/pip" install -q "open-webui==$OWUI_VERSION"
    ok "open-webui $OWUI_VERSION установлен"
else
    ok "open-webui $OWUI_VERSION уже установлен"
fi

OWUI_ENV="$ROOT/config/open-webui.env"
API_KEY="$(env_value API_KEY)"
if [[ ! -f "$OWUI_ENV" ]]; then
    sed -e "s/__WEBUI_SECRET_KEY__/$(openssl rand -hex 32)/" -e "s/__API_KEY__/$API_KEY/" \
        "$SRC/config/open-webui.env.example" > "$OWUI_ENV"
    ok "создан $OWUI_ENV"
else
    # ключ API всегда совпадает с .env
    sed -i '' "s/^OPENAI_API_KEYS=.*/OPENAI_API_KEYS=$API_KEY/" "$OWUI_ENV"
    # новые параметры из шаблона, которых ещё нет в файле, — дописываем
    while IFS= read -r line; do
        key="${line%%=*}"
        if ! grep -qE "^$key=" "$OWUI_ENV"; then
            printf '%s\n' "$line" >> "$OWUI_ENV"
            ok "добавлен параметр $key"
        fi
    done < <(grep -E '^[A-Z_]+=' "$SRC/config/open-webui.env.example" | grep -vE '^(WEBUI_SECRET_KEY|OPENAI_API_KEYS)=')
    ok "$OWUI_ENV уже существует — обновлены ключ API и новые параметры"
fi
# адреса сервера для CORS (меняются при переезде в другую сеть)
ok "адреса для браузера: $(bash "$SRC/deploy/update-cors.sh" | tr ';' ' ')"
chown "$SERVICE_USER":staff "$OWUI_ENV"
chmod 600 "$OWUI_ENV"

touch "$ROOT/logs/open-webui.log" "$ROOT/logs/open-webui-error.log"
chown "$SERVICE_USER":staff "$ROOT/logs/open-webui.log" "$ROOT/logs/open-webui-error.log"
install_daemon ru.school-ai.open-webui restart

owui_port="$(grep -E '^PORT=' "$OWUI_ENV" | cut -d= -f2 || true)"; owui_port="${owui_port:-8080}"
owui_host="$(grep -E '^HOST=' "$OWUI_ENV" | cut -d= -f2 || true)"
echo "    жду запуска Open WebUI (первый запуск — до пары минут)..."
for _ in $(seq 1 180); do
    curl -sf "http://127.0.0.1:$owui_port/health" >/dev/null 2>&1 && break
    sleep 1
done
if curl -sf "http://127.0.0.1:$owui_port/health" >/dev/null 2>&1; then
    ok "Open WebUI отвечает на порту $owui_port (HOST=$owui_host)"
else
    warn "Open WebUI не ответил, см. $ROOT/logs/open-webui-error.log"
fi

# ---------------------------------------------------------------------------
step "Сетевой фильтр"
# ---------------------------------------------------------------------------
touch "$ROOT/logs/firewall.log"
install_daemon ru.school-ai.firewall
if [[ -f "$ROOT/config/firewall.on" ]]; then
    ok "включён: сервисам закрыт выход в интернет (включится в конце установки)"
else
    warn "выключен. Рекомендуется: deploy/firewall.sh on (на вашем компьютере)"
fi

# ---------------------------------------------------------------------------
if [[ -n "$REINDEX" ]]; then
    step "12. Полная переиндексация документов"
    as_service "$ROOT/.venv/bin/school-ai" index --force
fi

# ---------------------------------------------------------------------------
step "Проверка"
# ---------------------------------------------------------------------------
as_service "$ROOT/.venv/bin/school-ai" check

WEB_PORT="$(env_value WEB_PORT)"; WEB_PORT="${WEB_PORT:-8000}"
web_status() { curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$WEB_PORT/api/health" 2>/dev/null || true; }
for _ in $(seq 1 15); do
    [[ "$(web_status)" =~ ^(200|401)$ ]] && break
    sleep 1
done
if [[ "$(web_status)" =~ ^(200|401)$ ]]; then
    ok "веб-интерфейс: http://127.0.0.1:$WEB_PORT (с вашего компьютера — через deploy/tunnel.sh)"
else
    warn "веб-интерфейс не отвечает, см. $ROOT/logs/web-error.log"
fi

printf '\n\033[1;32mГотово.\033[0m Open WebUI: deploy/tunnel.sh на вашем компьютере → http://localhost:%s\n' "$owui_port"
