from dataclasses import dataclass
from datetime import UTC, datetime
import logging

from src.domain.deduction import (
    SubscriptionDeduction,
    SubscriptionDeductionsRepo,
)
from src.domain.subscription import (
    Subscription,
    SubscriptionsRepo,
    SubscriptionStatus,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SubscriptionsPage:
    items: list[Subscription]
    page: int
    has_prev: bool
    has_next: bool


# Верхний порог числа встреч — защита от абсурдного ввода, не жёсткое правило
# (тот же подход, что и _MAX_SLOT_MINUTES в services/specialists.py).
_MAX_MEETINGS = 200
# Сколько вариантов-кнопок допускаем в настройке (умещаются в клавиатуру).
_MAX_PRESETS = 8


def parse_meetings(raw: str) -> int | None:
    """Проверить число встреч: положительное целое в разумных пределах.

    Возвращает число либо None для некорректного ввода (ноль, отрицательное,
    нечисловое, чрезмерно большое).
    """
    value = raw.strip()
    if not value.isdigit():
        return None
    meetings = int(value)
    if meetings <= 0 or meetings > _MAX_MEETINGS:
        return None
    return meetings


def parse_presets(raw: str) -> str | None:
    """Проверить и канонизировать список вариантов, заданный через запятую.

    Каждый элемент — корректное число встреч (см. parse_meetings). Возвращает
    канонический вид (по возрастанию, без повторов, например 4,8,12) либо None
    для некорректного ввода (пустой список, мусор, слишком много вариантов).
    """
    values: set[int] = set()
    for part in raw.split(","):
        meetings = parse_meetings(part)
        if meetings is None:
            return None
        values.add(meetings)
    if not values or len(values) > _MAX_PRESETS:
        return None
    return ",".join(str(n) for n in sorted(values))


def presets_list(stored: str) -> list[int]:
    """Разобрать канонический список вариантов в числа по возрастанию."""
    return [int(part) for part in stored.split(",")]


def _log(event: str, subscription: Subscription) -> None:
    logger.info(
        event,
        extra={
            "specialist_id": subscription.specialist_id,
            "client_id": subscription.client_id,
            "subscription_id": subscription.id,
        },
    )


async def get_active(
    repo: SubscriptionsRepo, *, client_id: int, specialist_id: int
) -> Subscription | None:
    return await repo.get_active(client_id, specialist_id)


async def get_card(
    repo: SubscriptionsRepo, *, subscription_id: int, specialist_id: int
) -> Subscription | None:
    return await repo.get_for_specialist(subscription_id, specialist_id)


def _to_page(
    rows: list[Subscription], *, page: int, page_size: int
) -> SubscriptionsPage:
    # Fetch one extra row to detect a next page without a separate COUNT query.
    return SubscriptionsPage(
        items=rows[:page_size],
        page=page,
        has_prev=page > 0,
        has_next=len(rows) > page_size,
    )


async def list_active_page(
    repo: SubscriptionsRepo, *, specialist_id: int, page: int, page_size: int
) -> SubscriptionsPage:
    rows = await repo.list_active_for_specialist(
        specialist_id, limit=page_size + 1, offset=page * page_size
    )
    return _to_page(rows, page=page, page_size=page_size)


async def list_closed_page(
    repo: SubscriptionsRepo, *, specialist_id: int, page: int, page_size: int
) -> SubscriptionsPage:
    rows = await repo.list_closed_for_specialist(
        specialist_id, limit=page_size + 1, offset=page * page_size
    )
    return _to_page(rows, page=page, page_size=page_size)


async def create_subscription(
    repo: SubscriptionsRepo,
    *,
    client_id: int,
    specialist_id: int,
    meetings: int,
) -> Subscription | None:
    """Создать активный абонемент. None — когда активный абонемент уже есть.

    Инвариант «один активный на клиента» держим здесь: создание идёт строго из
    одной точки UI (см. design.md, решение 3).
    """
    existing = await repo.get_active(client_id, specialist_id)
    if existing is not None:
        return None
    subscription = Subscription(
        id=None,
        client_id=client_id,
        specialist_id=specialist_id,
        purchased=meetings,
        remaining=meetings,
        status=SubscriptionStatus.ACTIVE,
        created_at=datetime.now(UTC),
    )
    saved = await repo.add(subscription)
    _log("subscription.created", saved)
    return saved


async def decrement_meeting(
    deductions_repo: SubscriptionDeductionsRepo,
    *,
    subscription_id: int,
    specialist_id: int,
    now: datetime,
) -> SubscriptionDeduction | None:
    """Ручное «вычесть встречу»: атомарный декремент + строка журнала (реш. 7).

    None — когда списывать нечего: остаток уже 0 (строка не создаётся, остаток в
    минус не уходит) либо абонемент не активный / не найден. Строка журнала идёт
    без привязки к встрече (`appointment_id IS NULL`); комментарий «после встречи»
    добавляется позже на экране списания.
    """
    deduction = await deductions_repo.add_manual(
        subscription_id=subscription_id,
        specialist_id=specialist_id,
        created_at=now,
    )
    if deduction is None:
        return None
    logger.info(
        "subscription.decremented",
        extra={"specialist_id": specialist_id, "subscription_id": subscription_id},
    )
    return deduction


async def list_deductions(
    deductions_repo: SubscriptionDeductionsRepo, *, subscription_id: int
) -> list[SubscriptionDeduction]:
    """Неотменённые строки журнала списаний абонемента (свежие сверху)."""
    return await deductions_repo.list_active_for_subscription(subscription_id)


async def get_deduction(
    deductions_repo: SubscriptionDeductionsRepo,
    *,
    deduction_id: int,
    specialist_id: int,
) -> SubscriptionDeduction | None:
    return await deductions_repo.get_for_specialist(deduction_id, specialist_id)


async def cancel_deduction(
    deductions_repo: SubscriptionDeductionsRepo,
    *,
    deduction_id: int,
    specialist_id: int,
    now: datetime,
) -> Subscription | None:
    """Мягко отменить списание: остаток +1, строка помечается отменённой (реш. 4).

    Идемпотентна (повторная отмена ничего не меняет) и доступна только на активном
    абонементе. Строка остаётся в БД и держит замок — отменённая встреча повторно
    не спишется. None — если списание не найдено / уже отменено / абонемент закрыт.
    """
    subscription = await deductions_repo.cancel(
        deduction_id, specialist_id, cancelled_at=now
    )
    if subscription is None:
        return None
    logger.info(
        "subscription.deduction_cancelled",
        extra={"specialist_id": specialist_id, "deduction_id": deduction_id},
    )
    return subscription


async def set_deduction_comment(
    deductions_repo: SubscriptionDeductionsRepo,
    *,
    deduction_id: int,
    specialist_id: int,
    comment: str | None,
) -> SubscriptionDeduction | None:
    """Задать/изменить комментарий «после встречи». None — если редактировать нельзя.

    Доступно только на активном абонементе; факты строки (привязка, дата, снимок
    комментария записи) не меняются.
    """
    updated = await deductions_repo.set_closing_comment(
        deduction_id, specialist_id, comment=comment
    )
    if updated is None:
        return None
    logger.info(
        "subscription.deduction_commented",
        extra={"specialist_id": specialist_id, "deduction_id": deduction_id},
    )
    return updated


async def extend_subscription(
    repo: SubscriptionsRepo,
    *,
    subscription_id: int,
    specialist_id: int,
    meetings: int,
) -> Subscription | None:
    """Продлить абонемент: purchased и remaining += meetings (кумулятивно)."""
    subscription = await repo.get_for_specialist(subscription_id, specialist_id)
    if subscription is None:
        return None
    updated = await repo.update_counters(
        subscription_id,
        specialist_id,
        purchased=subscription.purchased + meetings,
        remaining=subscription.remaining + meetings,
    )
    assert updated is not None  # noqa: S101 — just fetched it under the same owner
    # Продление — единственный путь, переводящий remaining 0 → >0, поэтому
    # сбрасываем payment_reminded_at именно здесь: при следующем обнулении остатка
    # напоминание сработает заново (см. design.md, решение 4). «Вычесть» (decrement)
    # этот признак не меняет.
    await repo.mark_payment_reminded(subscription_id, None)
    updated.payment_reminded_at = None
    _log("subscription.extended", updated)
    return updated


async def close_subscription(
    repo: SubscriptionsRepo, *, subscription_id: int, specialist_id: int
) -> Subscription | None:
    """Закрыть абонемент (status=closed, closed_at). None — если не найден."""
    updated = await repo.close(
        subscription_id, specialist_id, closed_at=datetime.now(UTC)
    )
    if updated is None:
        return None
    _log("subscription.closed", updated)
    return updated
