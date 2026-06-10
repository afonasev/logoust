# Архитектура

Clean Architecture: домен в центре, инфраструктура и адаптеры по краям. Веб-слой (HTTP) не планируется — единственный канал общения со специалистом это Telegram.

## Слои

```
src/
├── config.py                 # pydantic-settings: env → settings
├── logging_setup.py          # structlog + logging
├── __main__.py               # entrypoint: запускает aiogram-бота
│
├── domain/                   # сущности, протоколы репозиториев
│   ├── specialist.py
│   └── client.py
│
├── services/                 # use-cases, depend on domain
│   ├── invites.py
│   └── clients.py
│
├── infrastructure/           # ORM, async-сессии, репозитории
│   ├── db.py
│   ├── specialists_repo.py
│   └── clients_repo.py
│
├── bot/                      # adapter-слой: aiogram dispatcher + хендлеры
│   ├── dispatcher.py
│   ├── handlers/
│   │   ├── start.py
│   │   └── clients.py
│   ├── messages.py
│   └── messages.toml
│
└── cli/                      # adapter-слой: административные команды
    └── create_invite.py
```

## Правила зависимостей

- `domain/` импортирует только stdlib.
- `services/` импортирует `domain/`.
- `infrastructure/` импортирует `domain/` (+ SQLAlchemy).
- `bot/`, `cli/` — adapter-слои. Импортируют `services/`, `infrastructure/`, `domain/`, `config`. Никогда наоборот.

`bot/` и `cli/` — две «двери» в приложение: пользовательский Telegram-канал и административный shell. Обе через `services/` дёргают одну и ту же бизнес-логику.

**Почему бизнес-правила не в хендлерах.** Валидация минимума клиента, нормализация телефона/Telegram живут в `domain/`/`services/`, а не в хендлерах — чтобы не зависеть от способа ввода. Это держит дверь открытой для второго адаптера (например, Telegram Mini App) поверх тех же use-cases без переписывания логики.

### Авторизация

- Основной канал — через aiogram inner-middleware `SpecialistMiddleware` (`bot/handlers/clients.py`): резолвит специалиста по `chat_id`, инжектит `specialist_id` в хендлеры и отсекает неонбординнутых.
- Исключение — роутер `bot/handlers/reminders.py` (колбэк `appt:cfm:`): его актор — **клиент**, а не специалист, поэтому он намеренно вне `SpecialistMiddleware`. Изоляцию владельца здесь обеспечивает сервис: сверяет `chat_id` ответившего с привязанным клиентом напоминания.

## Фоновый планировщик (минутный тик)

Кроме long-polling бот держит рядом фоновый asyncio-таск: `__main__._scheduler_loop` спит до начала следующей минуты и запускает due-джобы.

На каждом тике выполняются четыре независимые джобы, каждая обёрнута в свой `try/except Exception`:

- дневной проход напоминаний клиентам — `bot/scheduler.run_reminder_pass`;
- утренняя сводка специалисту — `bot/scheduler.run_digest_pass`;
- напоминание специалисту об оплате абонемента — `bot/scheduler.run_payment_reminder_pass`;
- доставка отложенных уведомлений клиенту — `bot/scheduler.run_outbox_pass`.

Раздельная обёртка значит, что падение одной джобы не пропускает другие и не роняет polling. Цикл — общий «планировщик»: добавить джобу = дописать ещё один обёрнутый вызов в тик, без отдельного asyncio-таска на фичу. Внешний планировщик/cron сознательно не вводится (YAGNI): один процесс — один тик.

### Где что лежит

- Цикл со `sleep` — в `__main__.py` (исключён из покрытия).
- Тестируемая логика проходов — в `bot/scheduler.py`.
- Решение «кому пора» — чистые функции `domain/reminder.is_reminder_due`, `domain/specialist.is_digest_due`, `domain/specialist.is_payment_reminder_due` (настенное время в tz, антидубль через `*_last_run_on`, догон через порог `>=`).

### Как обеспечивается «ровно один раз»

- **Напоминания клиенту.** Идемпотентность отправки дополнительно гарантирует `UNIQUE` журнала `appointment_reminders`.
- **Утренняя сводка.** Пометка `morning_notify_last_run_on` ставится **до** отправки — сбой доставки не зацикливает проход и не шлёт повторно.
- **Отложенные уведомления** (`run_outbox_pass`). Проход берёт строки `scheduled_client_messages` напрямую (`status='queued' AND due_at<=now`, по возрастанию), без обхода специалистов: для каждой повторно проверяет привязку клиента, шлёт снимок текста и переводит строку в `sent`/`failed`. Антидубль и догон после простоя — через сам переход статуса (повторный тик не подхватывает `sent`/`failed`).
- **Напоминание об оплате** (`run_payment_reminder_pass` → `services/payment_reminder.run_payment_reminders_if_due`) держит два слоя антидубля:
  - «решение за день» (`payment_reminder_last_run_on`) — один прогон в день;
  - per-subscription `payment_reminded_at` — один алерт, пока остаток 0 (сбрасывается при продлении).

Сам алерт об оплате уходит специалисту (имя клиента, время завтрашней записи, превью текста для клиента). Привязанному клиенту под алертом — кнопка «Отправить» (`pay:send:<client_id>`); отправка клиенту идёт по тапу специалиста через `bot/handlers/payment.py` с записью в `audit_log`.

## Поток данных: онбординг

```
admin@shell ──► make create-invite ──► cli/create_invite.main()
                                        │
                                        ▼
                                services.invites.create_invite(repo)
                                        │
                                        ▼
                                SQLite (specialists)
                                        │
                                        ▼
                                stdout: deep-link URL


specialist@telegram ──► /start <token> ──► bot/handlers/start.handle_start
                                            │
                                            ▼
                                    services.invites.consume_invite(repo, token, chat_id, username)
                                            │
                                            ▼
                                    SQLite (specialists) — атомарное обновление
                                            │
                                            ▼
                                    bot отправляет текст из messages.toml
```

Async везде: aiogram async, SQLAlchemy `AsyncSession`, CLI оборачивает корневую корутину через `asyncio.run`. Alembic остаётся sync (отдельный URL без `+aiosqlite`).

## Конфигурация

`src/config.py` — `pydantic-settings` поверх `.env`.

| Переменная             | Тип   | Дефолт                                | Назначение |
| ---------------------- | ----- | ------------------------------------- | ---------- |
| `TELEGRAM_BOT_TOKEN`   | `SecretStr` | — (обязательная)                 | Токен бота из BotFather. |
| `TELEGRAM_BOT_USERNAME`| `str` | — (обязательная)                      | Username бота без `@`, нужен CLI для deep-link. |
| `DATABASE_URL`         | `str` | `sqlite+aiosqlite:///./logoust.db`    | Async-URL. Alembic читает sync-вариант. |
| `LOG_FORMAT`           | `str` | `text`                                | `text` или `json`. |
| `LOG_LEVEL`            | `str` | `INFO`                                | Стандартные уровни logging. |
| `LOG_FILE_ENABLED`     | `bool`| `false`                               | Включает файловый handler с ротацией по полуночи UTC. |
| `LOG_DIR`              | `str` | `./logs`                              | Куда писать файлы логов. |
| `LOG_FILE_BACKUP_DAYS` | `int` | `7`                                   | Сколько дней хранить ротированные файлы. |

## Каталог текстов

Все пользовательские тексты — в `src/bot/messages.toml`. Загрузчик `src/bot/messages.py` читает файл один раз при старте через `tomllib`, кладёт в frozen-dataclass `BotMessages`. Отсутствие обязательного ключа → `RuntimeError` на старте, до приёма апдейтов.
