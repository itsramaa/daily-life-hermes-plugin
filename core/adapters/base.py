from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__ = ["PluginAdapter"]


@runtime_checkable
class PluginAdapter(Protocol):
    """Base protocol for Daily Life external adapters."""

    name: str

    async def is_available(self) -> bool:
        """Check if target plugin/system is accessible and configured."""
        ...
