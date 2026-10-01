## 1. Хранение версии клавиатуры (БД)

- [x] 1.1 Добавить `SpecialistORM.keyboard_version: Mapped[str | None]` (nullable, `String`) в `src/infrastructure/specialists_repo.py`; протянуть поле в доменную сущность `Specialist` и `to_domain`
- [x] 1.2 Создать Alembic-миграцию: `ADD COLUMN keyboard_version` (nullable, без backfill)
- [x] 1.3 Добавить в `SqlAlchemySpecialistsRepo` метод обновления версии (`set_keyboard_version(specialist_id, version)`)

## 2. Вычисление версии набора кнопок

- [x] 2.1 Реализовать хелпер `keyboard_version(messages) -> str` — детерминированный хеш упорядоченного списка подписей кнопок `build_main_keyboard` (рядом с `build_main_keyboard` в `src/bot/handlers/clients.py` или в отдельном модуле `bot/`)
- [x] 2.2 Тест: добавление/переименование кнопки меняет версию; перезапуск без изменений — нет

## 3. Узнавание клиента по chat_id

- [x] 3.1 Добавить `SqlAlchemyClientsRepo.find_by_chat_id(telegram_chat_id) -> Client | None` (+ протокол в `domain`, если есть)
- [x] 3.2 Тест: находит привязанного клиента, возвращает None для неизвестного

## 4. Голый /start узнаёт пользователя (вариант A)

- [x] 4.1 В `messages.toml` добавить клиентский текст `[start].already_linked` («вы уже подключены»)
- [x] 4.2 В `make_start_handler` (`src/bot/handlers/start.py`) переписать ветку `if not token:`: спец по `chat_id` → `already_welcomed` + `build_main_keyboard` + фиксация текущей версии; иначе клиент по `chat_id` → `already_linked`; иначе → `no_token`
- [x] 4.3 Python-лог события (например `specialist.keyboard_refreshed`) при обновлении версии; БЕЗ client-audit (правило bot.md)
- [x] 4.4 Тесты хендлера: спец → already_welcomed+клавиатура+bump; клиент → already_linked; неизвестный → no_token

## 5. Автообновление клавиатуры (вариант D3, middleware)

- [x] 5.1 Реализовать outer-middleware в `bot/`: по chat_id найти специалиста, при `keyboard_version != current` пометить апдейт «нужна свежая клавиатура»
- [x] 5.2 Гарантировать подкладывание `reply_markup=build_main_keyboard` в исходящий ответ и запись версии ТОЛЬКО после успешной отправки (при сбое доставки версию не бампить)
- [x] 5.3 Зарегистрировать middleware в `src/bot/dispatcher.py::build_dispatcher`
- [x] 5.4 Тесты middleware: устаревшая версия → ответ несёт markup и версия записана; актуальная версия → markup не подменяется, версия не меняется; сбой доставки → версия не меняется

## 6. Документация и спеки

- [x] 6.1 `docs/database.md` — столбец `specialists.keyboard_version` (таблица + раздел миграций)
- [x] 6.2 `docs/bot.md` — middleware автообновления клавиатуры и новое поведение голого `/start`
- [ ] 6.3 Синхронизировать `openspec/specs/` (specialists, clients, новая capability specialist-keyboard-refresh) при архивации

## 7. Проверка

- [x] 7.1 `make check` — формат, линт, типы, 100% покрытие
