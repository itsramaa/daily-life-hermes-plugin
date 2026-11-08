from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any
from .base import PluginAdapter

logger = logging.getLogger(__name__)

__all__ = ["GmailAdapter"]


class GmailAdapter(PluginAdapter):
    """Adapter for Gmail inbox, newsletter triage, and draft replies with real cloud credentials."""

    name: str = "gmail"

    def __init__(self, client: Any = None) -> None:
        self._client = client

    def _resolve_real_client(self) -> Any:
        """Attempt to instantiate real GmailClient from data/plugins/gmail if credentials exist."""
        if self._client is not None:
            return self._client

        try:
            hermes_home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
            token_file = hermes_home / "google_workspace_token.json"
            secret_file = hermes_home / "client_secret.json"
            try:
                import sys
                common_dir = Path(__file__).resolve().parent.parent.parent.parent / "common"
                if str(common_dir.parent) not in sys.path:
                    sys.path.insert(0, str(common_dir.parent))
                from common.profile_resolver import resolve_profile_path, get_hermes_home
                home = get_hermes_home()
                token_file = resolve_profile_path("google_workspace_token.json", fallback_root=home)
                if not token_file.is_file():
                    token_file = resolve_profile_path("google_token.json", fallback_root=home)
                secret_file = resolve_profile_path("client_secret.json", fallback_root=home)
                if not secret_file.is_file():
                    secret_file = resolve_profile_path("google_client_secret.json", fallback_root=home)
            except Exception:
                pass

            if token_file.is_file() and secret_file.is_file():
                import sys
                gmail_dir = Path(__file__).resolve().parent.parent.parent.parent / "gmail"
                if str(gmail_dir) not in sys.path:
                    sys.path.insert(0, str(gmail_dir))

                from client import GmailClient
                from config import GmailConfig

                cfg = GmailConfig(token_path=token_file, client_secret_path=secret_file)
                self._client = GmailClient(cfg)
                return self._client
        except Exception as exc:
            logger.debug("Real GmailClient could not be initialized: %s", exc)

        return None

    def check_oauth_token_health(self) -> bool:
        """Subsystem 5, Point 41: Check if OAuth token file exists and is non-empty."""
        hermes_home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
        candidates = [hermes_home / "google_workspace_token.json", hermes_home / "google_token.json"]
        try:
            import sys
            common_dir = Path(__file__).resolve().parent.parent.parent.parent / "common"
            if str(common_dir.parent) not in sys.path:
                sys.path.insert(0, str(common_dir.parent))
            from common.profile_resolver import resolve_profile_path, get_hermes_home
            home = get_hermes_home()
            candidates = [
                resolve_profile_path("google_workspace_token.json", fallback_root=home),
                resolve_profile_path("google_token.json", fallback_root=home),
            ]
        except Exception:
            pass
        return any(p.is_file() and p.stat().st_size > 10 for p in candidates)

    async def is_available(self) -> bool:
        """Check if Gmail tool or OAuth client is configured with real credentials."""
        if self.check_oauth_token_health():
            return True

        gmail_dir = Path(__file__).resolve().parent.parent.parent.parent / "gmail"
        return (gmail_dir / "plugin.yaml").is_file()

    async def fetch_newsletters(
        self,
        sender_domains: list[str] | None = None,
        max_results: int = 5,
    ) -> list[dict[str, Any]]:
        """Fetch unread or recent newsletter digests from the last 24-36h."""
        real_client = self._resolve_real_client()

        if real_client is not None:
            try:
                query = "newer_than:1d"
                if sender_domains:
                    domain_q = " OR ".join(f"from:{d}" for d in sender_domains)
                    query = f"({domain_q}) newer_than:1d"

                messages = await real_client.search(query=query, max_results=max_results)
                results: list[dict[str, Any]] = []
                for msg in messages:
                    results.append({
                        "id": getattr(msg, "id", "msg-1"),
                        "subject": getattr(msg, "subject", "Newsletter"),
                        "sender": getattr(msg, "sender", "Unknown"),
                        "date": getattr(msg, "date", None),
                        "snippet": getattr(msg, "snippet", ""),
                    })
                return results
            except Exception as exc:
                logger.warning("Failed querying live Gmail API: %s. Using fallback.", exc)

        return []

    async def fetch_important_unread_emails(self, max_results: int = 5) -> list[dict[str, Any]]:
        """Subsystem 5, Point 42: Triage priority unread emails from clients/team."""
        real_client = self._resolve_real_client()

        if real_client is not None:
            try:
                messages = await real_client.search(query="is:unread label:IMPORTANT newer_than:1d", max_results=max_results)
                return [
                    {
                        "id": getattr(m, "id", "m-1"),
                        "subject": getattr(m, "subject", "Important Notice"),
                        "sender": getattr(m, "sender", "Client"),
                        "snippet": getattr(m, "snippet", ""),
                    }
                    for m in messages
                ]
            except Exception as exc:
                logger.warning("Failed querying important emails: %s", exc)

        return []

    async def draft_email_reply(
        self,
        thread_id: str,
        to: str,
        subject: str,
        body: str,
    ) -> dict[str, Any]:
        """Subsystem 5, Point 43: Draft email response requiring human approval before dispatch."""
        draft = {
            "thread_id": thread_id,
            "to": to,
            "subject": f"Re: {subject}" if not subject.startswith("Re:") else subject,
            "body": body,
            "status": "draft_created",
            "requires_approval": True,
        }
        real_client = self._resolve_real_client()
        if real_client and hasattr(real_client, "create_draft"):
            try:
                return await real_client.create_draft(draft)
            except Exception as exc:
                logger.warning("Error creating Gmail draft on live API: %s", exc)

        return draft

    # ------------------------------------------------------------------
    # Subsystem 5 — additional hardening points (44)
    # ------------------------------------------------------------------
    async def download_attachments(
        self,
        message_id: str,
        target_dir: Path | str,
        allowed_extensions: tuple[str, ...] = (".pdf", ".xlsx", ".csv", ".docx"),
    ) -> list[dict[str, Any]]:
        """Subsystem 5, Point 44: Auto-download email attachments for briefing.

        When a real Gmail client is available and the message carries
        attachment data, persist each attachment under `target_dir` and
        return a manifest listing the saved files. When no client or no
        matching extension, an empty manifest is returned without raising.
        """
        target = Path(target_dir)
        target.mkdir(parents=True, exist_ok=True)

        real_client = self._resolve_real_client()
        if real_client is None:
            return []

        try:
            attachments = []
            if hasattr(real_client, "list_attachments"):
                raw = await real_client.list_attachments(message_id)
                for att in raw:
                    filename = getattr(att, "filename", "attachment.bin")
                    ext = Path(filename).suffix.lower()
                    if ext not in allowed_extensions:
                        continue
                    payload = getattr(att, "data", b"")
                    dest = target / filename
                    dest.write_bytes(payload)
                    attachments.append({
                        "filename": filename,
                        "path": str(dest),
                        "size": len(payload),
                    })
            return attachments
        except Exception as exc:
            logger.warning("Failed downloading attachments for %s: %s", message_id, exc)
            return []
