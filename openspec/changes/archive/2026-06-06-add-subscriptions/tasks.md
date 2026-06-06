## 1. Домен

- [x] 1.1 Создать `src/domain/subscription.py`: dataclass `Subscription` (`id`, `client_id`, `specialist_id`, `purchased`, `remaining`, `status`, `created_at`, `closed_at`), enum `SubscriptionStatus` (`active`/`closed`), константа `DEFAULT_SUBSCRIPTION_MEETINGS = 8`
- [x] 1.2 В `subscription.py` добавить протокол `SubscriptionsRepo`: `add`, `get_active(client_id, specialist_id)`, `get_for_specialist(subscription_id, specialist_id)`, `update_counters(...)`, `close(...)`
- [x] 1.3 В `src/domain/specialist.py` добавить поле `subscription_default: int = DEFAULT_SUBSCRIPTION_MEETINGS` и константу дефолта

## 2. Инфраструктура и миграция

- [x] 2.1 Создать `src/infrastructure/subscriptions_repo.py`: ORM-модель `subscriptions` (Mapped/mapped_column, FK на `clients` и `specialists`, индекс `(client_id, status)`) и `SqlAlchemySubscriptionsRepo`, реализующий протокол
- [x] 2.2 Добавить ORM-колонку `subscription_default` в модель специалиста (server-default `8`)
- [x] 2.3 Создать alembic-миграцию: таблица `subscriptions` + колонка `subscription_default` в `specialists` со server-default 8; `downgrade` удаляет обе

## 3. Сервисы

- [x] 3.1 Создать `src/services/subscriptions.py`: `create_subscription`, `decrement_meeting`, `extend_subscription`, `close_subscription`, `get_active`, `get_card` с логированием бизнес-событий (`subscription.created`, `.decremented`, `.extended`, `.closed`)
- [x] 3.2 Добавить хелпер валидации числа встреч (положительное целое с верхним порогом) и инвариант «один активный» в `create_subscription`; списание не уходит ниже 0
- [x] 3.3 В `src/services/specialists.py` добавить `SettingField.SUBSCRIPTION_DEFAULT` и его нормализацию/валидацию (положительное целое)

## 4. Бот: абонементы

- [x] 4.1 Создать `src/bot/handlers/subscriptions.py`: роутер с `SpecialistMiddleware`, рендер карточки абонемента, клавиатура действий (вычесть/продлить/закрыть/назад на карточку клиента)
- [x] 4.2 FSM создания: показ подсказки с дефолтом, приём числа, валидация, создание, показ карточки
- [x] 4.3 FSM продления: подсказка с дефолтом, приём числа, валидация, обновление счётчиков
- [x] 4.4 Действие списания (с alert при остатке 0) и закрытие с экраном подтверждения (да/отмена)
- [x] 4.5 Зарегистрировать роутер в `src/bot/dispatcher.py`

## 5. Бот: карточка клиента и настройки

- [x] 5.1 В `src/bot/handlers/clients.py` в `_card_view`/`_card_keyboard` добавить кнопку абонемента: создать (нет активного) или перейти с остатком (есть), только на активной карточке
- [x] 5.2 В `src/bot/handlers/settings.py` добавить пункт меню и FSM-шаг для `subscription_default`
- [x] 5.3 Добавить тексты абонементов и настройки в `src/bot/messages.toml` и их типы в `src/bot/messages.py`

## 6. Тесты

- [x] 6.1 `tests/test_subscriptions_repo.py`: add/get_active/get_for_specialist/update/close, изоляция по специалисту
- [x] 6.2 `tests/test_subscriptions_service.py`: создание (дефолт/число), инвариант одного активного, списание и блок на 0, продление, закрытие с остатком, валидация числа
- [x] 6.3 Тесты хендлеров: карточка абонемента, FSM создания/продления, подтверждение закрытия, кнопка на карточке клиента (создать/перейти/скрыта на архиве)
- [x] 6.4 Тест миграции в `tests/test_migration.py`: дефолт `subscription_default = 8` у существующих, наличие таблицы `subscriptions`
- [x] 6.5 Тест настройки `subscription_default` в `tests/` (валидный/невалидный ввод)

## 7. Документация и спеки

- [x] 7.1 Обновить `docs/database.md` (таблица `subscriptions`, колонка специалиста, миграция), `docs/bot.md` (новый роутер, кнопка, пункт настроек), `docs/features.md` (сценарий абонементов)
- [x] 7.2 Обновить `README.md` (раздел «Ключевые возможности») и синхронизировать `openspec/specs/` при архивации изменения

## 9. Раздел «Абонементы» в меню (расширение)

- [x] 9.1 Репозиторий: `list_active_for_specialist` / `list_closed_for_specialist` (пагинация) + методы в протоколе `SubscriptionsRepo`
- [x] 9.2 Сервис: `SubscriptionsPage`, `list_active_page` / `list_closed_page`
- [x] 9.3 Бот: reply-кнопка «🎫 Абонементы» в `build_main_keyboard`; хендлеры `show_list`/`show_active`/`show_closed`, клавиатуры списков с пагинацией и кросс-переходом активные↔закрытые; read-only карточка закрытого абонемента
- [x] 9.4 Тексты раздела в `messages.toml`/`messages.py` (кнопка, заголовки/пустые списки, строки списков, навигация, пометка «закрыт»)
- [x] 9.5 Тесты репозитория/сервиса/хендлеров для списков, истории и read-only карточки
- [x] 9.6 Обновить `docs/` (bot.md, features.md), `README.md`, спеку `subscriptions`

## 8. Проверка

- [x] 8.1 `openspec validate add-subscriptions` без ошибок
- [x] 8.2 `make check` — формат, линт, типы, 100% покрытие
