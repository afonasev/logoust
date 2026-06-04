# Telegram-бот

Бот построен на **aiogram 3** в режиме long-polling. Все пользовательские тексты — в `src/bot/messages.toml`, в коде формулировок нет.

## Сборка приложения

- `src/bot/dispatcher.py::build_dispatcher(messages, session_factory) -> Dispatcher` — собирает `Dispatcher`, подключает роутеры. **Порядок важен:** `clients`-роутер включается раньше `start`-роутера — fallback `start` ловит любой текст (онбординг по вставленному коду), поэтому иначе он перехватывал бы кнопку «Клиенты» и ввод в визардах. Нераспознанный текст всё равно проваливается в `start`.
- `src/bot/handlers/start.py::build_router(messages, session_factory) -> Router` — регистрирует обработчик `/start` и fallback на вставленный код/ссылку.
- `src/bot/handlers/clients.py::build_router(messages, session_factory) -> Router` — роутер управления клиентами: reply-кнопка, inline-меню, визард добавления (FSM), карточка, редактирование полей, архив/возврат, списки. Состояние FSM — на дефолтном `MemoryStorage` (теряется при рестарте процесса).
- `src/bot/handlers/start.py::make_start_handler(messages, session_factory)` — фабрика хендлера `/start` (вынесена для удобства тестов).
- `src/bot/handlers/start.py::make_token_handler(messages, session_factory)` — фабрика fallback-хендлера: ловит обычный текст и через `extract_token()` достаёт токен из голого кода или deep-link.
- `src/__main__.py` — точка входа: грузит `settings`, инициализирует logging, создаёт `Bot`, `session_factory`, `messages`, `Dispatcher`, запускает `dp.start_polling(bot)`.

## Хендлеры

| Команда | Файл | Что делает |
| --- | --- | --- |
| `/start [<token>]` | `src/bot/handlers/start.py` | Ищет специалиста по токену, идемпотентно проставляет `chat_id`/`username`/`welcomed_at`, отвечает текстом из каталога. Четыре ветки: welcomed, already_welcomed, unknown_token, no_token. |
| Текст с кодом/ссылкой | `src/bot/handlers/start.py` (`make_token_handler`) | Fallback: если специалист вставил в чат голый токен или deep-link целиком — извлекает токен (`extract_token`) и проводит тот же онбординг, что и `/start <token>`. На сообщения, не похожие на токен, молчит. Зарегистрирован после `/start`, поэтому `/start <token>` обрабатывается штатно. |
| Кнопка «👶 Клиенты» | `src/bot/handlers/clients.py` | Reply-кнопка (показывается после онбординга) открывает inline-меню: «Добавить / Активные / Архив». Нажатие также сбрасывает любой активный визард. |
| Управление клиентами | `src/bot/handlers/clients.py` (`ClientsHandlers`) | Добавление (FSM: имя ребёнка → имя контакта → телефон → telegram), карточка с редактированием любого поля, архивация/возврат, список активных. Колбэки по схеме `clients:<action>:<id>`. |
| Архив клиентов | `src/bot/handlers/clients.py` (`show_archive`) | Постраничный архив, сортировка по `archived_at` убыванию (свежие сверху), дата архивации в строке. Размер страницы — `_ARCHIVE_PAGE_SIZE`. Навигация: `clients:list:archived` (стр. 0) и `clients:arch:<page>`. Наличие следующей страницы определяется выборкой `page_size + 1` строки (без COUNT). |

Доступ ограничивает `SpecialistMiddleware` (inner-middleware `clients`-роутера): резолвит специалиста по `chat_id` через `SpecialistsRepo.find_by_chat_id`, кладёт `specialist_id` в данные хендлера, а апдейты от неонбординнутых пользователей роняет.

Других команд бота (включая `/help`) сознательно нет — приветствие не должно врать о возможностях.

## Каталог текстов

Файл: `src/bot/messages.toml`. Загрузка: `src/bot/messages.py::load_messages(path) -> BotMessages` (frozen dataclass).

Обязательные ключи (отсутствие → `RuntimeError` на старте, до приёма апдейтов):

- `[start].welcome` — приветствие при первом валидном `/start <token>`.
- `[start].already_welcomed` — ответ на повторный `/start` уже использованного токена.
- `[start].unknown_token` — ответ, если токен не найден.
- `[start].no_token` — ответ, если `/start` пришёл без payload.
- `[clients].*` — секция текстов управления клиентами: `button`, заголовки меню и списков (`menu_title`, `list_active_title`, `list_archived_title`, `empty_active`, `empty_archived`), шаблон карточки `card` и метки статуса (`status_active`/`status_archived`, `dash`), приглашения визарда (`ask_child_name`, `ask_contact_name`, `ask_phone`, `ask_telegram`) и подтверждения/ошибки (`added`, `archived`, `restored`, `updated`, `cancelled`, `empty_required`, `need_contact_channel`, `edit_prompt`, `not_found`). Короткие подписи inline-кнопок живут константами в `clients.py` (навигационная «обвязка», не сценарные тексты).

Тексты редактируются вручную и не требуют деплоя кода; перечитываются при перезапуске процесса.

## Логи

- `specialist.invite_created` — успешный `create_invite`.
- `specialist.welcomed` — первый успешный `/start <token>`.
- `specialist.invite_replayed` — повтор `/start` для уже привязанного токена.
- `specialist.invite_unknown` — `/start` с неизвестным токеном.
- `client.created` / `client.field_updated` / `client.archived` / `client.restored` — бизнес-события картотеки (в `extra`: `specialist_id`, `client_id`).

В `extra` всегда передаются `specialist_id` (если известен) и `token_prefix` — первые 6 символов токена, чтобы не светить полный токен в журнале.
