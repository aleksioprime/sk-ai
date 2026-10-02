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

Системные сервисы стартуют автоматически при включении Mac, без входа
в систему:

| Сервис | Что это | Адрес |
|---|---|---|
| `ru.school-ai.postgresql` | база данных | `127.0.0.1:5432` |
| `ru.school-ai.ollama` | языковые модели | `127.0.0.1:11434` |
| `ru.school-ai.web` | страница «Документы», проверка ответов, API для Open WebUI | `127.0.0.1:8000` |
| `ru.school-ai.indexer` | служба индексации: следит за `/opt/school-ai/data` | — |
| `ru.school-ai.open-webui` | Open WebUI — чат для пользователей | `127.0.0.1:8080` |
| `ru.school-ai.caddy` | вход из сети школы (только после `deploy/network.sh open`) | порт `80` |
| `ru.school-ai.firewall` | сетевой фильтр (срабатывает один раз при загрузке) | — |

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
scp -i ~/.ssh/school-ai -o HostKeyAlias=school-ai local/backups/school_ai-20261002-1200.dump asemochkin@school-ai.local:
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

Open WebUI и «Документы» всегда слушают только сам сервер (`127.0.0.1`).
В сеть школы их выводит **Caddy** — один вход на обычном порту 80:

```
http://school-ai.local/             → чат (Open WebUI, 127.0.0.1:8080)
http://school-ai.local/documents/   → «Документы» (127.0.0.1:8000)
```

Сразу после установки Caddy выключен, и всё доступно только через SSH-туннель
(`deploy/tunnel.sh`). **Сначала зарегистрируйте администратора** (см.
`01-installation.md`, «Первый вход»), затем на своём компьютере:

```bash
deploy/network.sh open
```

Скрипт проверит доступ и покажет адреса для сотрудников — по имени и по IP:

| Что | Адрес |
|---|---|
| Чат (Open WebUI) | `http://school-ai.local` или `http://<IP сервера>` |
| Документы | `http://school-ai.local/documents/` или `http://<IP сервера>/documents/` |

Имя `school-ai.local` работает в пределах одной сети (подсети) на macOS,
Windows 10/11 и iOS. Если у части сотрудников оно не открывается —
давайте им адрес по IP.

Закрыть доступ обратно: `deploy/network.sh close` (Caddy останавливается).
Проверить: `deploy/network.sh status`. Настройка Caddy — `deploy/Caddyfile`,
журнал — `/opt/school-ai/logs/caddy.log`.

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
  можно добавить баннер со ссылкой на страницу «Документы»
  (`/documents/` — на том же адресе, что и чат).
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

## 3.8. Переезд в другую сеть (например, в гимназию)

После переезда у Mac Studio сменится IP-адрес. Скрипты на вашем компьютере
подключаются по **имени** `school-ai.local`, поэтому обычно ничего менять
не нужно.

**До переезда** — свежая резервная копия:

```bash
deploy/backup.sh
```

**После переезда:**

1. Подключите Mac Studio к сети (лучше кабелем) и к питанию — он включится сам.
2. Подключите MacBook к той же сети и проверьте связь:

   ```bash
   deploy/ssh.sh hostname
   ```

   Должно вывести `school-ai`. Если не находит — узнайте IP сервера
   (у ИТ-службы, в роутере или на самом Mac: Системные настройки → Сеть) и
   создайте файл `deploy/config.local.sh` (в git не попадает):

   ```bash
   echo 'SERVER_HOST=10.1.2.3' > deploy/config.local.sh
   ```

   Разово можно и так: `SCHOOL_AI_HOST=10.1.2.3 deploy/ssh.sh hostname`.

3. Обновите адреса в настройках Open WebUI (CORS) — иначе чат в браузере
   по новому адресу не заработает:

   ```bash
   deploy/network.sh open
   ```

   (или `deploy/sync.sh`, если доступ из сети пока не нужен).
   Скрипт покажет новые адреса для сотрудников.

4. Проверьте сетевой фильтр: `deploy/firewall.sh test`.

Предупреждения SSH о «смене ключа» не будет: ключ сервера проверяется под
постоянным именем `school-ai` (параметр `HostKeyAlias` в `deploy/config.sh`),
а не под IP-адресом.

**Что попросить у ИТ-службы гимназии:**

* закрепить за Mac Studio постоянный IP (резервирование в DHCP) — тогда адрес
  для сотрудников не будет меняться;
* по возможности — DNS-имя в школьной сети (например, `ai.gymnasium.local`);
* разрешить внутри сети доступ к порту **80** (вход в School AI), SSH (**22**) —
  только с компьютера администратора;
* **не** пробрасывать эти порты в интернет.

**Важно:** сетевой фильтр (`deploy/firewall.sh`) разрешает сервисам соединения
только с частными сетями: `10.x.x.x`, `172.16–31.x.x`, `192.168.x.x`. Почти все
школьные сети такие. Если сеть гимназии использует другие адреса, сотрудники
не смогут открыть интерфейс при включённом фильтре — тогда добавьте диапазон
в `deploy/pf-school-ai.conf` и выполните `deploy/sync.sh`.

**Удалённая работа из дома** после переезда возможна только через VPN в сеть
гимназии (если ИТ-служба его предоставляет). Открывать SSH или интерфейсы в
интернет не нужно.
