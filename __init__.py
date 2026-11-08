"""Daily Life plugin entry point for Hermes Agent."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["register"]


def register(ctx: Any) -> None:
    """Hermes plugin entrypoint — registers daily life tools, commands, hooks, and prompt sections."""
    from .tools import register_tools
    from .commands import register_commands
    from .hooks import register_hooks
    from .core.prompt import build_today_focus_prompt

    # 1. Register tools model-callable
    register_tools(ctx)

    # 2. Register slash command (/daily)
    register_commands(ctx)

    # 3. Register lifecycle & telemetry hooks
    register_hooks(ctx)

    # 4. Register cache-safe system prompt section
    try:
        ctx.register_system_prompt_section(
            "daily-life.today-focus",
            build_today_focus_prompt,
            position="after_memory",
            max_chars=3000,
        )
    except Exception as exc:
        logger.debug("daily-life: could not register system prompt section: %s", exc)

    logger.debug("daily-life: plugin registered successfully")
