"""Command line entry point: `python -m leadwatch`."""
from __future__ import annotations

import argparse
import os
import sys
import urllib.error
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from . import __version__
from .checks import run_all_checks
from .config import Config, load_env_file
from .loader import DataSourceError, load_leads
from .metrics import compute_summary
from .report import build_digest, source_label
from .telegram import TelegramError, find_chats, send_digest, send_message


def _parse_now(text: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text.strip(), fmt)
        except ValueError:
            continue
    raise ValueError(f'--now must look like "2026-11-12 09:00", got {text!r}')


def current_time(cfg: Config) -> datetime:
    """'Now' in the sheet's timezone, as a naive datetime (all sheet times are naive too)."""
    utc_now = datetime.now(timezone.utc)
    return (utc_now + timedelta(hours=cfg.utc_offset_hours)).replace(tzinfo=None, microsecond=0)


def describe_error(exc: Exception) -> str:
    """A short reason that is safe to post in a chat (no URLs, tokens or tracebacks)."""
    if isinstance(exc, (DataSourceError, TelegramError)):
        return str(exc)
    if isinstance(exc, urllib.error.URLError):
        return "network error while reading the sheet"
    return f"unexpected error ({type(exc).__name__})"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="leadwatch",
        description="Watches a lead sheet and sends a daily digest of what needs attention.",
    )
    parser.add_argument("--source", help="path or URL of the lead CSV (default: $LEADWATCH_SOURCE or data/leads_demo.csv)")
    parser.add_argument("--now", help='pretend it is this moment, e.g. "2026-11-12 09:00" (demos and tests)')
    parser.add_argument("--send", action="store_true", help="send the digest to Telegram")
    parser.add_argument("--print", dest="show", action="store_true", help="with --send: also print the digest")
    parser.add_argument("--ping", action="store_true", help="send a test message to Telegram and exit")
    parser.add_argument("--find-chat", action="store_true", help="list chats that messaged the bot (to find the chat id)")
    parser.add_argument("--sla-hours", type=float, help="hours allowed before a new lead counts as stuck")
    parser.add_argument("--version", action="version", version=f"leadwatch {__version__}")
    return parser


def _telegram_settings() -> tuple:
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(), os.environ.get("TELEGRAM_CHAT_ID", "").strip()


def main(argv: Optional[List[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # emoji must not crash a Windows console
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    load_env_file()
    args = build_parser().parse_args(argv)
    token, chat_id = _telegram_settings()

    try:
        cfg = Config.from_env()
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    if args.source:
        cfg = replace(cfg, source=args.source)
    if args.sla_hours:
        cfg = replace(cfg, sla_hours=args.sla_hours)

    if args.find_chat:
        if not token:
            print("Set TELEGRAM_BOT_TOKEN first (see README).", file=sys.stderr)
            return 2
        try:
            chats = find_chats(token)
        except TelegramError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        if not chats:
            print("No chats found. Send any message to your bot in Telegram, then run this again.")
            return 0
        for chat in chats:
            print(f"TELEGRAM_CHAT_ID={chat['id']}   ({chat['type']}: {chat['name']})")
        return 0

    if (args.send or args.ping) and not (token and chat_id):
        print("Telegram is not configured: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID (see README).", file=sys.stderr)
        return 2

    if args.ping:
        try:
            send_message(token, chat_id, "✅ Lead Watchdog is connected to this chat.")
        except TelegramError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        print("Test message sent.")
        return 0

    try:
        now = _parse_now(args.now) if args.now else current_time(cfg)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    try:
        result = load_leads(cfg.source)
        summary = compute_summary(result.leads, now, cfg)
        alerts = run_all_checks(result, summary, now, cfg)
        text = build_digest(summary, alerts, now, cfg, source_label(cfg.source))
    except Exception as exc:  # noqa: BLE001 - a monitor must report its own failure
        reason = describe_error(exc)
        print(f"Lead Watchdog could not run: {reason}", file=sys.stderr)
        if args.send:
            warning = (
                "⚠️ Lead Watchdog could not run today\n"
                f"Reason: {reason}\n"
                "Nothing was checked, so do not read the silence as 'all clear'."
            )
            try:
                send_message(token, chat_id, warning)
            except TelegramError as send_exc:
                print(f"Could not send the warning either: {send_exc}", file=sys.stderr)
        return 1

    if args.send:
        try:
            parts = send_digest(token, chat_id, text)
        except TelegramError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        # Lead names are personal data: keep them out of CI logs unless asked.
        print(f"Digest sent ({parts} message{'s' if parts != 1 else ''}, {len(text)} characters).")
        if args.show:
            print()
            print(text)
    else:
        print(text)
    return 0
