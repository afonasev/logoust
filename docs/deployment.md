# Деплой и продакшн

Как бот развёрнут на VPS. Команды деплоя и бэкапа — в корневом `README.md`,
здесь — устройство сервера и чек-лист первичной настройки.

## Где работает

- **VPS:** `72.56.39.20`, Ubuntu, доступ по SSH под `root` (только по ключу).
  Тот же сервер, что и `guitar-for-everyone`, но отдельный сервис.
- SSH-алиас `logoust` в `~/.ssh/config` — на него настроены `scripts/deploy.sh`
  и `scripts/pull_backups.sh`.

```
Host logoust
    HostName 72.56.39.20
    User root
    IdentityFile ~/.ssh/gfe
```

## Раскладка на сервере

| Путь | Что |
| --- | --- |
| `/opt/logoust-assistant` | Код приложения (сюда rsync'ит `deploy.sh`) |
| `/opt/logoust-assistant/.env` | Конфиг прода (абсолютный `DATABASE_URL`, `LOG_FORMAT=json`, `LOG_FILE_ENABLED=false`) |
| `/var/lib/logoust-assistant/app.db` | SQLite-база (вне каталога кода, чтобы `rsync --delete` её не трогал) |
| `/var/backups/logoust-assistant/` | Бэкапы БД: `daily/` и `weekly/` |

Процесс работает под непривилегированным системным пользователем `logoust`
(не под `root`). `uv` установлен в `/home/logoust/.local/bin`.

База лежит **вне** каталога кода: `deploy.sh` использует `rsync --delete`, и
держать базу внутри `/opt/...` означало бы риск её удаления при рассинхроне
списка исключений. Абсолютный `DATABASE_URL` в прод-`.env` разводит код и данные
физически:

```
DATABASE_URL=sqlite+aiosqlite:////var/lib/logoust-assistant/app.db
```

## Сервис приложения

systemd-юнит `logoust-assistant.service` (шаблон в репо:
`deploy/logoust-assistant.service`): запускает `uv run --no-dev python -m src`
(long-polling) под пользователем `logoust`, с автозапуском и `Restart=on-failure`.

```bash
systemctl status logoust-assistant
systemctl restart logoust-assistant
```

## Reverse proxy, TLS, firewall — НЕ нужны

Бот работает на long-polling: он сам ходит к Telegram за апдейтами и не принимает
входящих соединений. Поэтому reverse proxy, TLS-сертификат, открытие портов в
firewall и HTTP health-check для него не нужны (в отличие от веб-сервиса gfe на
том же VPS). Базовый хардненинг сервера (UFW, fail2ban, unattended-upgrades,
persistent journald) уже настроен под gfe и переиспользуется как есть.

## Бэкапы БД

`scripts/backup_db.sh` запускается на VPS по cron ежедневно в **03:45 UTC**
(`/etc/cron.d/logoust-assistant-backup`, шаблон в репо:
`deploy/logoust-assistant-backup.cron`). Время сдвинуто от бэкапа gfe (03:30),
чтобы два бэкапа не конкурировали за IO. Использует `sqlite3 .backup` —
консистентная онлайн-копия, безопасная под нагрузкой.

- `daily/` — ежедневный снимок, хранится 7 дней.
- `weekly/` — по воскресеньям копия дневного снимка, хранится ~месяц.

Скачать бэкапы локально: `make backup-prod` (→ `backups/vps/`).

## Логи

Всё пишется в systemd journal (персистентный), отдельных лог-файлов нет.
В проде `LOG_FORMAT=json` и `LOG_FILE_ENABLED=false`: structlog пишет JSON в
stdout, systemd собирает в journal.

```bash
journalctl -u logoust-assistant -f   # приложение (structlog, JSON)
journalctl -t CRON                   # бэкап-cron
```

## Процесс деплоя

`make deploy` (= `scripts/deploy.sh`):

1. `rsync` кода в `/opt/logoust-assistant` (исключая `.git`, `.venv`, БД, `.env`,
   `backups`, `logs`, `data`, кэши).
2. `chown -R logoust:logoust` каталога кода.
3. `uv sync --no-dev` — установка рантайм-зависимостей.
4. `alembic upgrade head` — миграции.
5. Раскатка `deploy/logoust-assistant.service` → `/etc/systemd/system/` и
   `deploy/logoust-assistant-backup.cron` → `/etc/cron.d/`, затем `daemon-reload`.
   Так серверный конфиг воспроизводим из git.
6. `systemctl restart` → `sleep 3` → `systemctl is-active`.

`.env` и база на сервере деплоем не затрагиваются. Любой упавший шаг прерывает
скрипт с ненулевым кодом (`set -euo pipefail`).

## Создание приглашения специалиста на проде

`make create-invite-prod` (= `scripts/create_invite_prod.sh`) запускает CLI
`src.cli.create_invite` на VPS под пользователем `logoust` против прод-БД и
прод-`.env`, поэтому deep-link сразу содержит username прод-бота. Заходить на
сервер руками не нужно. В stdout печатается только ссылка вида
`https://t.me/<bot>?start=<token>`.

### Нюанс: 409-конфликт long-polling

Telegram отдаёт `getUpdates` только одному клиенту: две копии бота одновременно
держать нельзя. Поэтому деплой — это `systemctl restart` (стоп старого → старт
нового), а `is-active` проверяется после паузы, а не сразу. `is-active`
подтверждает только что процесс жив; при сомнениях смотри стартовые строки в
`journalctl -u logoust-assistant -f`.

## Блокировка IP Telegram провайдером

Провайдер этого VPS (Москва) **выборочно режет IP-диапазоны Telegram** на 443.
DNS отдаёт для `api.telegram.org` адрес из заблокированного диапазона → бот виснет
на `get_me()`, падает с кодом 1 и крашлупится (`Restart=on-failure`). Симптом —
в логах `aiogram.exceptions.TelegramNetworkError: Request timeout error` и растущий
счётчик `NRestarts`.

Диагностика прямо с сервера:

```bash
# рабочий ли выход вообще (должен ответить быстро):
curl -4 -s -o /dev/null -w '%{http_code} %{time_total}s\n' --max-time 12 https://1.1.1.1/
# какой IP отдаёт DNS и доступен ли он на 443:
getent ahosts api.telegram.org
curl -4 -s -o /dev/null -w '%{http_code} %{time_total}s\n' --max-time 12 https://api.telegram.org/
```

Два способа обхода (по возрастанию надёжности):

### Быстрый разблок: пин рабочего IP в `/etc/hosts`

Среди IP Telegram часть остаётся открытой. Найти живой и запинить на него имя:

```bash
echo '149.154.167.220 api.telegram.org' >> /etc/hosts
systemctl restart logoust-assistant.service
```

**Хрупко:** это ручное состояние сервера (а `/etc/hosts` тут под управлением
Ansible — следующий прогон может его перетереть), и конкретный IP могут закрыть
позже. Годится как аварийная мера, не как постоянное решение.

### Надёжно: прокси через `TELEGRAM_PROXY_URL`

Бот умеет ходить к Bot API через прокси (см. `src/bot/bot_factory.py`). Прокси
сам резолвит `api.telegram.org` со своей (нефильтрующей) стороны, поэтому смена
или блокировка IP Telegram бот больше не ломает. Включается переменной в
прод-`.env`:

```
TELEGRAM_PROXY_URL=socks5://user:pass@proxy-host:1080
```

Схемы: `http` / `socks4` / `socks5` (через `aiohttp-socks`). Нужна внешняя точка
выхода, не фильтрующая Telegram (свой VPS за рубежом с SOCKS, VPN-прокси и т.п.).
Подробнее о выборе — `docs/decisions/2026-06-11_telegram-ip-block-proxy.md`.

## Чек-лист первичной настройки

Одноразовая ручная настройка (повторяемая часть — юнит и cron — раскатывается
деплоем):

1. Создать системного пользователя `logoust`.
2. Создать каталоги:
   `/opt/logoust-assistant`, `/var/lib/logoust-assistant`,
   `/var/backups/logoust-assistant` — владелец `logoust:logoust`.
3. Установить `uv` в `/home/logoust/.local/bin` (под пользователем `logoust`).
4. Установить `sqlite3` CLI и `rsync` (нужны для бэкапа и деплоя).
5. Создать прод-`.env` в `/opt/logoust-assistant/.env`: `TELEGRAM_BOT_TOKEN`,
   абсолютный `DATABASE_URL` (см. выше), `LOG_FORMAT=json`, `LOG_FILE_ENABLED=false`.
6. `systemctl enable logoust-assistant` (после первого `make deploy`, раскатавшего юнит).
7. Локально: добавить SSH-алиас `logoust` в `~/.ssh/config`.

Дальше — первый `make deploy`, проверка `journalctl -u logoust-assistant -f`,
дождаться первого ночного бэкапа и убедиться, что `make backup-prod` его скачивает.
