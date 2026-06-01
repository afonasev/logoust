#!/usr/bin/env python3
"""UserPromptSubmit-советчик: подсказывает уровень /effort по фазе spec-driven работы.

Claude Code НЕ даёт хукам менять effort программно — это read-only-контекст для хуков
(проверено по докам hooks.md). Поэтому хук лишь распознаёт фазу по тексту промпта и
печатает рекомендацию: systemMessage — человеку, additionalContext — модели.
Сам /effort переключает человек. Это ближайший к «авто» доступный механизм.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile

# Триггеры фаз (англ. + рус.), сравниваются в lower-case.
# Порядок проверки в recommend() задаёт приоритет.
#
# SPEC_WRITE — ТОЛЬКО авторские сигналы фазы написания спеки. Намеренно НЕ содержит
# общих слов "спек"/"openspec"/"opsx": они встречаются и в фазе реализации
# ("реализуй по спеке", "opsx:apply"), иначе реализация ложно опознаётся как спека.
SPEC_WRITE = (
    "opsx:new", "opsx:continue", "opsx:ff", "opsx:explore", "openspec new",
    "напиши спек", "написать спек", "создай спек", "создать спек",
    "новая спек", "новую спек", "напиши спецификаци", "draft spec", "change proposal",
)
CRITICAL = (
    "security", "безопасн", "critical", "критич",
    "cross-service", "кросс-сервис", "межсервис", "несколько сервис",
    "авторизац", "payment", "платеж", "персональн", "медицинск",
)
REVIEW = (
    "code-review", "security-review", "ревью", "review diff", "review the diff",
    "проверь дифф", "посмотри дифф", "просмотри изменени", "проверь изменени",
)
IMPL_HIGH = (
    "concurrency", "конкурент", "гонк", "race condition", "миграц", "migration",
    "внешн интеграц", "интеграц с", "необратим", "irreversible",
    "удаление данн", "delete data", "drop table", "алгоритм", "algorithm",
    "транзакц", "transaction", "идемпотент",
)
IMPL_GENERAL = (
    "opsx:apply", "примени спек", "примени измен", "реализуй задач",
    "реализуй по спек", "реализуй спек", "implement the change", "implement task",
    "implement the spec", "продолжи реализац", "continue implement",
)


def _any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(k in text for k in keywords)


def recommend(text: str) -> tuple[str, str] | None:
    """Вернуть (level, reason) или None, если фаза не распознана."""
    if _any(text, SPEC_WRITE) and _any(text, CRITICAL):
        return "max", "спека critical / security / кросс-сервисной фичи"
    if _any(text, SPEC_WRITE):
        return "high", "фаза написания спеки"
    if _any(text, REVIEW):
        return "high", "ревью диффа"
    if _any(text, IMPL_HIGH):
        return "high", "рискованная реализация (гонки/миграции/интеграции/необратимое/алгоритмы)"
    if _any(text, IMPL_GENERAL):
        return "medium", "рутинная реализация по готовой спеке"
    return None


def already_advised(session_id: str, level: str) -> bool:
    """True, если последняя рекомендация в этой сессии уже была такой же.

    Без дедупликации хук повторял бы один и тот же совет на каждом промпте внутри
    длинной фазы. Состояние — один файл на сессию во временной папке ОС.
    """
    if not session_id:
        return False
    key = hashlib.sha256(session_id.encode()).hexdigest()[:16]
    marker = os.path.join(tempfile.gettempdir(), f"cc_effort_{key}")
    try:
        with open(marker, encoding="utf-8") as fh:
            last = fh.read().strip()
    except OSError:
        last = ""
    if last == level:
        return True
    try:
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write(level)
    except OSError:
        pass
    return False


def main() -> int:
    raw = sys.stdin.read()
    if not raw.strip():
        return 0
    data = json.loads(raw)
    rec = recommend(str(data.get("prompt", "")).lower())
    if rec is None:
        return 0
    level, reason = rec
    if already_advised(str(data.get("session_id", "")), level):
        return 0
    out = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": (
                f"[effort-advisor] Похоже на фазу: {reason}. "
                f"Рекомендуемый reasoning effort: {level}. "
                f"Хук не может переключить effort сам — напомни пользователю "
                f"выполнить /effort {level}, если уровень ещё не выставлен."
            ),
        },
        "systemMessage": f"⚙️ effort: рекомендую /effort {level} — {reason}.",
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # Хук НИКОГДА не должен ломать отправку промпта — глушим любую ошибку.
        sys.exit(0)
