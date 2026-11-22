"""Loads configuration (bot token, signup deadline) from the .env file."""
import os
from datetime import datetime, timezone

from dotenv import load_dotenv


def load_token() -> str:
    load_dotenv()
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "DISCORD_BOT_TOKEN is not set. Copy .env.example to .env and fill it in."
        )
    return token


def load_signup_deadline() -> datetime:
    load_dotenv()
    raw = os.getenv("SIGNUP_DEADLINE")
    if not raw:
        raise RuntimeError(
            "SIGNUP_DEADLINE is not set. Copy .env.example to .env and fill it in "
            "(ISO format, e.g. 2026-12-05T18:00:00+00:00)."
        )
    deadline = datetime.fromisoformat(raw)
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return deadline


def load_dev_guild_id() -> int | None:
    """Optional: a server ID to sync slash commands to instantly during
    testing. Without this, new/changed commands only sync globally, which
    Discord can take up to an hour to propagate."""
    load_dotenv()
    raw = os.getenv("DEV_GUILD_ID")
    return int(raw) if raw else None
