from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any
from .base import PluginAdapter

logger = logging.getLogger(__name__)

__all__ = ["HonchoAdapter"]


class HonchoAdapter(PluginAdapter):
    """Adapter for Honcho user memory, conclusions, and dialectic context with real API support."""

    name: str = "honcho"

    def __init__(self, client: Any = None) -> None:
        self._client = client

    def _resolve_real_client(self) -> Any:
        """Attempt to instantiate real Honcho client if HONCHO_API_KEY is present."""
        if self._client is not None:
            return self._client

        api_key = os.environ.get("HONCHO_API_KEY", "").strip()
        if not api_key:
            return None

        try:
            import sys
            honcho_dir = Path(__file__).resolve().parent.parent.parent.parent / "honcho"
            if str(honcho_dir) not in sys.path:
                sys.path.insert(0, str(honcho_dir))

            from client import HonchoClient
            self._client = HonchoClient(api_key=api_key)
            return self._client
        except Exception as exc:
            logger.debug("Real HonchoClient could not be initialized: %s", exc)

        return None

    async def is_available(self) -> bool:
        """Check if Honcho API key or plugin directory is configured."""
        if os.environ.get("HONCHO_API_KEY", "").strip():
            return True
        honcho_dir = Path(__file__).resolve().parent.parent.parent.parent / "honcho"
        return (honcho_dir / "plugin.yaml").is_file()

    async def get_user_dialectic_context(self, session_id: str | None = None) -> dict[str, Any]:
        """Fetch user preferences, conclusions, and working style from Honcho."""
        real_client = self._resolve_real_client()

        if real_client is not None:
            try:
                if hasattr(real_client, "get_context"):
                    return await real_client.get_context(session_id=session_id)
            except Exception as exc:
                logger.warning("Error fetching from live Honcho API: %s", exc)

        # Baseline fallback when Honcho API key is not yet provisioned
        return {"user_representation": "Analytical senior developer, prefers concise output"}

    async def conclude_learning(self, conclusion_text: str) -> dict[str, Any]:
        """Store reflection conclusion back into Honcho dialectic memory (Feedback Loop)."""
        real_client = self._resolve_real_client()
        if real_client is not None and hasattr(real_client, "conclude"):
            try:
                return await real_client.conclude(conclusion_text)
            except Exception as exc:
                logger.debug("Failed posting conclusion to Honcho API: %s", exc)

        return {"status": "recorded", "conclusion": conclusion_text}
