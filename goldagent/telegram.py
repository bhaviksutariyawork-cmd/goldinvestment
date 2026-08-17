"""Telegram Bot API: sending and polling.

Two things here are less obvious than they look.

**Markdown is not safe by default.** Telegram's legacy Markdown parser rejects a
message containing an unbalanced `*`, `_`, `` ` `` or `[`. Model output contains
those regularly — an asterisk in a footnote, an underscore in a series name like
`DFII10_x`. A rejected send returns HTTP 400 and the briefing is lost. So a
failed Markdown send is retried as plain text rather than dropped.

**Polling, not webhooks.** GitHub Actions has no long-running process to receive a
webhook, so commands arrive via `getUpdates` on the scheduled tick. The update
offset is stored in SQLite; without it, every run would re-process the same
commands.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import requests

log = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org/bot{token}/{method}"

# Telegram's hard limit is 4096 UTF-16 code units. Leave room for the part marker.
MAX_MESSAGE_CHARS = 3900
SEND_TIMEOUT = 20
POLL_TIMEOUT = 25


class TelegramError(RuntimeError):
    """A send or poll failed in a way retrying will not fix."""


@dataclass(slots=True)
class Update:
    update_id: int
    text: str
    chat_id: str
    from_id: str | None = None
    message_id: int | None = None


class Telegram:
    def __init__(
        self,
        token: str,
        chat_id: str,
        *,
        parse_mode: str = "Markdown",
        disable_preview: bool = True,
        session: requests.Session | None = None,
    ) -> None:
        self._token = token
        self.chat_id = str(chat_id)
        self.parse_mode = parse_mode
        self.disable_preview = disable_preview
        self._session = session or requests.Session()

    # ----------------------------------------------------------------- sending

    def send(self, text: str, *, chat_id: str | None = None) -> list[int]:
        """Send text, splitting long messages. Returns the message ids sent."""
        target = str(chat_id or self.chat_id)
        chunks = split_message(text)
        sent: list[int] = []

        for index, chunk in enumerate(chunks):
            if len(chunks) > 1:
                chunk = f"{chunk}\n\n_(part {index + 1} of {len(chunks)})_"
            message_id = self._send_one(chunk, target)
            if message_id is not None:
                sent.append(message_id)
            if index < len(chunks) - 1:
                # Telegram rate-limits bursts to the same chat.
                time.sleep(0.6)

        return sent

    def _send_one(self, text: str, chat_id: str) -> int | None:
        try:
            payload = self._post(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": self.parse_mode,
                    "disable_web_page_preview": self.disable_preview,
                },
            )
            return (payload.get("result") or {}).get("message_id")
        except TelegramError as exc:
            # Almost always an unbalanced formatting character. Losing the content
            # over a stray asterisk would be worse than losing the formatting.
            log.warning(
                "formatted send rejected, retrying as plain text",
                extra={"error": str(exc)[:200]},
            )

        payload = self._post(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": self.disable_preview,
            },
        )
        return (payload.get("result") or {}).get("message_id")

    # ---------------------------------------------------------------- polling

    def poll(self, offset: int = 0, limit: int = 25) -> list[Update]:
        """Fetch pending updates. `offset` must be last_seen_id + 1."""
        payload = self._post(
            "getUpdates",
            {
                "offset": offset,
                "limit": limit,
                # Short long-poll: the workflow tick is the real cadence, so there
                # is nothing to gain from holding the connection open.
                "timeout": 2,
                "allowed_updates": ["message"],
            },
            timeout=POLL_TIMEOUT,
        )

        updates: list[Update] = []
        for raw in payload.get("result", []):
            message = raw.get("message") or {}
            text = (message.get("text") or "").strip()
            chat = message.get("chat") or {}
            if not text or not chat.get("id"):
                # Still counted so the offset advances past it.
                updates.append(
                    Update(update_id=raw.get("update_id", 0), text="", chat_id=str(chat.get("id", "")))
                )
                continue
            updates.append(
                Update(
                    update_id=raw["update_id"],
                    text=text,
                    chat_id=str(chat["id"]),
                    from_id=str((message.get("from") or {}).get("id") or "") or None,
                    message_id=message.get("message_id"),
                )
            )
        return updates

    # ------------------------------------------------------------------ plumbing

    def _post(self, method: str, data: dict[str, Any], timeout: int = SEND_TIMEOUT) -> dict:
        url = API_BASE.format(token=self._token, method=method)
        try:
            response = self._session.post(url, json=data, timeout=timeout)
        except requests.RequestException as exc:
            raise TelegramError(f"{method} failed to reach Telegram: {exc}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise TelegramError(
                f"{method} returned non-JSON (HTTP {response.status_code})"
            ) from exc

        if not payload.get("ok"):
            description = payload.get("description", "no description")
            raise TelegramError(
                f"{method} rejected by Telegram (HTTP {response.status_code}): {description}"
            )
        return payload


# --------------------------------------------------------------------------- #
# Message splitting
# --------------------------------------------------------------------------- #


def split_message(text: str, limit: int = MAX_MESSAGE_CHARS) -> list[str]:
    """Split on paragraph, then line, then hard character boundaries.

    Briefings are capped at 450 words and never need this. `/deep` output and a
    long failure trace can, so it exists rather than truncating.
    """
    text = text.strip()
    if not text:
        return [""]
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""

    for paragraph in text.split("\n\n"):
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        if len(paragraph) <= limit:
            current = paragraph
            continue
        # A single oversized paragraph: break on lines, then on characters.
        for line in paragraph.split("\n"):
            candidate = f"{current}\n{line}" if current else line
            if len(candidate) <= limit:
                current = candidate
                continue
            if current:
                chunks.append(current)
                current = ""
            while len(line) > limit:
                chunks.append(line[:limit])
                line = line[limit:]
            current = line

    if current:
        chunks.append(current)
    return chunks


def failure_notice(mode: str, error: str, *, run_url: str | None = None) -> str:
    """Short notice posted when a run dies, so a break is visible not silent."""
    lines = [
        "⚠️ *Gold agent run failed*",
        "",
        f"Mode: `{mode}`",
        f"Error: {error[:600]}",
    ]
    if run_url:
        lines += ["", f"Logs: {run_url}"]
    lines += ["", "_No briefing was produced. State is unchanged._"]
    return "\n".join(lines)
