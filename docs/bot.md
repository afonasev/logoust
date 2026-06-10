# Telegram-бот

Бот построен на **aiogram 3** в режиме long-polling. Все пользовательские тексты — в `src/bot/messages.toml`, в коде формулировок нет.

## Сборка приложения

- `src/bot/dispatcher.py::build_dispatcher(messages, session_factory) -> Dispatcher` — собирает `Dispatcher`, подключает роутеры. **Порядок важен:** фичевые роутеры (`clients`, `schedule`, `settings`, `windows`) включаются раньше `start`-роутера — fallback `start` ловит любой текст (онбординг по вставленному коду), поэтому иначе он перехватывал бы reply-кнопки и ввод в визардах. Нераспознанный текст всё равно проваливается в `start`.
- `src/bot/handlers/start.py::build_router(messages, session_factory) -> Router` — регистрирует обработчик `/start` и fallback на вставленный код/ссылку. Различает онбординг специалиста (токен без префикса) и привязку клиента (токен с префиксом `cli_`, см. `src/bot/deeplink.py`): `cli_`-payload идёт в `link_client_by_token`, остальное — в специалистский `consume_invite`.
- `src/bot/handlers/clients.py::build_router(messages, session_factory) -> Router` — роутер управления клиентами: reply-кнопка, inline-меню, визард добавления (FSM), карточка, редактирование полей, архив/возврат, списки. Из карточки клиента ведут переходы в записи (`sched:*`). Состояние FSM — на дефолтном `MemoryStorage` (теряется при рестарте процесса).
- `src/bot/handlers/schedule.py::build_router(messages, session_factory) -> Router` — роутер записей. Объединяет разовые и регулярные записи; колбэки — `sched:<action>[:<args>]` и `recur:<action>[:<args>]`. Подробно:
  - **Разовые записи.** Инлайн-календарь и сетка слотов с пометкой занятости (`build_calendar`, `build_slots_keyboard`; для регулярных пикеров — `build_recur_slots_keyboard` с третьим маркером 🟡 «текущее время» через `current`), FSM свободного времени и комментария, карточка записи (перенос/удаление с подтверждением, правка комментария — `sched:cmt:`, FSM `Schedule.edit_comment`).
  - **Экраны расписания.** Раздел «📅 Расписание» открывается на **сегодня** с навигацией по дням (◀/▶) и кнопкой «📆 Неделя»; история специалиста — текстом, по календарным неделям. Будущие записи клиента — кнопками в его карточке, история клиента — текстом по отдельной кнопке.
  - **Регулярные записи** (`RecurringHandlers`, регистрация — `_register_recurring`): двухуровневые карточки (расписание / отдельная встреча), визард создания, «Настроить» (per-slot правка/добавление/удаление), действия пропуска/переноса/комментария отдельной встречи и остановки всего расписания. Колбэк-схема `recur:<action>[:<args>]`:
    - визард — `recur:add` / `recur:done` / `recur:wd:` / `recur:tslot:` / `recur:tother` / `recur:cskipc` / `recur:cancel`;
    - карточки — `recur:sched:` / `recur:occ:`;
    - настройка — `recur:cfg:` / `recur:cfgadd:` / `recur:slot:` / `recur:slottime:` / `recur:slotday:` / `recur:slotdel:`;
    - остановка — `recur:stopask:` / `recur:stop:`;
    - общий комментарий серии (из карточки расписания) — `recur:schedcmt:` (FSM `Recurring.sched_comment`);
    - отдельная встреча — `recur:occmove:` / `recur:occskipask:` / `recur:occskip:` / `recur:occcmt:`, календарь переноса — `recur:cal:` / `recur:day:` (создание идёт через обычный поток записи, см. ниже).
  - **Виртуальные повторы.** Будущие повторы подмешиваются в день/неделю/занятость/карточку клиента как виртуальные `Appointment` (`id=None`, `slot_id`/`origin_date` заполнены). Пометку 🔁 несёт транзиентный флаг `recurring_mark` — он стоит только у «чистой» будущей occurrence; перенесённая отдельная дата индивидуализируется и значка не получает; тап ведёт на карточку расписания, а не записи.
  - **Единое правило занятости** во **всех** пикерах слотов (`pick_day`, `pick_move_day`, `pick_weekday`, `start_slot_time`): `taken_slot_times` отдаёт настенные **начала** записей (разовых + повторов серий), а чистая `domain.schedule.occupied_grid_slots(grid, starts, slot_minutes)` разворачивает их в занятые ячейки сетки (`|начало − слот| < slot_minutes`) — поэтому запись вне сетки (14:10) помечает обе соседние ячейки. Регулярные пикеры берут опорной **ближайшую дату выбранного дня недели ≥ сегодня**; при правке слота его вклад исключается (`exclude_slot_id` через `taken_slot_times`→`slot_taken_times`), а текущее время помечается 🟡. Перед сеткой печатается текстовый список записей дня (`render_day_appointments_list`, общий хелпер `_picker_day_view`).
- `src/bot/navigation.py::Navigator` — нейтральный реестр «`back`-префикс → корутина `(specialist_id, back) -> (text, keyboard)`», собираемый в `schedule.build_router` (`_build_navigator`) из «чистых» билдеров роутеров `schedule`/`recurring`/`clients`. Позволяет действию в одном роутере переоткрыть меню, которым владеет другой (например карточку клиента из роутера записей), без ре-диспатча синтетического колбэка и без перекрёстного импорта хендлеров (`clients` никогда не импортирует `schedule`). См. «Навигация после необратимых действий».
- `src/bot/handlers/settings.py::build_router(messages, session_factory) -> Router` — роутер настроек расписания. Колбэки — `settings:<action>[:<value>]`. Содержит:
  - меню и выбор таймзоны из списка российских зон;
  - FSM-ввод начала/конца дня и длины слота с валидацией;
  - мульти-тоггл рабочих дней недели (немедленное сохранение);
  - тумблер авто-напоминаний клиентам + FSM-ввод времени напоминаний;
  - тумблер утренней сводки специалисту + FSM-ввод её времени + кнопка «📤 Отправить сейчас» (немедленная проверочная отправка мимо расписания);
  - тумблер напоминания об оплате абонемента (`settings:payment`) + FSM-ввод времени (`settings:payment_time`);
  - тумблер вечернего авто-списания (`settings:consumption`) + FSM-ввод времени (`settings:consumption_time`) + кнопка «📤 Отправить сейчас» (`settings:consumption_now` → реальный прогон мимо времени и дневного флага);
  - заголовки блоков фич — кликабельные кнопки `settings:help:<feature>` (`reminder`/`digest`/`payment`/`consumption`): показывают краткую справку всплывающим окном (`show_help`, ранее был немой `noop`).
  - Раздел **«📝 Тексты для клиента»** (`settings:templates`): список настраиваемых клиентских шаблонов с действиями правки (`tpl:edit:<key>`, FSM `EditTemplate.body`) и сброса к дефолту (`tpl:reset:<key>`). Каталог ключей и whitelist плейсхолдеров — `CLIENT_TEMPLATES` в домене; правка проходит строгую валидацию (`save_template_override`), сброс удаляет override (`reset_template`).
- `src/bot/handlers/subscriptions.py::build_router(messages, session_factory) -> Router` — роутер абонементов. Колбэки — `subs:<action>[:<id|page>]`, переиспользует `SpecialistMiddleware`. Состав:
  - reply-кнопка «🎫 Абонементы» (раздел со списком активных абонементов всех клиентов и историей закрытых, постранично); зарегистрирована до FSM-хендлеров (escape-hatch из визарда, как «Клиенты»);
  - карточка абонемента, FSM ввода числа встреч при создании/продлении (с кнопкой принятия дефолта специалиста), действия списания/закрытия;
  - создание инициируется кнопкой на карточке клиента; «Назад» с карточки абонемента ведёт на карточку клиента (`clients:card:<id>`);
  - карточка **закрытого** абонемента — read-only (только возврат к клиенту).
  - **Журнал списаний** на карточке — кнопками (`subs:ded:<deduction_id>` на каждое неотменённое списание; на закрытом — только для чтения). Экран списания показывает дату, дату/время встречи (для авто-списания), снимок комментария записи и комментарий после встречи. На активном абонементе — «✏️ Комментарий» (`subs:dedcomment:<id>`, FSM `SubscriptionFlow.closing_comment`) и «↩️ Отменить списание» (`subs:dedcancel:<id>`, остаток +1, строка скрывается из истории).
  - Префиксы `subs:dedcancel:` / `subs:dedcomment:` / `subs:ded:` disjoint по двоеточиям, регистрируются именно в этом порядке.
- `src/bot/handlers/reminders.py::build_router(messages, session_factory) -> Router` — **клиентский** роутер ответа на напоминание (колбэк `appt:cfm:<reminder_id>:<y|n>`). Вне `SpecialistMiddleware`: актор — клиент. Записывает ответ (`apply_reminder_response`), отвечает клиенту тостом; при переходе в `declined` шлёт специалисту сообщение с кнопкой «→ Открыть запись». Включён в dispatcher до `start`-роутера.
- `src/bot/handlers/payment.py::build_router(messages, session_factory) -> Router` — роутер отправки напоминания об оплате клиенту по тапу специалиста (колбэк `pay:send:<client_id>`). Переиспользует `SpecialistMiddleware` (актор — специалист). Грузит клиента (сверка владельца), повторно проверяет привязку (`telegram_chat_id`), отправляет фиксированный шаблон `payment_reminder` и журналирует исход через воронку `record_client_message` (SENT/FAILED); нет привязки → ответ «клиент не привязан». Кнопку рождает фоновый проход `run_payment_reminder_pass` (см. `docs/architecture.md`).
- `src/bot/handlers/windows.py::build_router(messages, session_factory) -> Router` — роутер раздела «🪟 Окна»: по reply-кнопке рендерит список свободных окон на ближайшие 5 рабочих дней (`list_free_windows`) с inline-переключателем режима «Все окна» / «Смежные окна»; колбэки `windows:all` / `windows:adjacent` пересобирают окна и редактируют то же сообщение. `callback_query` идёт через ту же `SpecialistMiddleware`. Пагинации нет.
- `src/bot/handlers/audit.py::build_router(messages, session_factory) -> Router` — роутер раздела «📜 Аудит»: по reply-кнопке открывает ленту событий специалиста (новейшие сверху, `list_audit`) с пагинацией inline-кнопками `◀ позже` / `раньше ▶` (`audit:page:<n>`). Рендер строки зависит от `kind`: `message` — иконка статуса (✅/⚠️) + событие + клиент + текст, `action` — иконка действия + событие + клиент. Имена клиентов тянутся через `client_name_map`. Переиспользует `SpecialistMiddleware`.
- `src/bot/handlers/start.py::make_start_handler(messages, session_factory)` — фабрика хендлера `/start` (вынесена для удобства тестов).
- `src/bot/handlers/start.py::make_token_handler(messages, session_factory)` — фабрика fallback-хендлера: ловит обычный текст и через `extract_token()` достаёт токен из голого кода или deep-link.
- `src/__main__.py` — точка входа: грузит `settings`, инициализирует logging, создаёт `Bot`, `session_factory`, `messages`, `Dispatcher`, запускает `dp.start_polling(bot)` рядом с фоновым `_scheduler_loop(...)` — минутным планировщиком (см. `docs/architecture.md`). На каждом тике выполняются пять проходов: напоминания клиентам, утренняя сводка специалисту, напоминание об оплате абонемента, **вечерний авто-учёт расхода абонементов** и доставка отложенных уведомлений клиенту. Тестируемые проходы — `src/bot/scheduler.py::run_reminder_pass`, `run_digest_pass`, `run_payment_reminder_pass`, `run_consumption_pass` и `run_outbox_pass`.

## Хендлеры

### Онбординг и привязка — `src/bot/handlers/start.py`

**`/start [<token>]`.** Разбор токена:

- префикс `cli_` → привязка клиента (`link_client_by_token`, idempotent rebind `chat_id`/`linked_at`; `from_user.username` автозаполняет пустой `contact_telegram`; ответы `clients.linked` / `clients.link_unknown`);
- иначе — онбординг специалиста: ищет по токену, идемпотентно проставляет `chat_id`/`username`/`welcomed_at`, четыре ветки (`welcomed`, `already_welcomed`, `unknown_token`, `no_token`).

**Текст с кодом/ссылкой** (`make_token_handler`) — fallback на вставленный голый токен или deep-link целиком. Сначала `extract_client_token` (ловит `cli_`-payload → привязка клиента), затем `extract_token` (специалистский онбординг). На сообщения, не похожие на токен, молчит. Зарегистрирован после `/start`.

### Клиенты — `src/bot/handlers/clients.py`

Колбэки по схеме `clients:<action>:<id>`.

**Кнопка «👶 Клиенты»** (`show_menu`/`show_active`) — reply-кнопка (после онбординга) открывает постраничный список активных клиентов (сортировка по `child_name`). Навигация `clients:active:<page>`, размер страницы — `_ACTIVE_PAGE_SIZE`; нажатие сбрасывает любой активный визард.

- Каждая строка (`_client_row`) — два столбца: клиент (→ `clients:card:<id>`) и его **ближайшая будущая запись** (`nearest_future_by_client`; компактно, без комментария; → карточка записи для переноса) либо «➕ Записать» (`sched:new:<id>`), если записей нет.
- Внизу — «➕ Добавить» и «🗄 Архив».

**Карточка и управление** (`ClientsHandlers`):

- Добавление — FSM: имя ребёнка → имя контакта → телефон → telegram.
- Карточка: общая информация, затем **будущие записи кнопками** (`_future_button` → `sched:card:<id>`, тап открывает запись для переноса/отмены), «➕ Записать», строка «✏️ Изменить / 🗂 История / 📦 В архив» и «⬅️ Назад». Ближайшая occurrence активного расписания показывается среди будущих с пометкой 🔁 (`_appt_callback` → `recur:occ:<slot>:<origin_date>~<back>`).
- **Кнопка абонемента** (создать или перейти с остатком, см. раздел «Абонементы»).
- **Приглашение в бота** (`clients:invite:<id>`, `send_invite`): лениво выдаёт `cli_`-токен (переиспользуя существующий), отдельным **пересылаемым** сообщением шлёт ссылку `t.me/<bot>?start=cli_<token>` (`build_client_start_link`). Подпись отражает состояние: «Пригласить в бота» / «✅ Привязан» при заполненном `telegram_chat_id`.
- **Отложенные уведомления в очереди** (`list_queued_for_client`): блок `dnotify_title` со строками «время · текст» и кнопкой отмены у каждого (`clients:dnotify:cancel:<client_id>:<message_id>`, `cancel_deferred_notify` — owner-scoped, перерисовывает карточку). Без очереди блока нет.
- Архивация — **с подтверждением** (`clients:archiveask:<id>` → `clients:archive:<id>`); возврат из архива — сразу. История записей клиента — **только текстом**, с пагинацией, без кнопок.
- Кнопка «⬅️ Назад» **контекстная** (origin в `clients:card:<id>~<back>`; по умолчанию — список активных/архив по статусу).

**Архив клиентов** (`show_archive`) — постраничный, сортировка по `archived_at` убыванию (свежие сверху), дата архивации в строке. Размер страницы — `_ARCHIVE_PAGE_SIZE`. Навигация: `clients:list:archived` (стр. 0) и `clients:arch:<page>`; «⬅️ Меню» (`clients:menu`) возвращает к активному списку. Наличие следующей страницы — выборкой `page_size + 1` строки (без COUNT).

### Ответ клиента на напоминание — `src/bot/handlers/reminders.py`

**Колбэк `appt:cfm:`** (`ReminderHandlers`) — **клиентский** ответ на напоминание (вне `SpecialistMiddleware`).

- `appt:cfm:<reminder_id>:y` → `confirmed`, `:n` → `declined` (`apply_reminder_response`, изоляция по `chat_id` ответившего). Клиенту — тост.
- При переходе в `declined` специалисту шлётся `reminder.specialist_declined` с кнопкой «→ Открыть запись»: разовая → `sched:card:<id>` (через `find_by_occurrence`); виртуальный повтор → `recur:occ:<slot>:<origin_date>`; перенесённая/исчезнувшая разовая → `sched:day_view:<iso>`.
- Ответ можно менять; повторный тот же ответ специалиста не дёргает.

### Расписание и записи — `src/bot/handlers/schedule.py`

**Кнопка «📅 Расписание»** (`show_feed`/`open_day`/`show_week`) — открывает расписание на **релевантный день**: сегодня, если сегодня рабочий день или есть записи, иначе ближайший вперёд **показываемый** день (`schedule_landing_day`).

- Записи дня — каждая отдельной кнопкой `время · ребёнок` → карточка, с префиксом статуса ответа клиента (`statuses_for_appointments` → `ReminderMessages.status_mark`): ✅ при `confirmed`, ❌ при `declined`, ничего при `pending`/без напоминания. На карточке записи/серии при `confirmed` — строка-пометка `reminder.card_confirmed`. Те же ✅/❌ — у ближайшей записи в списке клиентов и у будущих записей на карточке клиента.
- ◀/▶ (`sched:day_view:<iso>`) ведут на ближайший показываемый день в направлении (`adjacent_shown_day`, series-aware — `SeriesContext` в оба вызова): пустые нерабочие дни пропускаются, а нерабочий день с записью **или** с приземлением будущего повтора активной серии (с учётом пропусков/переносов) остаётся доступным; назад серии не учитываются (прошлое — только реальные строки); нет дня в сторону → стрелка не рисуется.
- «📆 Неделя» (`sched:week`) — read-only обзор 7 дней (сгруппированный текст; пустые дни не выводятся). «🗂 История» — прошедшие записи **только текстом**, по дням, навигация **по календарным неделям** (пн–вс, `sched:hist:<week>`, ◀ старше / ▶ новее), без кнопок на записи.

**Запись на приём** (`ScheduleHandlers`) — из карточки клиента: календарь (прошлое заблокировано) → слот из сетки или «Другое время» (`ЧЧ:ММ`) → опциональный комментарий → карточка записи.

- Слоты помечаются 🟢 (свободен) / 🔴 (на это время уже есть запись; `taken_slot_times`), без запрета бронирования. Перенос переиспользует тот же пикер, меняет только `starts_at` и исключает свой текущий слот из «занятых»; удаление — с подтверждением `[Да, удалить] [Отмена]`.
- Кнопка «⬅️ Назад» на карточке записи **контекстная** — возвращает туда, откуда открыли (origin в `sched:card:<id>~<back-callback>`; по умолчанию — день записи).

**Шаг «Уведомить клиента?»** — **последним шагом** создания/переноса/отмены, **только если у клиента заполнен `telegram_chat_id`**, бот отдельным сообщением задаёт вопрос (`notify_ask`) с **предпросмотром текста** и кнопками `[✅ Да, отправить] [Нет]` (`_ask_notify`/`_notify_ask_keyboard`).

- Контекст уведомления (событие, `client_id`, снимок `chat_id`, снимок текста, устойчивый `target_key`, `back`-токен карточки) складывается в **FSM-данные** (не в `callback_data` — устойчиво к лимиту 64 байта).
- «Да» (`sched:ntfwhen`) **не отправляет сразу**, а показывает **выбор момента**:
  - «📤 Сейчас» (`sched:ntfnow`);
  - «🕗 в HH:MM» — пресет `deferred_notify_time` (`sched:ntfpreset`);
  - «🕑 Своё время» (`sched:ntfcustom` → FSM `Schedule.notify_custom_time`, ввод `ЧЧ:ММ`; под промптом «Отмена» `sched:ntfcancel` → `notify_custom_cancel`: сбрасывает только FSM-state ввода, сохраняет notify-контекст, возвращает к выбору момента, ничего не ставя в очередь).
  - «Нет» — `sched:ntfno`.
- **Сейчас** (`notify_now`) — прежнее немедленное поведение: повторная проверка привязки (`get_for_specialist`; иначе `notify_not_linked`), отправка снимка текста и `notify_sent`/`notify_failed`.
- **Пресет/своё время** (`notify_preset`/`apply_notify_custom_time`) считают `due_at` через `next_occurrence_utc` (ближайшее наступление в tz), ставят снимок в очередь `scheduled_client_messages` (`enqueue_deferred`), отвечают `notify_deferred_queued`; при вытеснении прежней `queued`-строки той же `target_key` — `notify_deferred_superseded`.
- Если FSM-контекст утерян (другая операция вызвала `state.clear()`) — под-кнопки отвечают `notify_session_stale`, ничего не отправляя.
- После завершения сценария карточка сущности **переоткрывается последней** через `Navigator` (`_open_card`) — интерфейс не «прыгает».
- `target_key`: `appt:<id>` для разовой (для отмены фиксируется до удаления; см. `appointment_target_key`); для регулярных — `schedule_target_key(id)` (всё расписание) / `slot_date_target_key(slot, origin_date)` (отдельная встреча).
- Тот же шаг — и для **регулярных записей**: для расписания целиком текст про правило (`notify_series_created`/`notify_series_changed`/`notify_series_cancelled`, `_ask_series_notify`), для отдельной встречи — текст про конкретные дату/время. Непривязанному клиенту шаг не показывается; обычный просмотр карточки (`show_card`) уведомление не предлагает.

**Регулярная запись** (`ScheduleHandlers`, `RecurringHandlers`). Единица повтора — **расписание** клиента (`recurring_schedule`), владеющее N **слотами** (`recurring_slot`: день недели + время); в одном расписании может быть несколько встреч в неделю и даже два слота в один день.

- **Визард создания** входит из обычного потока записи: после выбора времени бот спрашивает «сделать регулярной?» (`sched:reg:1`/`sched:reg:0`, `choose_regular`) перед комментарием.
  - При «да» первый слот **засевается** из выбранной даты+времени (`weekday`/`start_date` = выбранная дата), затем цикл «добавить ещё один день?» (`add_more`): «➕ Добавить день» (`recur:add`) → день недели (`recur:wd:`) → время (`recur:tslot:`/`recur:tother`) → снова вопрос. «Готово, дальше» (`recur:done`) → **один общий комментарий расписания** (`Recurring.comment` / `recur:cskipc`) → `create_schedule` + `add_slot` на каждый слот → карточка расписания.
  - При «нет» → обычная разовая запись.
- **Две карточки** (тап по 🔁-повтору в дне/неделе/карточке клиента → карточка расписания):
  - Карточка расписания (`recur:sched:<id>[~<back>]`, `show_schedule`): правило (список активных слотов, `rule_line`) + ближайшие occurrence на **скользящие 14 дней** кнопками (`occ_btn`) + «⚙️ Настроить» (`recur:cfg:`) / «🗑 Отменить всё» (`recur:stopask:` → `recur:stop:`, с подтверждением).
  - Карточка встречи (`recur:occ:<slot>:<date>[~<back>]`, `show_meeting`): одна occurrence (дата/время с учётом переноса, эффективный комментарий `override.comment ?? schedule.comment`) и действия «📅 Перенести» / «🗑 Отменить» / «🗒 Комментарий» / «🔁 К расписанию». Каждое действует ровно на `(slot, original_date)`: перенос (`recur:occmove:`, `start_move`) — календарём (`recur:cal:`/`recur:day:`) → сеткой слотов → `move_occurrence`; отмена (`recur:occskipask:` → `recur:occskip:`, `skip_occurrence`) с подтверждением; комментарий (`recur:occcmt:`, FSM `Recurring.occ_comment` → `set_occurrence_comment`).
- **«Настроить»** (`show_config`) — список слотов (`recur:slot:`) + «➕ Добавить день» (`recur:cfgadd:`); по слоту — сменить время (`recur:slottime:`), сменить день (`recur:slotday:`), удалить (`recur:slotdel:`). Удаление **последнего** активного слота останавливает всё расписание (`remove_slot`).
- Кнопки «⬅️ Назад» и «Отмена» **контекстные** (callback в `~<back>` и в FSM-состоянии). Перенесённая отдельная дата индивидуализируется — значка 🔁 нет. Все действия ограничены своими расписаниями/слотами (`get_for_specialist`).
- После операции — **только для привязанного клиента** — шаг «Уведомить клиента?» с выбором момента (см. выше): создание/«Настроить»/«Отменить всё» → текст про правило (`_ask_series_notify`, `target_key` = `schedule_target_key(id)`); «Перенести»/«Отменить» отдельной встречи → текст про конкретную дату (`_ask_notify`, `target_key` = `slot_date_target_key(slot, origin_date)`).

### Настройки — `src/bot/handlers/settings.py`

**Кнопка «⚙️ Настройки»** (`SettingsHandlers`) — открывает настройки расписания. Новое значение сразу применяется к генерации сетки, трактовке времени и расчёту окон.

- **Базовые поля:** таймзона (из списка рос. зон), начало/конец рабочего дня и длина слота (FSM-ввод с валидацией).
- **Рабочие дни** — экран мульти-тоггла (7 кнопок ✅/⬜ Пн–Вс + «⬅️ Назад»): тап инвертирует день (`settings:wd:<idx>`), сразу сохраняет канонизированный набор (`toggle_working_day`) и перерисовывает клавиатуру, без подтверждения.
- **Напоминания клиентам:** тумблер `settings:reminder` (`toggle_reminder`), `settings:reminder_time` (FSM-ввод `ЧЧ:ММ`) и `settings:reminder_now` (`send_reminders_now`) — немедленная проверочная рассылка на завтра в обход гейта по времени, дневного флага и тумблера. Доставка/аудит — той же воронкой, что боевой проход (`scheduler.deliver_reminder`: текст `appt_reminder`, кнопки «приду/не приду», запись в `audit_log` SENT/FAILED); журнал дедуплицирует per-occurrence; дневной флаг **не** проставляется. Итог — «отправлено: N» / «некого напоминать».
- **Утренняя сводка:** тумблер `settings:digest` (`toggle_digest`), `settings:digest_time` (FSM `ЧЧ:ММ`) и `settings:digest_now` — немедленная отправка за сегодня (мимо `is_digest_due`, **без** пометки дня; пустой день → «записей нет»).
- **Напоминание об оплате:** тумблер `settings:payment` (`toggle_payment_reminder`) и `settings:payment_time` (FSM `ЧЧ:ММ`).
- **Варианты числа встреч в абонементе** — `settings:subscription_presets` (FSM-ввод списка через запятую, например `4,8,12`; канонизируется — по возрастанию, без повторов); из них строятся кнопки при создании/продлении.
- **«📝 Тексты для клиента»** (`settings:templates`) — список клиентских шаблонов (`CLIENT_TEMPLATES`); правка показывает текущий текст и доступные плейсхолдеры (обязательные ⭐), принятый текст проходит строгую валидацию whitelist/обязательных (иначе ошибка и остаётся в FSM). «↩️ Сброс» удаляет override (или «уже стандартный»). Только обезличенные плейсхолдеры (дата/время/правило/ссылка) — имён нет.
- Каждый шаг FSM-ввода свободного текста дописывает к промпту текущее значение строкой «Сейчас: {current}» (`value_now`) и показывает кнопку «Отмена» (`settings:menu` → `open_menu`: сброс state + меню, без сохранения).

### Абонементы — `src/bot/handlers/subscriptions.py`

**Кнопка «🎫 Абонементы»** (`SubscriptionsHandlers`) — постраничный список **активных** абонементов всех клиентов (строка `имя · осталось N` → карточка, `subs:active:<page>`) с кнопкой «🗄 Закрытые». История закрытых (`subs:closed:<page>`) — строка `имя · закрыт {дата}`, кнопка «⬅️ К активным». Имена — через `client_name_map`. Карточка **закрытого** абонемента — read-only.

**Карточка и действия:**

- На активной карточке клиента — кнопка абонемента: «➕ Создать абонемент» (`subs:create:<client_id>`), если активного нет, либо «🎫 Абонемент · {остаток}» (`subs:card:<sid>`), если есть; на архивной карточке скрыта.
- Создание/продление спрашивают число встреч (FSM) с кнопками-вариантами из настройки (`subs:createval:<client_id>:<n>` / `subs:extendval:<sid>:<n>`); можно ввести своё число, невалидный ввод переспрашивается.
- Карточка: дата создания, куплено, остаток и действия «➖ Вычесть встречу» (`subs:dec:`, −1, alert при остатке 0), «➕ Продлить» (`subs:extend:`, оба счётчика +N), «✖ Закрыть» (`subs:closeask:` → `subs:close:`, с подтверждением, запись не удаляется).
- Не более одного активного абонемента на клиента; все действия ограничены своими клиентами.

### Напоминание об оплате (отправка клиенту) — `src/bot/handlers/payment.py`

`PaymentHandlers`. Фоновый проход накануне присылает специалисту алерт «У клиента {имя} закончился абонемент, завтра в {время} запись. Отправить напоминание об оплате?» с превью текста для клиента; у привязанного клиента под алертом кнопка «📨 Отправить» (`pay:send:<client_id>`).

- Тап шлёт клиенту фиксированный шаблон `payment_reminder`, журналирует исход (`record_client_message`, событие `payment_reminder`, SENT/FAILED) и отвечает: «отправлено» / «не доставлено» / «клиент не привязан» (повторная проверка `telegram_chat_id` на случай отвязки).
- У непривязанного клиента кнопки нет — специалист напоминает вручную.

### Окна — `src/bot/handlers/windows.py`

**Кнопка «🪟 Окна»** (`WindowsHandlers`) — одним сообщением свободные окна на ближайшие 5 рабочих дней, сгруппированные по дням.

- Окно = слот сетки без записи на это время; для сегодня прошедшие окна скрыты. День без окон всё равно показывается со строкой «свободных окон нет» (предсказуемый счёт пяти дней). Рабочие дни не заданы (`working_days` пуст) → подсказка открыть «Настройки» (без клавиатуры режимов).
- Под сообщением — inline-переключатель «Все окна» (по умолчанию) / «Смежные окна» с пометкой активного «●»; колбэки `windows:all` / `windows:adjacent` (`switch`) редактируют то же сообщение. «Смежные» = только окна, у которых соседний слот сетки занят (границы дня — не сосед, пустой день окон не даёт). Пагинации нет.

### Аудит — `src/bot/handlers/audit.py`

**Кнопка «📜 Аудит»** (`AuditHandlers`) — лента значимых событий специалиста, новейшие сверху, постранично (`audit:page:<n>`, размер страницы `_PAGE_SIZE`).

- `message` (исходящее клиенту) — иконка доставки (✅ `sent` / ⚠️ `failed`), событие, имя клиента и полный текст.
- `action` (ключевое действие специалиста) — иконка 📌, событие и имя клиента (где есть).
- Пустой журнал → «журнал пуст», без кнопок. Специалист видит только свои строки.

---

Доступ ограничивает `SpecialistMiddleware` (inner-middleware, переиспользуется всеми фичевыми роутерами): резолвит специалиста по `chat_id` через `SpecialistsRepo.find_by_chat_id`, кладёт `specialist_id` в данные хендлера, а апдейты от неонбординнутых пользователей роняет. На каждое действие специалиста middleware вызывает `settle` — материализует прошедшие occurrence активных серий в строки `appointments` (планировщика нет; идемпотентно, с дневным guard'ом через `materialized_through`).

Других команд бота (включая `/help`) сознательно нет — приветствие не должно врать о возможностях.

## Навигация после необратимых действий

После необратимого действия с записью (удаление разовой, перенос/остановка/пропуск даты серии, отмена флоу) бот **не** показывает тупиковый экран с единственной кнопкой «Назад». Единый хелпер `Navigator.open_after_action` (через тонкий шим `schedule._post_action`):

1. оставляет **отдельное сообщение-результат** в истории чата («Запись удалена/перенесена/…»). В колбэк-флоу (`edit=True`) исходная карточка с уже неактуальными кнопками редактируется в текст результата без клавиатуры — мёртвых кнопок не остаётся; во флоу, завершённом введённым текстом (`edit=False`, своё сообщение пользователя редактировать нельзя), результат шлётся новым сообщением;
2. сразу открывает **исходное меню** (`Navigator.render` по префиксу `back`) новым сообщением — самый свежий экран. Цель определяется тем же `back`-колбэком, что нёс бывший «Назад»: `sched:day_view:`/`sched:feed` → день, `recur:sched:` → карточка расписания, `recur:occ:` → карточка встречи, `clients:card:`/`clients:active:`/`clients:arch:` → картотека, `sched:chist:` → история клиента. Неизвестная/пустая цель → сегодняшний день расписания (fallback).

Если у клиента заполнен `telegram_chat_id`, открытие карточки **откладывается до конца** шага-вопроса «Уведомить клиента?» (см. таблицу хендлеров): сначала результат + вопрос/выбор момента, и лишь после завершения сценария (Сейчас/пресет/своё/Нет) карточка сущности переоткрывается через `Navigator` как последний экран (`_open_card`) — чтобы карточка не мелькала в середине потока. `back`-токен карточки переносится в FSM вместе с контекстом уведомления; для разовой/перенесённой записи это `sched:card:<id>~<inner>` (новый билдер `nav_appt_card`). Непривязанному клиенту карточка открывается сразу. Промежуточные экраны-**подтверждения** (`[Да, удалить] [Отмена]` и т.п.) правилом не затронуты — у них есть осмысленный выбор.

## Каталог текстов

Файл: `src/bot/messages.toml`. Загрузка: `src/bot/messages.py::load_messages(path) -> BotMessages` (frozen dataclass).

Обязательные ключи (отсутствие → `RuntimeError` на старте, до приёма апдейтов):

- `[start].welcome` — приветствие при первом валидном `/start <token>`.
- `[start].already_welcomed` — ответ на повторный `/start` уже использованного токена.
- `[start].unknown_token` — ответ, если токен не найден.
- `[start].no_token` — ответ, если `/start` пришёл без payload.
- `[clients].*` — секция текстов управления клиентами: `button`, заголовки меню и списков (`menu_title`, `list_active_title`, `list_archived_title`, `empty_active`, `empty_archived`), шаблон карточки `card` и метки статуса (`status_active`/`status_archived`, `dash`), приглашения визарда (`ask_child_name`, `ask_contact_name`, `ask_phone`, `ask_telegram`) и подтверждения/ошибки (`added`, `archive_confirm`, `archived`, `restored`, `updated`, `cancelled`, `empty_required`, `need_contact_channel`, `edit_prompt`, `not_found`), а также приглашение клиента в бота (`invite_button`, `invite_button_linked`, `invite_forward`, `linked`, `link_unknown`, `tg_linked_badge` — бейдж «в боте» в поле Telegram карточки) и подписи кнопки абонемента на карточке (`btn_subscription_create`, `btn_subscription_open` с `{remaining}`) и блок отложенных уведомлений (`dnotify_title`, `dnotify_line` с `{when}`/`{text}`, `dnotify_cancel` с `{when}`). Короткие подписи inline-кнопок живут константами в `clients.py` (навигационная «обвязка», не сценарные тексты).
- `[schedule].*` — секция записей: `button`, день/неделя/история (`day_title`, `day_empty`, `week_title`, `week_empty`, `history_title`, `history_empty`), записи клиента (`client_future_empty`, `client_history_title`, `client_history_empty`), инлайн-блок будущих в карточке клиента (`card_future_title`, `card_appt_line`), шаблоны строк (`line`, `line_full`, `day_header`, `comment_suffix`), шаблон карточки `card`, тексты пикера и FSM (`pick_date`, `pick_time`, `ask_custom_time`, `bad_time`, `ask_comment`), подтверждения (`created`, `rescheduled`, `deleted`, `not_found`, `confirm_delete`) и подписи кнопок (`btn_*`). Шаг уведомления: предпросмотр и Да/Нет (`notify_ask`, `notify_yes`, `notify_no`), тексты клиенту (`notify_created`/`notify_rescheduled`/`notify_cancelled`, `notify_series_*`), исходы (`notify_sent`, `notify_failed`, `notify_not_linked`, `notify_skipped`); выбор момента и отложенная отправка (`notify_when_ask`, `notify_when_now`, `notify_when_preset` с `{time}`, `notify_when_custom`, `notify_custom_time_ask`, `notify_custom_cancel`, `notify_deferred_queued`/`notify_deferred_superseded` с `{when}`, `notify_session_stale`, `notify_deferred_failed` с `{child}` — сообщение специалисту о сбое отложенной доставки). Подписи навигации календаря (`◀`/`▶`, дни недели) и маркеры занятости слотов (🟢/🔴) — константы в `schedule.py`.
- `[recurring].*` — секция регулярных записей (ключи переписаны под двухуровневые карточки и мульти-слот): `mark` (🔁-пометка повтора), приглашения и FSM (`pick_weekday`, `pick_move_date`, `pick_time`, `ask_custom_time`, `bad_time`, `ask_comment`), `line`; визард создания (`add_more`, `btn_add_more`, `btn_done`); карточка расписания (`schedule_card`, `rule_line`, `empty_window`, `occ_btn`, `btn_configure`, `btn_stop`); карточка встречи (`meeting_card`, `btn_move`, `btn_skip`, `btn_comment`, `btn_to_schedule`); настройка (`configure_title`, `slot_btn`, `slot_actions_title`, `btn_slot_time`, `btn_slot_day`, `btn_slot_delete`, `btn_add_day`, `slot_removed`, `edited`); подтверждения и исходы (`confirm_stop`, `btn_confirm_stop`, `stopped`, `cancelled`, `skip_confirm`, `btn_confirm_skip`, `skipped`, `moved`, `ask_occ_comment`, `comment_set`, `created`, `not_found`). Прежние ключи одноуровневой карточки серии удалены (`card`, `btn_edit`, `btn_move_date`, `btn_skip_date`).
- `[reminder].*` — секция авто-напоминаний клиенту: `client_text` (шаблон `{child}`/`{date}`/`{time}` сообщения клиенту), подписи кнопок (`btn_confirm`, `btn_decline`), тосты ответа (`confirmed_toast`, `declined_toast`), уведомление специалисту об отказе (`specialist_declined`, `btn_open_appt`), индикаторы статуса в списках записей (`confirmed_mark` ✅, `declined_mark` ❌) и пометка подтверждения на карточке (`card_confirmed`).
- `[settings].*` — секция настроек: `button`, шаблон `title` (текущие значения, включая `working_days`, напоминания `{reminders}`/`{reminder_time}` и утреннюю сводку `{digest}`/`{digest_time}`), подписи кнопок (`btn_timezone`, `btn_day_start`, `btn_day_end`, `btn_slot`, `btn_working_days`, `btn_reminder` с `{state}`, `btn_reminder_time`, `btn_reminder_now`, `btn_digest` с `{state}`, `btn_digest_time`, `btn_digest_now`, `btn_payment` с `{state}`, `btn_payment_on`/`btn_payment_off`, `btn_payment_time`, `btn_consumption`/`btn_consumption_on`/`btn_consumption_off`/`btn_consumption_time`/`btn_consumption_now`, справка по блокам `help_reminder`/`help_digest`/`help_payment`/`help_consumption`, `state_on`, `state_off`, `btn_back`, `btn_cancel`), приглашения и ошибки FSM (`pick_timezone`, `pick_working_days`, `ask_day_start`, `ask_day_end`, `ask_slot`, `ask_reminder_time`, `ask_digest_time`, `ask_payment_time`, `ask_consumption_time`, `ask_subscription_presets`, `ask_deferred_time`, `consumption_now_empty`, `consumption_now_done`, `no_working_days`, `bad_time`, `bad_slot`, `bad_subscription_presets`, `digest_now_empty`, `digest_now_failed`, `reminders_now_empty`, `reminders_now_done` с `{count}`, `value_now` с `{current}`, `saved`, `not_found`), а также `btn_subscription_presets`/`subscription_presets` и `btn_deferred_time`/`deferred_notify_time` (время пресета отложенной отправки) в шаблоне `title`. Список таймзон — `RUSSIAN_TIMEZONES`, короткие подписи дней — `RU_WEEKDAYS_SHORT` в `src/domain/schedule.py`.
- `[payment].*` — секция напоминания об оплате абонемента: `alert` (сообщение специалисту с `{child}`/`{time}`/`{preview}`), `btn_send` (кнопка «📨 Отправить»), исходы тапа `sent`/`not_delivered`/`not_linked`. Загружается в `PaymentMessages`. Фиксированный текст для клиента — клиентский шаблон `payment_reminder` (`[templates.defaults]`).
- `[consumption].*` — секция отчёта вечернего авто-списания: `title`, заголовок списанных (`deducted_header`) и шаблон кнопки абонемента (`deducted_btn` с `{child}`/`{remaining}`), заголовок ❗-списка (`missed_header`) и подписи кнопок ❗-встреч (`missed_no_subscription`/`missed_exhausted` с `{child}`/`{time}`), `now_empty`/`now_done`. Загружается в `ConsumptionMessages`; используется фоновым проходом и кнопкой «Отправить сейчас». Тексты экрана списания и журнала живут в `[subscriptions]`.
- `[digest].*` — секция утренней сводки специалисту: `title` (шаблон заголовка с `{date}`), `line` (строка встречи с `{time}`/`{child}`/`{comment}`), `comment_suffix` (обёртка комментария, подставляется только при наличии) и `dash`. Загружается в `services.digest.DigestMessages` и используется и фоновым проходом, и кнопкой «Отправить сейчас».
- `[windows].*` — секция раздела «Окна»: `button`, `title`, `day_header` (шаблон с `{date}`), `empty_day` (строка для дня без свободных окон), `no_working_days` (подсказка, когда рабочие дни не заданы), `button_all` / `button_adjacent` (подписи кнопок переключения режима показа окон).
- `[subscriptions].*` — секция абонементов: `button` (reply-кнопка раздела), заголовки и пустые состояния списков (`list_active_title`, `list_active_empty`, `list_closed_title`, `list_closed_empty`), шаблоны строк списков (`list_row_active` с `{child}`/`{remaining}`, `list_row_closed` с `{child}`/`{closed}`), кнопки навигации между списками (`btn_closed`, `btn_active`), пометка закрытого (`closed_note`), шаблон `card` (`{child}`/`{created}`/`{purchased}`/`{remaining}`), приглашения и ошибка FSM (`create_prompt`, `extend_prompt`, `bad_meetings`), подтверждения (`created`, `extended`, `decremented`, `nothing_to_decrement`, `close_confirm`, `closed`, `not_found`, `cancelled`), подписи кнопок (`btn_decrement`, `btn_extend`, `btn_close`, `btn_confirm_close`, `btn_cancel`, `btn_back_client`). Журнал и экран списания: шаблоны строк журнала (`journal_row_auto` с `{date}`/`{appt}`, `journal_row_manual` с `{date}`), экран (`ded_title`, `ded_created`, `ded_meeting`, `ded_manual`, `ded_record_comment`, `ded_closing_comment`, `ded_closing_empty`), кнопки и тексты (`btn_ded_comment`, `btn_ded_cancel`, `btn_back_card`, `ded_cancelled`, `ask_closing_comment`, `closing_comment_set`, `ded_not_found`). Кнопки-варианты числа встреч строятся из настройки `subscription_presets` (подпись = само число). Подписи пагинации (◀/▶) — константы в `subscriptions.py`.

- `[audit].*` — секция раздела «Аудит»: `button` (reply-кнопка), `title` (заголовок ленты), `empty` (пустой журнал), шаблоны строк (`line_message` с `{icon}`/`{when}`/`{event}`/`{client}`/`{text}`, `line_action` без текста/статуса), `client_suffix` (`{child}`), иконки статуса доставки (`status_sent` ✅, `status_failed` ⚠️) и действия (`action_icon` 📌), подписи пагинации (`btn_prev` «◀ позже», `btn_next` «раньше ▶»), подсекция `[audit.events]` — подпись на каждый slug `AuditEvent`.

- `[templates].*` — секция раздела «Тексты для клиента»: UI-тексты редактора (`btn_open`, `title`, `btn_edit`, `btn_reset`, `required_mark`, `no_placeholders`, `edit_prompt` с `{label}`/`{current}`/`{placeholders}`, `saved`, `reset_done`, `reset_noop`, ошибки `err_empty`/`err_malformed`/`err_disallowed`/`err_missing`), подсекция `[templates.labels]` (подпись на каждый `template_key`) и `[templates.defaults]` (дефолт `payment_reminder` — у него ещё нет своей секции-владельца). Дефолты остальных клиентских шаблонов живут в своих секциях (`reminder.client_text`, `schedule.notify_*`, `clients.invite_forward`/`linked`); привязку ключ → секция держит `src/bot/client_templates.py`.

Клиентские сообщения отправляются через `resolve_template(repo, specialist_id, key, default)` (override специалиста ?? дефолт), а не читают `messages.toml` напрямую: напоминание о записи (`scheduler`), уведомления о создании/переносе/отмене разовых и регулярных записей (`schedule`), приглашение и подтверждение привязки (`clients`/`start`).

Тексты редактируются вручную и не требуют деплоя кода; перечитываются при перезапуске процесса.

## Логи

- `specialist.invite_created` — успешный `create_invite`.
- `specialist.welcomed` — первый успешный `/start <token>`.
- `specialist.invite_replayed` — повтор `/start` для уже привязанного токена.
- `specialist.invite_unknown` — `/start` с неизвестным токеном.
- `client.created` / `client.field_updated` / `client.archived` / `client.restored` — бизнес-события картотеки (в `extra`: `specialist_id`, `client_id`).
- `client.invite_created` / `client.linked` — выдача приглашения и привязка Telegram клиента (в `extra`: `specialist_id`, `client_id`). `client.link_unknown` — переход по неизвестному `cli_`-токену (в `extra`: `token_prefix`).
- `appointment.created` / `appointment.rescheduled` / `appointment.deleted` — бизнес-события записей (в `extra`: `specialist_id`, `appointment_id`, для создания ещё `client_id`).
- `specialist.setting_updated` — изменение настройки расписания (в `extra`: `specialist_id`, `field`; для тоггла рабочих дней `field="working_days"`, для напоминаний `field="reminder_enabled"`/`"reminder_time"`, для сводки `field="morning_notify_enabled"`/`"morning_notify_time"`, для напоминания об оплате `field="payment_reminder_enabled"`/`"payment_reminder_time"`, для вариантов абонемента `field="subscription_presets"`).
- `subscription.created` / `subscription.decremented` / `subscription.extended` / `subscription.closed` / `subscription.deduction_cancelled` / `subscription.deduction_commented` — бизнес-события абонементов (в `extra`: `specialist_id`, `subscription_id`/`deduction_id`). Журналятся в сервисе `services/subscriptions.py`.
- `subscription.auto_deducted` — вечерний проход списал встречу с абонемента (в `extra`: `specialist_id`, `subscription_id`, `appointment_id`); DUPLICATE-пропуск — `subscription.auto_deduct_skipped_duplicate` (debug). Журналятся в сервисе `services/consumption.py`. Отчёт специалисту собирает `bot/scheduler.py::run_consumption_pass` (кнопки на абонементы + кнопки ❗-встреч: EXHAUSTED → `subs:card:<id>`, NO_SUBSCRIPTION → `clients:card:<id>`), доставка не журналируется в `audit_log` (это сообщение специалисту, не клиенту).
- `subscription.payment_reminder_alerted` — фоновый проход показал специалисту алерт об оплате (в `extra`: `specialist_id`, `client_id`, `subscription_id`, `linked`). Журналится в сервисе `services/payment_reminder.py`. `subscription.payment_reminder_sent` / `subscription.payment_reminder_send_failed` — отправка фиксированного текста клиенту по тапу прошла / упала на `TelegramForbiddenError`/`TelegramBadRequest` (в `extra`: `specialist_id`, `client_id`). Журналятся в `bot/handlers/payment.py`.
- `appointment.reminder_sent` / `appointment.reminder_failed` — отправка напоминания клиенту прошла / упала на `TelegramForbiddenError`/`TelegramBadRequest` (в `extra`: `specialist_id`, `client_id`). Журналятся в `bot/scheduler.py`.
- `appointment.reminder_confirmed` / `appointment.reminder_declined` — ответ клиента на напоминание (в `extra`: `specialist_id`, `client_id`). Журналятся в сервисе `apply_reminder_response`.
- `specialist.digest_sent` / `specialist.digest_skipped_empty` — утренняя сводка специалисту отправлена / пропущена из-за пустого дня (в `extra`: `specialist_id`). Журналятся в сервисе `services/digest.py`. `specialist.digest_failed` — сбой доставки сводки (`TelegramForbiddenError`/`TelegramBadRequest`); журналятся в `bot/scheduler.py` (фоновый проход) и в `bot/handlers/settings.py` (кнопка «Отправить сейчас»).
- `appointment.notified` / `appointment.notify_failed` — немедленное уведомление клиента о записи («Сейчас») отправлено / упало на `TelegramForbiddenError`/`TelegramBadRequest` (в `extra`: `specialist_id`, `client_id`). Журналятся в `_send_notify` (`bot/handlers/schedule.py`) и `bot/scheduler.py` (отложенная доставка).
- `appointment.notify_deferred` / `appointment.notify_deferred_sent` / `appointment.notify_cancelled_deferred` — уведомление поставлено в очередь (в `extra` ещё `superseded`) / доставлено фоновым проходом / отменено с карточки (в `extra`: `specialist_id`, `client_id`/`message_id`). Журналятся в `services/scheduled_messages.py` и `bot/scheduler.py`.
- `audit.recorded` — записана строка журнала событий (в `extra`: `specialist_id`, `client_id`, `kind`, `event`, для `message` ещё `status`). Журналятся в сервисе `services/audit.py`. Это Python-лог самого факта записи в БД-журнал `audit_log` — отдельный, видимый специалисту слой (см. `docs/database.md`).

**Журналирование исходящих клиенту (`message`-строки).** Любая отправка клиенту проходит через единую воронку `src/bot/client_audit.py::record_client_message`, которая пишет `message`-строку в `audit_log` с фактическим статусом (`sent`/`failed`+причина).

Точки отправки:

- `schedule.py::_send_notify` — немедленные уведомления о записи (разовой и регулярной: создание/перенос/отмена; перенос регулярной = событие `notify_rescheduled`);
- `scheduler.py::_deliver_outbox` — отложенная доставка уведомлений (событие из снимка `scheduled_client_messages.event`);
- `scheduler.py::_deliver` — авто-напоминания (событие `reminder`);
- `start.py::_link_client_and_reply` — подтверждение привязки (событие `welcome`).

**Правило для новых фич:** каждая новая точка отправки клиенту обязана журналировать обе ветки через эту воронку — закреплено в `.claude/rules/bot.md`. Сообщения специалисту (сводка, уведомление об отказе) сюда не входят.

В `extra` всегда передаются `specialist_id` (если известен) и `token_prefix` — первые 6 символов токена, чтобы не светить полный токен в журнале.
