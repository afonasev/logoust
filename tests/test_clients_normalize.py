import pytest

from src.domain.client import normalize_phone, normalize_telegram


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("8 (916) 123-45-67", "+79161234567"),
        ("+7 916 123-45-67", "+79161234567"),
        ("79161234567", "+79161234567"),
        ("9161234567", "+79161234567"),
        ("8-916-123-45-67", "+79161234567"),
    ],
)
def test_normalize_phone_canonicalizes(raw: str, expected: str):
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "+1 202 555 0123",  # иностранный: 11 цифр, но ведущая 1
        "12345",  # слишком короткий
        "позвоните мне",  # цифр нет вовсе
        "7916123456789",  # слишком длинный
    ],
)
def test_normalize_phone_falls_back_to_trimmed_input(raw: str):
    assert normalize_phone(f"  {raw}  ") == raw


def test_normalize_phone_empty_stays_empty():
    assert not normalize_phone("")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("@masha", "masha"),
        ("masha", "masha"),
        ("  @masha  ", "masha"),
    ],
)
def test_normalize_telegram_strips_leading_at(raw: str, expected: str):
    assert normalize_telegram(raw) == expected
