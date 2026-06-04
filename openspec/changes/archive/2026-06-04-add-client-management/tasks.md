## 1. Domain слой

- [x] 1.1 Создать `src/domain/client.py`: `ClientStatus` enum (`active`/`archived`), `@dataclass(slots=True) Client` со всеми полями (`id`, `specialist_id`, `child_name`, `contact_name`, `contact_phone`, `contact_telegram`, `extra_contacts`, `note`, `status`, `archived_at`, `created_at`, `updated_at`)
- [x] 1.2 Добавить чистую `normalize_phone(raw: str) -> str` по алгоритму из design (strip не-цифры → 8/7/10-цифр → `+7…`; фолбэк на trim исходника)
- [x] 1.3 Добавить чистую `normalize_telegram(raw: str) -> str` (срез ведущего `@`)
- [x] 1.4 Объявить `ClientsRepo` Protocol: `add`, `get_for_specialist`, `list_by_status`, `update_fields`, `set_status`
- [x] 1.5 Юнит-тесты на `normalize_phone` (все 5 примеров спеки + фолбэк) и `normalize_telegram`

## 2. Infrastructure слой

- [x] 2.1 Создать `src/infrastructure/clients_repo.py`: `ClientORM(Base)` по conventions (`Mapped`/`mapped_column`, `String(...)`/`Text`, FK на `specialists.id`, `status` строкой, `archived_at` nullable, `created_at`/`updated_at` с `lambda: datetime.now(UTC)`, `__repr__`)
- [x] 2.2 Реализовать `to_domain` и `SqlAlchemyClientsRepo` (`flush`→`commit`; `select(...).where(specialist_id==…, status==…).order_by(child_name)` для списков; `get_for_specialist` фильтрует по владельцу)
- [x] 2.3 Сгенерировать Alembic-миграцию: таблица `clients`, FK `specialist_id`, индексы по `specialist_id` и `(specialist_id, status)`
- [x] 2.4 Тесты репозитория на async-сессии: add/list/update/set_status + изоляция по `specialist_id`

## 3. Services слой

- [x] 3.1 `src/services/clients.py` — `add_client(...)`: валидация минимума (имя ребёнка, имя контакта, хотя бы один из phone/telegram), нормализация phone/telegram, создание `active`; лог `client.created`
- [x] 3.2 `edit_client_field(...)`: проверка владельца, запрет пустого значения для `child_name`/`contact_name`, нормализация phone/telegram, обновление `updated_at`; лог `client.field_updated`
- [x] 3.3 `archive_client(...)` / `restore_client(...)`: проверка владельца, смена статуса + `archived_at`, обновление `updated_at`; логи `client.archived` / `client.restored`
- [x] 3.4 `list_clients(specialist_id, status)`: возврат только своих, отсортированных по `child_name`
- [x] 3.5 Тесты сервисов: success/failure минимума, изоляция по владельцу, архив/возврат без потери данных

## 4. Bot слой

- [x] 4.0 Добавить `find_by_chat_id` в `SpecialistsRepo` (протокол + impl + тест) — резолв специалиста по chat_id для middleware

- [x] 4.1 Добавить тексты в `src/bot/messages.toml` (приглашения шагов добавления, подтверждения, ошибки валидации, заголовки/строки списков, пустой список, карточка клиента)
- [x] 4.2 `src/bot/handlers/clients.py`: FSM добавления клиента (пошаговый ввод полей с валидацией минимума)
- [x] 4.3 Карточка клиента + обобщённый FSM редактирования («выбрать поле → прислать значение»), inline-кнопки архивации/возврата
- [x] 4.4 Навигация по спискам активных/архивных клиентов (inline-кнопки, открытие карточки)
- [x] 4.5 Зарегистрировать router в `src/bot/dispatcher.py`
- [x] 4.6 Тесты хендлеров через фейковые `Message`/`CommandObject` (без поллинга): добавление, редактирование, архив/возврат, просмотр списков

## 5. Документация и спеки

- [x] 5.1 `docs/database.md`: таблица `clients`, индексы, FK, раздел миграции
- [x] 5.2 `docs/bot.md`: новый router, FSM, ключи `messages.toml`
- [x] 5.3 `docs/features.md`: сценарий управления списком клиентов
- [x] 5.4 `docs/architecture.md`: при необходимости — новый domain/service/repo (если меняются правила слоёв)
- [x] 5.5 `README.md`: раздел «Ключевые возможности»
- [x] 5.6 `/opsx:sync` (или ручной перенос) — синхронизировать `openspec/specs/clients/` с реализованным поведением

## 6. Проверка

- [x] 6.1 `make check` — format + lint + type-check + 100% покрытие зелёные
- [x] 6.2 Сверить реализацию со сценариями спеки (`openspec validate add-client-management`)
