## Why

Бот разворачивается на VPS вручную, без воспроизводимого процесса: нет команды
выкатки, нет регулярного бэкапа SQLite-базы и единого места для логов. Это риск
потери данных и человеческих ошибок при каждом обновлении. На том же VPS уже
работает сервис `guitar-for-everyone` с отлаженным процессом деплоя/бэкапа —
повторяем его модель для `logoust_assistant`.

## What Changes

- Команда `make deploy` (`scripts/deploy.sh`): rsync кода на VPS → `uv sync --no-dev`
  → `alembic upgrade head` → раскатка systemd-юнита и cron-файла из `deploy/` →
  `daemon-reload` → рестарт сервиса → пауза → `systemctl is-active`.
- Команда `make backup-prod` (`scripts/pull_backups.sh`): скачивание серверных бэкапов БД
  в локальный `backups/vps/`.
- Команда `make backup`: снимок локальной `logoust.db` в `backups/`.
- Команда `make create-invite-prod` (`scripts/create_invite_prod.sh`): создание приглашения
  специалиста на VPS с выводом deep-link прод-бота.
- Регулярный бэкап на VPS: `scripts/backup_db.sh` по cron в 03:45 UTC, `sqlite3 .backup`
  (консистентная онлайн-копия), ретеншен daily 7 дней / weekly ~месяц. Время 03:45
  выбрано чтобы не пересекаться с бэкапом gfe (03:30).
- Логи бота — в systemd journal (persistent, как у gfe): `LOG_FORMAT=json`,
  `LOG_FILE_ENABLED=false`. Отдельных лог-файлов нет.
- Серверные конфиги как шаблоны в репо: `deploy/logoust-assistant.service`,
  `deploy/logoust-assistant-backup.cron` — раскатываются деплоем, видны в git.
- Новый `docs/deployment.md`: раскладка на сервере, юнит, cron, journald, чек-лист
  первичной настройки (юзер, каталоги, `.env`, `uv`, SSH-алиас `logoust`), нюанс
  409-конфликта long-polling.
- Обновление `README.md`: команды `make deploy`/`make backup`/`make backup-prod`/
  `make create-invite-prod`, требование SSH-алиаса `logoust`.

Сознательно НЕ вводим (бот на long-polling не использует входящих соединений):
reverse proxy, TLS, открытие портов в firewall, HTTP health-check, build-stamp
фронтенда. Хардненинг VPS (UFW/fail2ban/unattended-upgrades/persistent journald)
уже настроен под gfe и переиспользуется как есть.

## Capabilities

### New Capabilities
- `deployment`: воспроизводимая выкатка бота на VPS, регулярный бэкап SQLite-базы с
  ретеншеном, скачивание бэкапов на локальную машину, сбор логов в journald.

### Modified Capabilities
<!-- Нет: меняется операционный контур, поведение существующих фич не затрагивается. -->

## Impact

- **Новые файлы:** `scripts/deploy.sh`, `scripts/pull_backups.sh`, `scripts/backup_db.sh`,
  `scripts/create_invite_prod.sh`, `deploy/logoust-assistant.service`,
  `deploy/logoust-assistant-backup.cron`, `docs/deployment.md`.
- **Изменяемые файлы:** `Makefile` (цели `deploy`/`backup`/`backup-prod`/
  `create-invite-prod`, переименование `create_invite` → `create-invite`), `README.md`,
  `docs/README.md` (индекс).
- **Зависимости:** новых Python-зависимостей нет. На VPS требуется `sqlite3` CLI и `rsync`.
- **Конфиг прода (на сервере, не в репо):** `.env` с абсолютным
  `DATABASE_URL=sqlite+aiosqlite:////var/lib/logoust-assistant/app.db`, `LOG_FORMAT=json`.
- **Локальная машина:** SSH-алиас `logoust` в `~/.ssh/config` (тот же VPS 72.56.39.20,
  root, ключ `~/.ssh/gfe`).
- **Код приложения (`src/`) не меняется** — изменение чисто операционное.
