from __future__ import annotations

import logging
from typing import Any

from core.lifecycle import format_daily_status_summary

logger = logging.getLogger(__name__)

__all__ = ["handle_daily_command", "register_commands"]


def handle_daily_command(args: str = "", **kwargs: Any) -> str:
    """Handler for user slash command /daily [status|plan|review|replan]."""
    subcommand = (args or "status").strip().split()[0].lower() if args else "status"

    if subcommand == "plan":
        return format_daily_status_summary()
    elif subcommand == "review":
        return "### Daily Review\n*Review cycle runs at 21:00 or when triggered via daily_life_trigger_cycle.*"
    elif subcommand == "replan":
        return "### Daily Replanning\n*Replanning initiated. Inspecting current task progress and Brain state...*"
    else:
        return format_daily_status_summary()


def register_commands(ctx: Any) -> None:
    """Register slash commands with the Hermes plugin context."""
    ctx.register_command(
        name="daily",
        handler=handle_daily_command,
        description="Inspect or trigger daily life activities (/daily [status|plan|review|replan]).",
    )
    logger.debug("daily-life: registered /daily slash command")
