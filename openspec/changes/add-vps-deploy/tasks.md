## 1. Серверные конфиги (шаблоны в репо)

- [x] 1.1 Создать `deploy/logoust-assistant.service`: запуск `python -m src` (long-polling)
      под юзером `logoust` из `/opt/logoust-assistant`, `EnvironmentFile=.env`,
      `Restart=on-failure`, `WantedBy=multi-user.target`
- [x] 1.2 Создать `deploy/logoust-assistant-backup.cron`: запуск `scripts/backup_db.sh`
      в 03:45 UTC от юзера `logoust` (не пересекается с gfe 03:30)

## 2. Скрипты деплоя и бэкапа

- [x] 2.1 `scripts/backup_db.sh`: `sqlite3 .backup` из `/var/lib/logoust-assistant/app.db`,
      gzip, daily (7 дней) + weekly-продвижение по воскресеньям (~месяц), ретеншен через
      `find -mtime`. Сделать исполняемым (`chmod +x`)
- [x] 2.2 `scripts/deploy.sh`: `SSH_HOST=logoust`, rsync кода в `/opt/logoust-assistant`
      (исключая `.git`, `.venv`, `*.db*`, `.env`, кэши, `backups`, `logs`, `data`),
      `chown -R logoust:logoust`, `uv sync --no-dev` + `alembic upgrade head` под `logoust`,
      раскатка юнита+cron из `deploy/` в системные пути, `daemon-reload`,
      `systemctl restart` → `sleep` → `systemctl is-active`. `set -euo pipefail`, `chmod +x`
- [x] 2.3 `scripts/pull_backups.sh`: `SSH_HOST=logoust`, rsync
      `/var/backups/logoust-assistant/` → `backups/vps/`, вывести список. `chmod +x`

## 3. Makefile

- [x] 3.1 Добавить цель `deploy` → `bash scripts/deploy.sh` (с `## ...` хелп-комментарием)
- [x] 3.2 Добавить цель `backup-prod` → `bash scripts/pull_backups.sh`
- [x] 3.3 Добавить цель `backup` → `mkdir -p backups` + копия
      `logoust.db` → `backups/logoust_$$(date +%Y-%m-%d).db`
- [x] 3.4 `scripts/create_invite_prod.sh` + цель `create-invite-prod`: запуск CLI
      `src.cli.create_invite` на VPS под `logoust`, в stdout только deep-link. `chmod +x`.
      Переименовать локальную цель `create_invite` → `create-invite` (единая схема имён)

## 4. Документация

- [x] 4.1 Создать `docs/deployment.md`: раскладка на сервере (таблица путей), systemd-юнит,
      reverse proxy/firewall — явно НЕ нужны (бот polling), cron-бэкап, логи в journald,
      процесс `make deploy`, чек-лист первичной настройки (юзер `logoust`, каталоги,
      прод-`.env`, `uv`, `systemctl enable`, SSH-алиас `logoust`), нюанс 409-конфликта
- [x] 4.2 Добавить `docs/deployment.md` в индекс `docs/README.md`
- [x] 4.3 Обновить `README.md`: команды `make deploy`/`make backup`/`make backup-prod`/
      `make create-invite-prod` (раздел «Команды разработки»), требование SSH-алиаса `logoust`

## 5. Проверка

- [x] 5.1 `openspec validate add-vps-deploy` проходит
- [x] 5.2 Прогнать `bash -n` по всем трём скриптам (синтаксис без выполнения),
      убедиться что у них выставлен бит исполнения
- [x] 5.3 `make check` (форматтер не трогает скрипты, но тесты/линт не должны сломаться)
