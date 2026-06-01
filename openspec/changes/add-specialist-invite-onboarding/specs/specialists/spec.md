## ADDED Requirements

### Requirement: Создание приглашения специалиста через CLI

Система **SHALL** предоставлять команду `make create_invite`, которая создаёт в таблице `specialists` новую запись с уникальным криптостойким `invite_token`, без `telegram_chat_id` и без `welcomed_at`, и печатает в stdout ровно одну строку — Telegram deep-link для специалиста.

#### Scenario: Успешное создание приглашения

- **WHEN** администратор запускает `make create_invite` при корректных настройках (`TELEGRAM_BOT_USERNAME`, `DATABASE_URL`)
- **THEN** в таблице `specialists` появляется новая запись с непустым `invite_token`, `telegram_chat_id IS NULL`, `welcomed_at IS NULL`, `created_at = текущее время`
- **AND** в stdout выводится ровно одна строка вида `https://t.me/<TELEGRAM_BOT_USERNAME>?start=<token>`, где `<token>` совпадает с `invite_token` созданной записи

#### Scenario: Каждое приглашение получает свой токен

- **WHEN** `make create_invite` запускается дважды подряд
- **THEN** в таблице `specialists` появляются две разные записи с разными значениями `invite_token`

### Requirement: Запуск бота применяет миграции

Команда `make run` **MUST** перед стартом aiogram-приложения применить все pending Alembic-миграции к настроенной БД. Если миграции не применяются — процесс **MUST** упасть до того, как начнёт принимать апдейты от Telegram.

#### Scenario: Чистая БД при первом запуске

- **WHEN** `make run` запускается на пустой БД, для которой ещё не применены миграции
- **THEN** Alembic создаёт таблицу `specialists` и все её индексы
- **AND** только после этого запускается long-polling aiogram

#### Scenario: Ошибка миграций блокирует запуск бота

- **WHEN** Alembic не может применить миграции (повреждённая БД, недоступный файл)
- **THEN** процесс завершается с ненулевым кодом возврата
- **AND** ни один Telegram-апдейт не обрабатывается

### Requirement: Онбординг при первом /start с валидным токеном

Бот **SHALL** на команду `/start <token>`, где `<token>` совпадает с `invite_token` записи специалиста, у которой `welcomed_at IS NULL`, атомарно установить в этой записи `telegram_chat_id = from_user.id`, `telegram_username = from_user.username` (или NULL, если username не задан), `welcomed_at = текущее время`, и отправить в чат пользователя текст приветствия из каталога `messages.toml`.

#### Scenario: Первый /start по приглашающей ссылке

- **WHEN** специалист открывает `https://t.me/<bot>?start=<valid_token>` и Telegram доставляет боту команду `/start <valid_token>`
- **THEN** запись с `invite_token = <valid_token>` обновляется: `telegram_chat_id` равен `from_user.id`, `telegram_username` равен `from_user.username` (или NULL), `welcomed_at` равен текущему времени
- **AND** бот отправляет в чат `from_user.id` текст из ключа `[start].welcome` каталога `messages.toml`

#### Scenario: У специалиста нет публичного username

- **WHEN** `/start <valid_token>` приходит от пользователя, у которого `from_user.username IS NULL`
- **THEN** запись обновляется как обычно, но `telegram_username` сохраняется как NULL
- **AND** приветствие отправляется штатно

### Requirement: Идемпотентность повторного /start

Бот **MUST NOT** повторно отправлять приветствие или изменять запись специалиста, если `/start <token>` приходит для токена, у записи которого `welcomed_at` уже установлен.

#### Scenario: Повторный переход по той же ссылке

- **WHEN** бот получает `/start <token>` для записи специалиста с `welcomed_at IS NOT NULL`
- **THEN** бот отправляет текст из ключа `[start].already_welcomed`
- **AND** поля `welcomed_at`, `telegram_chat_id`, `telegram_username` в БД не меняются

### Requirement: Отказ при неизвестном токене

Бот **SHALL** ответить текстом `[start].unknown_token`, если `/start <token>` приходит со значением, которого нет в таблице `specialists.invite_token`.

#### Scenario: /start с несуществующим токеном

- **WHEN** бот получает `/start <unknown_token>`, где `<unknown_token>` отсутствует в таблице `specialists`
- **THEN** бот отправляет текст из ключа `[start].unknown_token`
- **AND** в таблице `specialists` не появляется новых записей

### Requirement: /start без payload

Бот **SHALL** ответить текстом `[start].no_token`, если получает `/start` без аргументов (специалист открыл бота напрямую, не через invite-ссылку).

#### Scenario: Прямой /start без deep-link

- **WHEN** пользователь открывает `https://t.me/<bot>` и отправляет `/start` без payload
- **THEN** бот отправляет текст из ключа `[start].no_token`
- **AND** в таблице `specialists` не появляется новых записей, существующие записи не меняются

### Requirement: Каталог текстов — единственный источник формулировок

Все тексты, видимые специалисту, **MUST** загружаться из `src/bot/messages.toml`. В коде хендлеров и сервисов формулировки в адрес специалиста жёстко прописаны быть **MUST NOT**.

#### Scenario: Старт бота при отсутствии обязательного ключа

- **WHEN** бот стартует и в `src/bot/messages.toml` отсутствует хотя бы один из обязательных ключей (`[start].welcome`, `[start].already_welcomed`, `[start].unknown_token`, `[start].no_token`)
- **THEN** процесс завершается с ошибкой, в сообщении которой указано название отсутствующего ключа
- **AND** ни один Telegram-апдейт не обрабатывается, ни одно сообщение не отправляется

### Requirement: Язык общения — только русский

Все сообщения, отправляемые ботом специалисту, **MUST** быть на русском языке. Многоязычная инфраструктура (i18n, переводы) в систему не вводится.

#### Scenario: Любое исходящее сообщение бота

- **WHEN** бот отправляет специалисту любое сообщение по любому сценарию (приветствие, повторное приветствие, неизвестный токен, отсутствие токена)
- **THEN** текст этого сообщения на русском языке

### Requirement: Привязка `telegram_chat_id` уникальна

Один и тот же Telegram-аккаунт (`telegram_chat_id`) **MUST NOT** быть привязан более чем к одной записи специалиста.

#### Scenario: Попытка использовать второе приглашение с того же аккаунта

- **WHEN** специалист уже привязан к одной записи (welcomed) и переходит по второму, ещё не использованному приглашению со своего же Telegram-аккаунта
- **THEN** операция привязки второй записи завершается ошибкой целостности, не приводя к появлению второго `telegram_chat_id` с тем же значением
- **AND** факт аномалии записывается в лог события `specialist.invite_chat_conflict` с `specialist_id` и хвостом токена
