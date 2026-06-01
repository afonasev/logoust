# Telegram-бот

Бот построен на **aiogram 3** в режиме long-polling. Все пользовательские тексты — в `src/bot/messages.toml`, в коде формулировок нет.

## Сборка приложения

- `src/bot/dispatcher.py::build_dispatcher(messages, session_factory) -> Dispatcher` — собирает `Dispatcher`, подключает единственный роутер.
- `src/bot/handlers/start.py::build_router(messages, session_factory) -> Router` — регистрирует обработчик `/start`.
- `src/bot/handlers/start.py::make_start_handler(messages, session_factory)` — фабрика хендлера (вынесена для удобства тестов).
- `src/__main__.py` — точка входа: грузит `settings`, инициализирует logging, создаёт `Bot`, `session_factory`, `messages`, `Dispatcher`, запускает `dp.start_polling(bot)`.

## Хендлеры

| Команда | Файл | Что делает |
| --- | --- | --- |
| `/start [<token>]` | `src/bot/handlers/start.py` | Ищет специалиста по токену, идемпотентно проставляет `chat_id`/`username`/`welcomed_at`, отвечает текстом из каталога. Четыре ветки: welcomed, already_welcomed, unknown_token, no_token. |

Других команд (включая `/help`) сознательно нет — приветствие не должно врать о возможностях.

## Каталог текстов

Файл: `src/bot/messages.toml`. Загрузка: `src/bot/messages.py::load_messages(path) -> BotMessages` (frozen dataclass).

Обязательные ключи (отсутствие → `RuntimeError` на старте, до приёма апдейтов):

- `[start].welcome` — приветствие при первом валидном `/start <token>`.
- `[start].already_welcomed` — ответ на повторный `/start` уже использованного токена.
- `[start].unknown_token` — ответ, если токен не найден.
- `[start].no_token` — ответ, если `/start` пришёл без payload.

Тексты редактируются вручную и не требуют деплоя кода; перечитываются при перезапуске процесса.

## Логи

- `specialist.invite_created` — успешный `create_invite`.
- `specialist.welcomed` — первый успешный `/start <token>`.
- `specialist.invite_replayed` — повтор `/start` для уже привязанного токена.
- `specialist.invite_unknown` — `/start` с неизвестным токеном.

В `extra` всегда передаются `specialist_id` (если известен) и `token_prefix` — первые 6 символов токена, чтобы не светить полный токен в журнале.
