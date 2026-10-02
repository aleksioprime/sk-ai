# 3. Обслуживание

## 3.1. Где что лежит на сервере

```
/opt/school-ai/                  (владелец service, права 750)
├── app/                         код (копия репозитория)
├── .venv/                       Python-окружение School AI (Python 3.13)
├── config/
│   ├── .env                     настройки School AI, пароль БД, ключ API (права 600)
│   └── open-webui.env           настройки Open WebUI (права 600)
├── data/                        ДОКУМЕНТЫ — главная копия
├── open-webui/
│   ├── .venv/                   окружение Open WebUI (Python 3.12)
│   └── data/                    пользователи, чаты, настройки Open WebUI (webui.db)
├── logs/                        журналы сервисов
└── models/ollama/               модели Ollama (~13 ГБ)

/opt/homebrew/var/postgresql@17  данные PostgreSQL
/Library/LaunchDaemons/ru.school-ai.*.plist   системные сервисы
/usr/local/bin/school-ai         команда school-ai
```

## 3.2. Сервисы

Пять системных сервисов стартуют автоматически при включении Mac, без входа
в систему:

| Сервис | Что это | Адрес |
|---|---|---|
| `ru.school-ai.postgresql` | база данных | `127.0.0.1:5432` |
| `ru.school-ai.ollama` | языковые модели | `127.0.0.1:11434` |
| `ru.school-ai.web` | страница «Документы», проверка ответов, API для Open WebUI | порт `8000` |
| `ru.school-ai.indexer` | служба индексации: следит за `/opt/school-ai/data` | — |
| `ru.school-ai.open-webui` | Open WebUI — чат для пользователей | порт `8080` |

Состояние:

```bash
sudo launchctl list | grep school-ai
```

В первой колонке — номер процесса (значит, работает), во второй — код
последнего завершения (`0` — норма).

Перезапуск (например, Open WebUI):

```bash
sudo launchctl kickstart -k system/ru.school-ai.open-webui
```

Остановить / запустить:

```bash
sudo launchctl bootout system/ru.school-ai.open-webui
```

```bash
sudo launchctl bootstrap system /Library/LaunchDaemons/ru.school-ai.open-webui.plist
```

## 3.3. Журналы (логи)

```bash
tail -f /opt/school-ai/logs/open-webui-error.log
```

| Файл | Что там |
|---|---|
| `open-webui.log`, `open-webui-error.log` | Open WebUI (основной журнал — в `-error.log`) |
| `web.log`, `web-error.log` | страница «Документы» и API: вопросы от Open WebUI, ошибки |
| `indexer-error.log` | служба индексации: какие файлы проиндексированы, ошибки разбора |
| `ollama.log`, `ollama-error.log` | работа моделей (Ollama пишет основной журнал в `-error.log`) |
| `postgresql-error.log` | журнал БД |

Логи растут медленно; если файл стал большим — его можно очистить:

```bash
sudo truncate -s 0 /opt/school-ai/logs/ollama-error.log
```

**Журнал вопросов.** Каждый вопрос к «📘 Документы школы», ответ, источники,
время обработки и имя пользователя Open WebUI сохраняются в таблицу
`rag_queries`. Последние 10 вопросов:

```bash
/opt/homebrew/opt/postgresql@17/bin/psql -h /tmp school_ai -c "SELECT created_at, user_name, question, timings->>'total' AS sec FROM rag_queries ORDER BY id DESC LIMIT 10"
```

## 3.4. Резервные копии

На вашем компьютере:

```bash
deploy/backup.sh
```

| Что | Куда |
|---|---|
| документы `/opt/school-ai/data` | `local/data/` (точная копия) |
| база School AI (PostgreSQL) | `local/backups/school_ai-ДАТА.dump` |
| Open WebUI: пользователи, чаты, настройки | `local/backups/open-webui-ДАТА.db` |

Хранятся 10 последних копий баз. Делайте копию регулярно — особенно перед
обновлением Open WebUI и после загрузки новых документов.

### Восстановление

**Документы** — с компьютера на сервер:

```bash
deploy/push-data.sh --mirror
```

**База School AI** — её всегда можно построить заново из документов
(`school-ai index --force`). Копия нужна прежде всего для журнала вопросов:

```bash
scp -i ~/.ssh/school-ai local/backups/school_ai-20261002-1200.dump asemochkin@192.168.2.101:
```

```bash
/opt/homebrew/opt/postgresql@17/bin/pg_restore -h /tmp -d school_ai --clean --if-exists --no-owner --role=school_ai ~/school_ai-20261002-1200.dump
```

**Open WebUI** (на сервере, после копирования файла через `scp`):

```bash
sudo launchctl bootout system/ru.school-ai.open-webui
```

```bash
sudo install -o service -g staff -m 600 ~/open-webui-20261002-1200.db /opt/school-ai/open-webui/data/webui.db
```

```bash
sudo launchctl bootstrap system /Library/LaunchDaemons/ru.school-ai.open-webui.plist
```

## 3.5. Доступ из сети школы

Сразу после установки Open WebUI и «Документы» доступны только через
SSH-туннель (`deploy/tunnel.sh`). **Сначала зарегистрируйте администратора**
(см. `01-installation.md`, «Первый вход»), затем на своём компьютере:

```bash
deploy/network.sh open
```

Адреса для сотрудников:

| Что | Адрес |
|---|---|
| Чат (Open WebUI) | `http://192.168.2.101:8080` |
| Документы | `http://192.168.2.101:8000` |

Закрыть доступ обратно: `deploy/network.sh close`. Проверить: `deploy/network.sh status`.

**Вход** на обе страницы — по учётным записям Open WebUI. Страница «Документы»
отдельного пароля не имеет: она проверяет сессию Open WebUI на том же адресе.
Права: администраторы Open WebUI загружают и удаляют файлы, остальные
одобренные пользователи — только смотрят и скачивают.

Сотрудники регистрируются в Open WebUI сами и ждут одобрения администратора
(**Панель администратора → Пользователи**). Если регистрация не нужна —
отключите её: **Панель администратора → Настройки → Общие → Разрешить регистрацию**,
и создавайте учётные записи вручную.

**Рекомендуемые настройки в панели администратора Open WebUI:**

* **Настройки → Модели**: для `school-docs` задайте понятное название и
  описание; модель `qwen3-embedding:4b` скройте (она не для чата).
* **Настройки → Интерфейс**: модель по умолчанию — `school-docs`;
  можно добавить баннер со ссылкой на страницу «Документы».
* **Группы и права**: при необходимости ограничьте доступ к свободному чату
  `qwen3:14b`, оставив только «Документы школы».

> Соединение идёт по HTTP без шифрования — это допустимо только внутри
> школьной сети. Для доступа из интернета нужен HTTPS (отдельная задача).

> API `/v1` на порту 8000 защищён ключом `API_KEY` — им пользуется только
> Open WebUI. База данных и Ollama в сеть не открываются никогда.

## 3.6. Смена модели

Модель задаётся в `.env`. Например, попробовать более сильную MoE-модель
`qwen3:30b-a3b` (~19 ГБ, отвечает быстрее 14b за счёт архитектуры):

```bash
OLLAMA_HOST=127.0.0.1:11434 ollama pull qwen3:30b-a3b
```

```bash
sudo -u service nano /opt/school-ai/config/.env      # OLLAMA_CHAT_MODEL=qwen3:30b-a3b
```

```bash
sudo launchctl kickstart -k system/ru.school-ai.web
```

```bash
school-ai eval
```

Сравните результат `eval` с прежней моделью. Смена **модели генерации**
переиндексации не требует.

Смена **embedding-модели** или `EMBED_DIMENSIONS` требует пересоздания векторов:

```bash
/opt/homebrew/opt/postgresql@17/bin/psql -h /tmp school_ai -c "DROP INDEX IF EXISTS idx_chunks_embedding_hnsw; ALTER TABLE document_chunks ALTER COLUMN embedding TYPE vector(НОВАЯ_РАЗМЕРНОСТЬ) USING NULL;"
```

```bash
school-ai init-db && school-ai index --force
```

## 3.7. Типичные проблемы

| Симптом | Что проверить |
|---|---|
| `school-ai check`: Ollama недоступна | `sudo launchctl kickstart -k system/ru.school-ai.ollama`, лог `ollama-error.log` |
| `school-ai check`: PostgreSQL — ошибка пароля | пароль в `.env` не совпадает с ролью: запустите `deploy/sync.sh` — `install.sh` синхронизирует пароль |
| Ответ идёт очень долго | `ollama ps` — модель должна быть `100% GPU`; первая загрузка модели после перезагрузки ~10–20 с |
| «В загруженных документах не найдено…», хотя ответ есть | `school-ai ask --debug "…"` — посмотреть, что нашёл поиск; `school-ai parse файл` — распознались ли пункты |
| Ответ без номеров пунктов | документ без распознанной нумерации (скан, PDF без номеров, `.doc` через textutil) — загрузите DOCX |
| Open WebUI: нет модели «school-docs» | `curl` к API: `sudo launchctl kickstart -k system/ru.school-ai.web`; ключ `API_KEY` в `.env` и `OPENAI_API_KEYS` в `open-webui.env` должны совпадать — их синхронизирует `deploy/sync.sh` |
| Open WebUI: «Network error» или долго нет ответа | лог `web-error.log`; ответ по документам занимает 5–25 с — это нормально |
| После перезагрузки ничего не работает | включён FileVault? Mac ждёт пароль на экране |
| Интерфейс не открывается через туннель | `sudo launchctl list \| grep school-ai`, логи `open-webui-error.log`, `web-error.log` |
