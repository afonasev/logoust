import pytest

from src.bot.client_templates import template_default
from src.bot.messages import DEFAULT_MESSAGES_PATH, load_messages
from src.domain.message_template import (
    CLIENT_TEMPLATES,
    TemplateViolation,
    parse_placeholders,
    validate_template,
)

# Placeholders that name a person — these MUST NOT be customizable (PDn minimization).
_NAME_PLACEHOLDERS = {"child", "contact", "name", "phone", "telegram"}


def test_parse_placeholders_finds_names():
    assert parse_placeholders("Привет {date} в {time}") == {"date", "time"}


def test_parse_placeholders_ignores_escaped_braces():
    # {{ and }} are literal braces, not placeholders.
    assert parse_placeholders("Скидка 50% {{не плейсхолдер}} на {link}") == {"link"}


def test_parse_placeholders_reduces_dotted_and_indexed_names():
    assert parse_placeholders("{date.year} {items[0]}") == {"date", "items"}


def test_parse_placeholders_raises_on_unbalanced_braces():
    with pytest.raises(ValueError):  # noqa: PT011 — any ValueError is the contract
        parse_placeholders("сломанная скобка {date")


def test_validate_rejects_empty():
    violations = validate_template("appt_reminder", "   ")
    assert [v.kind for v in violations] == [TemplateViolation.EMPTY]


def test_validate_rejects_malformed_braces():
    violations = validate_template("appt_reminder", "{date} в {time")
    assert [v.kind for v in violations] == [TemplateViolation.MALFORMED]


def test_validate_rejects_disallowed_placeholder():
    violations = validate_template("appt_reminder", "{date} {time} {child}")
    assert len(violations) == 1
    assert violations[0].kind is TemplateViolation.DISALLOWED
    assert violations[0].placeholders == ("child",)


def test_validate_rejects_missing_required_placeholder():
    violations = validate_template("appt_reminder", "Напоминаем о записи {date}")  # noqa: RUF001
    assert len(violations) == 1
    assert violations[0].kind is TemplateViolation.MISSING_REQUIRED
    assert violations[0].placeholders == ("time",)


def test_validate_reports_disallowed_and_missing_together():
    violations = validate_template("appt_reminder", "{foo}")
    kinds = {v.kind for v in violations}
    assert kinds == {TemplateViolation.DISALLOWED, TemplateViolation.MISSING_REQUIRED}


def test_validate_accepts_valid_text():
    assert validate_template("appt_reminder", "Запись {date} в {time}") == []


def test_validate_accepts_template_without_placeholders():
    # `linked` allows no placeholders — plain text is valid.
    assert validate_template("linked", "Вы подключены.") == []


def test_every_catalog_key_has_a_nonempty_default():
    messages = load_messages(DEFAULT_MESSAGES_PATH)
    for key in CLIENT_TEMPLATES:
        assert template_default(messages, key).strip(), key


def test_catalog_defaults_pass_their_own_validation():
    # The out-of-the-box text must itself satisfy the key's whitelist/required rules.
    messages = load_messages(DEFAULT_MESSAGES_PATH)
    for key in CLIENT_TEMPLATES:
        assert validate_template(key, template_default(messages, key)) == [], key


def test_no_name_placeholders_are_allowed():
    for key, spec in CLIENT_TEMPLATES.items():
        assert not (spec.allowed & _NAME_PLACEHOLDERS), key
        # Required must be a subset of allowed.
        assert spec.required <= spec.allowed, key
