from dataclasses import replace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.client import (
    Client,
    ClientStatus,
    ClientValidationError,
    ValidationReason,
)
from src.infrastructure.clients_repo import SqlAlchemyClientsRepo
from src.services.clients import (
    EditResult,
    NewClient,
    add_client,
    archive_client,
    create_client_invite,
    edit_client_field,
    link_client_by_token,
    list_active_page,
    list_archived_page,
    list_clients,
    restore_client,
)

_DEFAULT_NEW = NewClient(
    specialist_id=1,
    child_name="Петя",
    contact_name="Мама",
    contact_phone="89161234567",
)


async def _add(
    session: AsyncSession, **kw: Any
) -> tuple[SqlAlchemyClientsRepo, Client]:
    repo = SqlAlchemyClientsRepo(session)
    return repo, await add_client(repo, replace(_DEFAULT_NEW, **kw))


async def _add_expecting_error(
    session: AsyncSession, data: NewClient
) -> ValidationReason:
    repo = SqlAlchemyClientsRepo(session)
    try:
        await add_client(repo, data)
    except ClientValidationError as err:
        return err.reason
    pytest.fail("expected ClientValidationError")  # pragma: no cover


async def test_add_client_creates_active_with_normalized_phone(session: AsyncSession):
    _, client = await _add(session)
    assert client.id is not None
    assert client.status is ClientStatus.ACTIVE
    assert client.contact_phone == "+79161234567"
    assert client.archived_at is None


async def test_add_client_accepts_telegram_only_contact(session: AsyncSession):
    _, client = await _add(session, contact_phone=None, contact_telegram="@masha")
    assert client.contact_phone is None
    assert client.contact_telegram == "masha"


async def test_add_client_trims_optional_fields(session: AsyncSession):
    _, client = await _add(session, extra_contacts="  бабушка  ", note="   ")
    assert client.extra_contacts == "бабушка"
    assert client.note is None


async def test_add_client_requires_child_name(session: AsyncSession):
    reason = await _add_expecting_error(
        session,
        NewClient(
            specialist_id=1,
            child_name="  ",
            contact_name="Мама",
            contact_phone="89161234567",
        ),
    )
    assert reason is ValidationReason.EMPTY_CHILD_NAME


async def test_add_client_requires_contact_name(session: AsyncSession):
    reason = await _add_expecting_error(
        session,
        NewClient(
            specialist_id=1,
            child_name="Петя",
            contact_name="",
            contact_phone="89161234567",
        ),
    )
    assert reason is ValidationReason.EMPTY_CONTACT_NAME


async def test_add_client_requires_a_contact_channel(session: AsyncSession):
    reason = await _add_expecting_error(
        session,
        NewClient(specialist_id=1, child_name="Петя", contact_name="Мама"),
    )
    assert reason is ValidationReason.NO_CONTACT_CHANNEL


async def test_edit_field_updates_note(session: AsyncSession):
    repo, client = await _add(session)
    assert client.id is not None
    result = await edit_client_field(
        repo, client_id=client.id, specialist_id=1, field="note", value="важно"
    )
    assert result is EditResult.UPDATED
    found = await repo.get_for_specialist(client.id, 1)
    assert found is not None
    assert found.note == "важно"
    assert found.updated_at is not None


async def test_edit_field_normalizes_phone(session: AsyncSession):
    repo, client = await _add(session)
    assert client.id is not None
    await edit_client_field(
        repo,
        client_id=client.id,
        specialist_id=1,
        field="contact_phone",
        value="8-916-123-45-67",
    )
    found = await repo.get_for_specialist(client.id, 1)
    assert found is not None
    assert found.contact_phone == "+79161234567"


async def test_edit_field_normalizes_telegram(session: AsyncSession):
    repo, client = await _add(session)
    assert client.id is not None
    await edit_client_field(
        repo,
        client_id=client.id,
        specialist_id=1,
        field="contact_telegram",
        value="@papa",
    )
    found = await repo.get_for_specialist(client.id, 1)
    assert found is not None
    assert found.contact_telegram == "papa"


async def test_edit_required_field_updates(session: AsyncSession):
    repo, client = await _add(session)
    assert client.id is not None
    result = await edit_client_field(
        repo,
        client_id=client.id,
        specialist_id=1,
        field="contact_name",
        value="Папа",
    )
    assert result is EditResult.UPDATED
    found = await repo.get_for_specialist(client.id, 1)
    assert found is not None
    assert found.contact_name == "Папа"


async def test_edit_required_field_to_empty_rejected(session: AsyncSession):
    repo, client = await _add(session)
    assert client.id is not None
    result = await edit_client_field(
        repo, client_id=client.id, specialist_id=1, field="child_name", value="   "
    )
    assert result is EditResult.EMPTY_REQUIRED
    found = await repo.get_for_specialist(client.id, 1)
    assert found is not None
    assert found.child_name == "Петя"


async def test_edit_field_clears_optional_when_blank(session: AsyncSession):
    repo, client = await _add(session, extra_contacts="бабушка")
    assert client.id is not None
    await edit_client_field(
        repo,
        client_id=client.id,
        specialist_id=1,
        field="extra_contacts",
        value="  ",
    )
    found = await repo.get_for_specialist(client.id, 1)
    assert found is not None
    assert found.extra_contacts is None


async def test_edit_field_rejects_unknown_field(session: AsyncSession):
    repo, client = await _add(session)
    assert client.id is not None
    with pytest.raises(ValueError, match="not editable"):
        await edit_client_field(
            repo,
            client_id=client.id,
            specialist_id=1,
            field="status",
            value="archived",
        )


async def test_edit_field_not_found_for_other_owner(session: AsyncSession):
    repo, client = await _add(session, specialist_id=1)
    assert client.id is not None
    result = await edit_client_field(
        repo, client_id=client.id, specialist_id=2, field="note", value="x"
    )
    assert result is EditResult.NOT_FOUND


async def test_archive_and_restore(session: AsyncSession):
    repo, client = await _add(session)
    assert client.id is not None
    assert await archive_client(repo, client_id=client.id, specialist_id=1) is True
    found = await repo.get_for_specialist(client.id, 1)
    assert found is not None
    assert found.status is ClientStatus.ARCHIVED
    assert found.archived_at is not None

    assert await restore_client(repo, client_id=client.id, specialist_id=1) is True
    found = await repo.get_for_specialist(client.id, 1)
    assert found is not None
    assert found.status is ClientStatus.ACTIVE
    assert found.archived_at is None


async def test_archive_other_owner_returns_false(session: AsyncSession):
    repo, client = await _add(session, specialist_id=1)
    assert client.id is not None
    assert await archive_client(repo, client_id=client.id, specialist_id=2) is False


async def test_restore_other_owner_returns_false(session: AsyncSession):
    repo, client = await _add(session, specialist_id=1)
    assert client.id is not None
    assert await restore_client(repo, client_id=client.id, specialist_id=2) is False


async def test_list_archived_page_flags_and_trimming(session: AsyncSession):
    repo, _ = await _add(session, child_name="Активный")  # активный — не в архиве
    for name in ["A", "B", "C"]:
        _, c = await _add(session, child_name=name)
        assert c.id is not None
        await archive_client(repo, client_id=c.id, specialist_id=1)

    p0 = await list_archived_page(repo, specialist_id=1, page=0, page_size=2)
    assert len(p0.clients) == 2
    assert p0.page == 0
    assert p0.has_prev is False
    assert p0.has_next is True

    p1 = await list_archived_page(repo, specialist_id=1, page=1, page_size=2)
    assert len(p1.clients) == 1
    assert p1.has_prev is True
    assert p1.has_next is False


async def test_list_active_page_sorted_and_paginated(session: AsyncSession):
    repo, _ = await _add(session, child_name="Архивный")
    archived = await repo.list_by_status(1, ClientStatus.ACTIVE)
    assert archived[0].id is not None
    await archive_client(repo, client_id=archived[0].id, specialist_id=1)
    for name in ["Яков", "Аня", "Боря"]:
        await _add(session, child_name=name)

    p0 = await list_active_page(repo, specialist_id=1, page=0, page_size=2)
    assert [c.child_name for c in p0.clients] == [
        "Аня",
        "Боря",
    ]  # by name, archived out
    assert p0.has_prev is False
    assert p0.has_next is True

    p1 = await list_active_page(repo, specialist_id=1, page=1, page_size=2)
    assert [c.child_name for c in p1.clients] == ["Яков"]
    assert p1.has_prev is True
    assert p1.has_next is False


async def test_create_client_invite_generates_token(session: AsyncSession):
    repo, client = await _add(session)
    assert client.id is not None
    updated = await create_client_invite(repo, client_id=client.id, specialist_id=1)
    assert updated is not None
    assert updated.invite_token is not None
    assert updated.telegram_chat_id is None


async def test_create_client_invite_reuses_existing_token(session: AsyncSession):
    repo, client = await _add(session)
    assert client.id is not None
    first = await create_client_invite(repo, client_id=client.id, specialist_id=1)
    second = await create_client_invite(repo, client_id=client.id, specialist_id=1)
    assert first is not None
    assert second is not None
    assert first.invite_token == second.invite_token


async def test_create_client_invite_other_owner_returns_none(session: AsyncSession):
    repo, client = await _add(session, specialist_id=1)
    assert client.id is not None
    result = await create_client_invite(repo, client_id=client.id, specialist_id=2)
    assert result is None


async def test_link_client_by_token_binds_chat_id(session: AsyncSession):
    repo, client = await _add(session)
    assert client.id is not None
    invited = await create_client_invite(repo, client_id=client.id, specialist_id=1)
    assert invited is not None
    assert invited.invite_token is not None

    linked = await link_client_by_token(repo, invited.invite_token, chat_id=555)
    assert linked is not None
    assert linked.telegram_chat_id == 555
    assert linked.linked_at is not None


async def test_link_client_by_token_rebind_overwrites(session: AsyncSession):
    repo, client = await _add(session)
    assert client.id is not None
    invited = await create_client_invite(repo, client_id=client.id, specialist_id=1)
    assert invited is not None
    assert invited.invite_token is not None

    await link_client_by_token(repo, invited.invite_token, chat_id=111)
    rebound = await link_client_by_token(repo, invited.invite_token, chat_id=222)
    assert rebound is not None
    assert rebound.telegram_chat_id == 222


async def test_link_client_by_token_unknown_returns_none(session: AsyncSession):
    repo, _ = await _add(session)
    result = await link_client_by_token(repo, "no-such-token", chat_id=1)
    assert result is None


async def test_link_one_account_to_two_cards(session: AsyncSession):
    # One Telegram account may bind to several client cards (testing scenario).
    repo, masha = await _add(session, child_name="Маша")
    _, petya = await _add(session, child_name="Петя")
    assert masha.id is not None
    assert petya.id is not None
    t1 = await create_client_invite(repo, client_id=masha.id, specialist_id=1)
    t2 = await create_client_invite(repo, client_id=petya.id, specialist_id=1)
    assert t1 is not None
    assert t1.invite_token is not None
    assert t2 is not None
    assert t2.invite_token is not None

    a = await link_client_by_token(repo, t1.invite_token, chat_id=777)
    b = await link_client_by_token(repo, t2.invite_token, chat_id=777)
    assert a is not None
    assert a.telegram_chat_id == 777
    assert b is not None
    assert b.telegram_chat_id == 777


async def test_list_clients_filters_by_status(session: AsyncSession):
    repo, _ = await _add(session, child_name="Аня")
    _, boris = await _add(session, child_name="Боря")
    assert boris.id is not None
    await archive_client(repo, client_id=boris.id, specialist_id=1)

    active = await list_clients(repo, specialist_id=1, status=ClientStatus.ACTIVE)
    assert [c.child_name for c in active] == ["Аня"]

    archived = await list_clients(repo, specialist_id=1, status=ClientStatus.ARCHIVED)
    assert [c.child_name for c in archived] == ["Боря"]
