## Context

`logoust_assistant` — Telegram-бот на aiogram (long-polling, `python -m src`),
хранилище — SQLite (`logoust.db`), логирование — structlog. Деплоя как процесса нет.

На том же VPS (`72.56.39.20`, Ubuntu, root по ключу) уже работает веб-сервис
`guitar-for-everyone` с отлаженной моделью: `scripts/deploy.sh` (rsync + uv + alembic +
restart), `scripts/backup_db.sh` (sqlite3 .backup по cron, daily/weekly), логи в journald.
Эталон описан в `~/Projects/guitar-for-everyone/docs/deployment.md`. Мы повторяем эту
модель, но gfe — веб-приложение, а logoust — polling-бот, поэтому часть контура отпадает.

## Goals / Non-Goals

**Goals:**

- `make deploy` — воспроизводимая выкатка бота на VPS одной командой.
- `make backup-prod` — скачивание серверных бэкапов БД на локальную машину.
- `make backup` — датированный снимок локальной БД.
- `make create-invite-prod` — создание приглашения специалиста на VPS (deep-link прод-бота).
- Регулярный бэкап БД на VPS по cron с ретеншеном daily/weekly.
- Логи бота в persistent journald (как у gfe).
- Серверные конфиги (systemd-юнит, cron) воспроизводимы из git (`deploy/`).

**Non-Goals:**

- Reverse proxy, TLS, открытие портов в firewall — бот на long-polling не принимает
  входящих соединений.
- HTTP health-check и build-stamp фронтенда — у бота нет HTTP-слоя и статики.
- Хардненинг VPS (UFW/fail2ban/unattended-upgrades/persistent journald) — уже настроен
  под gfe, переиспользуется как есть, в scope не входит.
- CI/CD-автоматизация деплоя — выкатка ручная, локально из рабочей копии (YAGNI).
- Zero-downtime деплой — рестарт сервиса допускает секунды простоя (см. риск 409).

## Decisions

**Раскладка на сервере (зеркало gfe).**

| Путь | Что |
| --- | --- |
| `/opt/logoust-assistant/` | Код (сюда rsync), + прод-`.env` |
| `/var/lib/logoust-assistant/app.db` | SQLite вне каталога кода — `rsync --delete` её не трогает |
| `/var/backups/logoust-assistant/{daily,weekly}/` | Бэкапы БД |
| user `logoust`, `uv` в `/home/logoust/.local/bin` | Непривилегированный системный юзер |

Почему БД вне каталога кода: `deploy.sh` использует `rsync --delete`, и держать базу
внутри `/opt/...` означало бы риск её удаления при рассинхроне исключений. Абсолютный
`DATABASE_URL` в прод-`.env` разводит код и данные физически.

**deploy.sh = gfe минус веб-специфика.** Берём gfe-скрипт, убираем стамп `sw.js` и
`curl`-health-check (нет HTTP). Health-check сводится к `systemctl is-active` после паузы.
Добавляем шаг раскатки `deploy/logoust-assistant.service` и `deploy/...-backup.cron` в
системные пути + `systemctl daemon-reload` — это делает серверный конфиг воспроизводимым
из git (улучшение относительно gfe, где юнит/cron живут только на сервере).

**Деление провижининга.** `deploy.sh` идемпотентно раскатывает юнит+cron на каждом
прогоне (подхватывает их изменения). Одноразовая первичная настройка (создание юзера
`logoust`, каталогов `/var/lib` и `/var/backups`, прод-`.env`, установка `uv`,
`systemctl enable`, SSH-алиас `logoust`) — руками по чек-листу в `docs/deployment.md`.
Альтернатива (полный `provision.sh`) отвергнута как YAGNI: первичная настройка одноразовая.

**Бэкап.** `scripts/backup_db.sh` (зеркало gfe): `sqlite3 .backup` даёт консистентную
онлайн-копию под нагрузкой; gzip; daily хранится 7 дней, по воскресеньям копия
продвигается в weekly (~месяц). Cron в **03:45 UTC** — сдвиг от gfe (03:30), чтобы два
бэкапа не конкурировали за IO. Скрипт едет на сервер вместе с кодом и вызывается из
`/etc/cron.d/logoust-assistant-backup`.

**Логи — journald, не файлы.** В коде уже есть опциональный файловый лог
(`LOG_FILE_ENABLED`), но в проде ставим `LOG_FILE_ENABLED=false`, `LOG_FORMAT=json`:
structlog пишет JSON в stdout, systemd собирает в persistent journal. Единообразно с gfe,
ротация и ретеншен — забота journald, а не приложения. Изменений в `src/` не требуется.

**SSH-алиас `logoust`.** Тот же VPS, что и gfe, но отдельный `Host logoust` в
`~/.ssh/config` (→ `72.56.39.20`, root, ключ `~/.ssh/gfe`). Скрипты используют
`SSH_HOST="logoust"`. Семантически чище переиспользования алиаса `gfe` для другого сервиса.

## Risks / Trade-offs

- **409-конфликт long-polling** → Telegram отдаёт `getUpdates` только одному клиенту;
  две копии бота одновременно держать нельзя. Митигация: деплой = `systemctl restart`
  (стоп старого → старт нового), `is-active` проверяется после `sleep`, а не сразу.
- **Нет полноценного health-check «бот принимает апдейты»** → `systemctl is-active`
  подтверждает только, что процесс жив. Митигация: при сомнениях смотреть
  `journalctl -u logoust-assistant -f` на стартовые строки; полноценную проверку не
  вводим (YAGNI).
- **`rsync --delete` при ошибке в списке исключений может удалить лишнее на сервере** →
  Митигация: БД и `.env` физически вне каталога кода; список исключений зеркалит
  проверенный gfe.
- **Cron-коллизия по IO с gfe** → Митигация: разнесены по времени (03:30 vs 03:45).
- **Первичная настройка вручную → дрейф от документации** → Митигация: чек-лист в
  `docs/deployment.md`; повторяемая часть (юнит/cron) раскатывается деплоем из git.

## Migration Plan

1. Первичная настройка VPS по чек-листу `docs/deployment.md` (юзер, каталоги, `.env`,
   `uv`, `enable` сервиса), SSH-алиас `logoust` локально.
2. Первый `make deploy` раскатывает код, юнит, cron и запускает сервис.
3. Проверка: `journalctl -u logoust-assistant -f`, дождаться первого ночного бэкапа,
   затем `make backup-prod` и убедиться, что снимки скачиваются.
4. Откат: сервис останавливается (`systemctl stop`), при необходимости БД
   восстанавливается из последнего снимка `/var/backups/logoust-assistant/daily/`.
