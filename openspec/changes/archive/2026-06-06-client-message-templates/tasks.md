# Tasks: client-message-templates

> Зависит от мёрджа `appointment-reminder`, `appointment-notify`,
> `subscription-payment-reminder`. До их слияния реально настраиваемы только
> `invite_forward` / `linked`; остальные ключи подключаются по мере появления их текстов.

## 1. Domain — каталог и валидация

- [x] 1.1 Завести `src/domain/message_template.py`: `TemplateSpec` (`allowed`, `required`) и
      каталог `CLIENT_TEMPLATES: dict[str, TemplateSpec]` — только клиентские ключи, без имён в
      whitelist.
- [x] 1.2 Чистая функция `parse_placeholders(body) -> set[str]` (учёт экранирования `{{`/`}}`).
- [x] 1.3 Чистая функция `validate_template(key, body) -> list[Violation]`: пустой текст,
      недопустимый плейсхолдер, пропуск обязательного, сломанные скобки. (Возвращает структурные
      `Violation`, а не `list[str]`, чтобы формулировки ошибок остались в `messages.toml`.)
- [x] 1.4 Протокол `MessageTemplatesRepo`: `get(specialist_id, key)`, `upsert(specialist_id, key,
      body)`, `delete(specialist_id, key)`.
- [x] 1.5 Тесты домена: валидатор (все ветки), парсер плейсхолдеров, инвариант «каждый ключ
      каталога имеет дефолт в `messages.toml` и whitelist без имён».

## 2. Infrastructure — таблица и репозиторий

- [x] 2.1 ORM-модель `MessageTemplate` (`specialist_id` FK, `template_key`, `body`,
      `UNIQUE(specialist_id, template_key)`).
- [x] 2.2 Alembic-миграция `0009_message_templates`: создать таблицу `message_templates`;
      downgrade — drop. Проверен upgrade/downgrade round-trip.
- [x] 2.3 Реализация `MessageTemplatesRepo` (get/upsert/delete) на `AsyncSession`.
- [x] 2.4 Тесты репозитория: upsert-замена, get-после-delete, изоляция по `specialist_id`.

## 3. Services — разрешение и правка

- [x] 3.1 `resolve_template(repo, *, specialist_id, key, default) -> str` (override ?? дефолт).
      Дефолт передаётся строкой (а не `messages`), чтобы services не зависел от `bot.messages`;
      привязку ключ → дефолт держит `src/bot/client_templates.py`.
- [x] 3.2 `save_template_override(repo, *, specialist_id, key, body) -> list[Violation]`
      (валидирует; пишет только при отсутствии ошибок; лог `template.overridden`).
- [x] 3.3 `reset_template(repo, *, specialist_id, key) -> bool` (удаляет override; лог
      `template.reset`; возвращает, был ли override).
- [x] 3.4 Тесты сервисов: override побеждает дефолт, дефолт при отсутствии override, отказ
      сохранения при ошибках валидации, сброс возвращает дефолт.

## 4. Bot — раздел настроек

- [x] 4.1 Пункт «📝 Тексты для клиента» в `src/bot/handlers/settings.py`; список шаблонов с
      кнопками `tpl:edit:<key>` / `tpl:reset:<key>`.
- [x] 4.2 Правка: показать текущий текст + доступные плейсхолдеры (обязательные помечены), FSM-
      приём нового текста → `save_template_override` → сохранено / список ошибок.
- [x] 4.3 Сброс: `reset_template`; если override не было — сообщение «уже дефолт».
- [x] 4.4 Новые тексты раздела в `messages.toml` (`[templates]`, `[templates.labels]`,
      `[templates.defaults]`) + загрузка `TemplatesMessages`.
- [x] 4.5 Тесты хендлеров (фейковые `Message`/`CallbackQuery`): успешная правка, отказ при
      невалидном тексте (пустой/сломанные скобки/недопустимый/без обязательного), сброс.

## 5. Интеграция точек отправки (после мёрджа зависимостей)

- [x] 5.1 Перевести отправку клиенту в `appointment-notify` (create/reschedule/cancel, разовые
      и серийные) с `messages.*.format(...)` на `resolve_template(...).format(...)` — превью и
      реальная отправка в `schedule.py`.
- [x] 5.2 Перевести напоминание о записи (`appointment-reminder`) на `resolve_template`
      (`scheduler.py`, резолв per-specialist).
- [ ] 5.3 Перевести просьбу продлить абонемент (`subscription-payment-reminder`) на
      `resolve_template`. **Отложено**: change `subscription-payment-reminder` ещё не вмёржен,
      точки отправки нет. Ключ `payment_reminder` и его дефолт добавлены заранее (по решению
      разработчика) — останется только подключить точку отправки после мёрджа.
- [x] 5.4 Перевести `invite_forward` (`clients.py`) / `linked` (`start.py`) на
      `resolve_template`.
- [x] 5.5 Обновить дельты живых спек: `appointment-reminder` (текст из `appt_reminder`),
      `appointments` (notify-ключи), `clients` (`invite_forward`/`linked`). Спека
      `subscription-payment-reminder` обновляется вместе с её мёрджем (см. 5.3).

## 6. Документация

- [x] 6.1 `docs/database.md` — таблица `message_templates` + миграция.
- [x] 6.2 `docs/bot.md` — раздел настроек «Тексты для клиента» + секция `[templates]` каталога.
- [x] 6.3 `docs/features.md` — сценарий настройки текстов.
- [x] 6.4 `README.md` — раздел «Ключевые возможности» (настраиваемые тексты).
- [x] 6.5 `openspec validate client-message-templates` — проходит.

## 7. Приёмка

- [x] 7.1 `make check` зелёный (формат, линт, типы, 100% покрытие).
- [x] 7.2 Ручная проверка: правка `appt_reminder`, отказ при `{child}`/без `{time}`, сброс,
      разрешение override против дефолта. Выполнена разработчиком в живом Telegram.
