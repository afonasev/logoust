"""Catalog of customizable client message templates and pure validation.

A *template* is a named client-facing text (`template_key`) the specialist may
override. The catalog (`CLIENT_TEMPLATES`) is pure data: for each key it declares
which placeholders are allowed (whitelist) and which are required. Only
depersonalized placeholders (date/time/rule/link) are ever allowed — names of a
child or contact MUST NOT appear, to minimize personal data in stored overrides
and the blast radius of a misdelivered message (see design.md, decision 3).

Validation (`validate_template`) is a pure function so it can be tested without a
DB and reused wherever an override is saved. User-facing wording is not built
here — the validator returns structured violations that the bot layer renders.
"""

from dataclasses import dataclass
import enum
from string import Formatter
from typing import Protocol


@dataclass(frozen=True, slots=True)
class TemplateSpec:
    allowed: frozenset[str]
    required: frozenset[str]


# Depersonalized placeholders only — see module docstring and design.md decision 3.
_DATE_TIME = frozenset({"date", "time"})
_RULE = frozenset({"rule"})

# The single source of truth for what is customizable and which placeholders apply.
# `payment_reminder` is registered ahead of its send point: the
# subscription-payment-reminder change wires the actual delivery later.
CLIENT_TEMPLATES: dict[str, TemplateSpec] = {
    "appt_reminder": TemplateSpec(allowed=_DATE_TIME, required=_DATE_TIME),
    "notify_created": TemplateSpec(allowed=_DATE_TIME, required=_DATE_TIME),
    "notify_rescheduled": TemplateSpec(allowed=_DATE_TIME, required=_DATE_TIME),
    "notify_cancelled": TemplateSpec(allowed=_DATE_TIME, required=_DATE_TIME),
    "notify_series_created": TemplateSpec(allowed=_RULE, required=_RULE),
    "notify_series_changed": TemplateSpec(allowed=_RULE, required=_RULE),
    # {time} is optional here: "Ваша регулярная запись отменена" is fine without it.
    "notify_series_cancelled": TemplateSpec(
        allowed=frozenset({"time"}), required=frozenset()
    ),
    "payment_reminder": TemplateSpec(allowed=frozenset(), required=frozenset()),
    "invite_forward": TemplateSpec(
        allowed=frozenset({"link"}), required=frozenset({"link"})
    ),
    "linked": TemplateSpec(allowed=frozenset(), required=frozenset()),
}


@dataclass(slots=True)
class MessageTemplate:
    """A specialist's override of one client template (absence ⇒ use the default)."""

    specialist_id: int
    template_key: str
    body: str


class TemplateViolation(enum.Enum):
    EMPTY = "empty"  # blank text
    MALFORMED = "malformed"  # unbalanced braces — would break .format() at render
    DISALLOWED = "disallowed"  # placeholder outside the key's whitelist
    MISSING_REQUIRED = "missing_required"  # a required placeholder is absent


@dataclass(frozen=True, slots=True)
class Violation:
    kind: TemplateViolation
    # Offending placeholders (disallowed) or absent ones (missing); empty otherwise.
    placeholders: tuple[str, ...]


def parse_placeholders(body: str) -> set[str]:
    """Names of `{...}` placeholders in `body`; `{{`/`}}` are literal braces.

    Raises ValueError on unbalanced braces (a lone `{` or `}`) — the same input
    that would make `str.format` blow up at render time, so the validator can
    reject it before it is ever stored.
    """
    names: set[str] = set()
    for _, field_name, _, _ in Formatter().parse(body):
        if field_name is not None:
            # Reduce "date.attr"/"date[0]" to the base name; we only allow flat keys.
            base = field_name.split(".", 1)[0].split("[", 1)[0]
            names.add(base)
    return names


def validate_template(key: str, body: str) -> list[Violation]:
    """Strictly validate an override `body` for `key`; empty list ⇒ valid.

    Whitelist + required enforcement guarantees a stored override carries exactly
    the expected placeholders, so rendering it never fails on an unknown field.
    """
    spec = CLIENT_TEMPLATES[key]
    if not body.strip():
        return [Violation(TemplateViolation.EMPTY, ())]
    try:
        found = parse_placeholders(body)
    except ValueError:
        return [Violation(TemplateViolation.MALFORMED, ())]
    violations: list[Violation] = []
    disallowed = tuple(sorted(found - spec.allowed))
    if disallowed:
        violations.append(Violation(TemplateViolation.DISALLOWED, disallowed))
    missing = tuple(sorted(spec.required - found))
    if missing:
        violations.append(Violation(TemplateViolation.MISSING_REQUIRED, missing))
    return violations


class MessageTemplatesRepo(Protocol):
    # Protocol method bodies are unimplemented placeholders by design.
    async def get(  # pragma: no cover
        self, specialist_id: int, key: str
    ) -> MessageTemplate | None: ...

    async def upsert(  # pragma: no cover
        self, specialist_id: int, key: str, body: str
    ) -> None: ...

    async def delete(  # pragma: no cover
        self, specialist_id: int, key: str
    ) -> bool: ...
