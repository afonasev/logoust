## 1. Домен

- [x] 1.1 Добавить сущность `SubscriptionDeduction` (id, subscription_id, appointment_id?, appointment_starts_at?, appointment_comment?, closing_comment?, created_at, cancelled_at?) в `src/domain/subscription.py` (или новый `domain/deduction.py`)
- [x] 1.2 Добавить repo-протокол `SubscriptionDeductionsRepo`: `add`, `list_active_for_subscription`, `get_for_specialist`, `set_closing_comment`, `cancel` (атомарно с возвратом остатка), `exists_for_appointment`
- [x] 1.3 Расширить `Specialist` полями `consumption_enabled`, `consumption_time`, `consumption_last_run_on` и хелпером `is_consumption_due(specialist, now)` в `src/domain/specialist.py`
- [x] 1.4 Перевести репо-контракт списания на атомарный декремент `WHERE remaining > 0` (метод вида `decrement_if_positive`) в `SubscriptionsRepo`

## 2. Инфраструктура и миграция

- [x] 2.1 ORM-модель `subscription_deductions` в `src/infrastructure/subscriptions_repo.py` (+ реализация `SqlAlchemySubscriptionDeductionsRepo`)
- [x] 2.2 Реализовать атомарные операции: вставка-замок + декремент в одной транзакции; отмена (cancelled_at + remaining+1) в одной транзакции
- [x] 2.3 Добавить колонки `consumption_*` в ORM специалиста и кандидатный запрос `list_consumption_candidates`
- [x] 2.4 Alembic-миграция: таблица `subscription_deductions`, уникальный частичный индекс на `appointment_id` (`WHERE appointment_id IS NOT NULL`), 3 колонки специалисту с дефолтами (вкл, `20:00`), back-fill существующим
- [x] 2.5 Проверить наличие уникального индекса `appointments(slot_id, origin_date)`; если нет — добавить этой же миграцией и перевести материализацию повтора на `INSERT … ON CONFLICT DO NOTHING` + повторное чтение

## 3. Сервисы

- [x] 3.1 `services/subscriptions.py`: `decrement_meeting` — атомарный декремент + запись строки журнала (appointment_id=NULL); вернуть None-ветку при remaining==0 без строки
- [x] 3.2 `services/subscriptions.py`: `cancel_deduction` (мягкая отмена, +1, идемпотентна) и `set_deduction_comment`
- [x] 3.3 Новый `services/consumption.py`: `run_consumption_if_due(specialist, now, …, report)` — гейт `is_consumption_due`, сбор сегодняшних прошедших встреч (через `load_series_context`/`list_specialist_day`), материализация повтора, авто-списание по решению 1, накопление результата (списанные абонементы + ❗-встречи), вызов `report`, простановка дневного флага
- [x] 3.4 Структура отчёта (`ConsumptionReport`/payload) с разделением: списанные абонементы и ❗-встречи (нет абонемента / закончился)

## 4. Бот

- [x] 4.1 `bot/scheduler.py`: `run_consumption_pass` + `_run_consumption_for_specialist` + доставка отчёта (кнопки на абонементы, ❗-список); сбой доставки не откатывает списания
- [x] 4.2 Подключить проход в поминутный цикл `__main__`
- [x] 4.3 `bot/handlers/subscriptions.py`: журнал списаний кнопками на карточке абонемента (активный/закрытый — read-only)
- [x] 4.4 Экран списания: показ даты встречи + оба комментария раздельно; кнопки «✏️ Комментарий» (FSM) и «↩️ Отменить» (с возвратом остатка); закрытый абонемент — без действий
- [x] 4.5 `bot/handlers/settings.py`: блок «Авто-списание» — тумблер, время (FSM-ввод с валидацией), «📤 Отправить сейчас» (реальный прогон мимо флага)
- [x] 4.6 Тексты в `src/bot/messages.toml`: отчёт, ❗-строки, экран списания, кнопки, подсказки настроек

## 5. Тесты

- [x] 5.1 Домен: `is_consumption_due`, поведение сущности списания
- [x] 5.2 Сервис consumption: списание/❗-ветки, пустой вечер (молчит, но день помечен), повтор серии материализуется
- [x] 5.3 Идемпотентность: два прохода по одной встрече → одно списание (через реальный уникальный индекс); «Отправить сейчас» поверх боевого не задваивает
- [x] 5.4 Атомарность: две встречи клиента за день по одному абонементу не теряют декремент
- [x] 5.5 Ручное списание пишет строку журнала; remaining==0 не создаёт строку
- [x] 5.6 Отмена: +1, скрытие из истории, повторно не списывается; редактирование комментария
- [x] 5.7 Хендлеры: карточка с журналом, экран списания, отмена, настройки + «Отправить сейчас»
- [x] 5.8 Миграция: применение/откат, back-fill, наличие уникальных индексов

## 6. Документация и спеки

- [x] 6.1 `docs/features.md` — раздел про авто-учёт расхода абонементов и журнал
- [x] 6.2 `docs/database.md` — таблица `subscription_deductions`, индексы, колонки специалиста, миграция
- [x] 6.3 `docs/bot.md` — новый проход, экраны журнала/списания, блок настроек
- [x] 6.4 Синхронизировать `openspec/specs/` (`subscription-consumption` + правка `subscriptions`) после реализации
- [x] 6.5 `make check` зелёный (формат, линт, типы, 100% покрытие)
