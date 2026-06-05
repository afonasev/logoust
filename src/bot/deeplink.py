from src.config import settings

# Client deep-links carry a "cli_"-prefixed payload so onboarding can tell a
# client token from a specialist token without probing both tables.
CLIENT_TOKEN_PREFIX = "cli_"  # noqa: S105 — routing prefix, not a credential


def build_client_start_link(token: str) -> str:
    """Deep-link a client follows to bind their Telegram to a card (one tap)."""
    return (
        f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}"
        f"?start={CLIENT_TOKEN_PREFIX}{token}"
    )
