"""Minimal Telegram Bot API client (standard library only).

The bot token is part of the request URL, so errors are re-raised WITHOUT the original
exception chained (`from None`): a traceback must never be able to print the token.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Callable, Dict, List, Optional

from .report import split_message

API_URL = "https://api.telegram.org/bot{token}/{method}"


class TelegramError(Exception):
    """Something went wrong talking to Telegram. The message never contains the token."""


def _call(
    token: str,
    method: str,
    payload: dict,
    opener: Optional[Callable] = None,
    timeout: float = 20,
) -> object:
    opener = opener or urllib.request.urlopen
    request = urllib.request.Request(
        API_URL.format(token=token, method=method),
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with opener(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        description = ""
        try:
            description = json.loads(exc.read().decode("utf-8")).get("description", "")
        except Exception:  # the error body is a bonus, never a reason to crash
            pass
        raise TelegramError(
            f"Telegram refused the request (HTTP {exc.code}){': ' + description if description else ''}"
        ) from None
    except urllib.error.URLError as exc:
        raise TelegramError(f"Could not reach Telegram: {exc.reason}") from None
    if not body.get("ok"):
        raise TelegramError(f"Telegram refused the request: {body.get('description', 'unknown error')}")
    return body.get("result")


def send_message(token: str, chat_id: str, text: str, opener: Optional[Callable] = None) -> None:
    """Send one message (plain text: no formatting characters to escape)."""
    _call(
        token,
        "sendMessage",
        {"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        opener=opener,
    )


def send_digest(token: str, chat_id: str, text: str, opener: Optional[Callable] = None) -> int:
    """Send a possibly long digest as several messages. Returns how many were sent."""
    parts = split_message(text)
    for index, part in enumerate(parts):
        if index:
            time.sleep(1)  # be polite: Telegram rate-limits bursts
        send_message(token, chat_id, part, opener=opener)
    return len(parts)


def find_chats(token: str, opener: Optional[Callable] = None) -> List[Dict[str, str]]:
    """List the chats that recently wrote to the bot (to discover TELEGRAM_CHAT_ID)."""
    updates = _call(token, "getUpdates", {"limit": 100}, opener=opener) or []
    chats: Dict[str, Dict[str, str]] = {}
    for update in updates:
        message = update.get("message") or update.get("channel_post") or update.get("my_chat_member") or {}
        chat = message.get("chat") or {}
        if "id" in chat:
            title = chat.get("title") or " ".join(
                part for part in (chat.get("first_name"), chat.get("last_name")) if part
            ) or chat.get("username", "")
            chats[str(chat["id"])] = {"id": str(chat["id"]), "name": title, "type": chat.get("type", "")}
    return list(chats.values())
