"""Turn the numbers and alerts into one short Telegram-sized message."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import List

from .config import Config
from .metrics import Summary
from .models import CRITICAL, DATA_QUALITY, UPCOMING_CALL, Alert
from .textfmt import delta, money, pct, plural

TELEGRAM_LIMIT = 4000  # the real limit is 4096 characters


def source_label(source: str) -> str:
    """Never print the sheet URL: it is a secret-ish link."""
    if source.lower().startswith(("http://", "https://")):
        return "Google Sheet (published CSV)"
    return Path(source).name


def _render_alert(alert: Alert, limit: int) -> List[str]:
    lines = [f"▸ {alert.title}"]
    lines += [f"   • {item}" for item in alert.items[:limit]]
    hidden = len(alert.items) - limit
    if hidden > 0:
        lines.append(f"   … and {hidden} more")
    if alert.hint:
        lines.append(f"   → {alert.hint}")
    return lines


def build_digest(
    summary: Summary,
    alerts: List[Alert],
    now: datetime,
    cfg: Config,
    source_name: str,
) -> str:
    action = [a for a in alerts if a.kind not in (UPCOMING_CALL, DATA_QUALITY) and a.severity == CRITICAL]
    watch = [a for a in alerts if a.kind not in (UPCOMING_CALL, DATA_QUALITY) and a.severity != CRITICAL]
    calls = [a for a in alerts if a.kind == UPCOMING_CALL]
    data = [a for a in alerts if a.kind == DATA_QUALITY]
    n_calls = sum(len(a.items) for a in calls)

    out: List[str] = [
        f"📊 Lead Watchdog · {now:%a %d %b, %H:%M}",
        f"Source: {source_name} · {summary.total_leads} leads loaded",
        "",
        f"{len(action)} need action · {len(watch)} to watch · {n_calls} calls in {cfg.upcoming_call_hours:g}h · "
        f"{plural(len(data), 'data issue')}",
        "",
        f"LAST {cfg.window_days} DAYS vs THE {cfg.window_days} BEFORE",
        f"Leads: {summary.volume_cur} (before: {summary.volume_prev}){delta(summary.volume_cur, summary.volume_prev)}",
    ]
    if summary.sla_rate is not None:
        out.append(
            f"Contacted within {cfg.sla_hours:g}h: {pct(summary.sla_rate)} "
            f"({summary.sla_responded} of {summary.sla_eligible})"
        )
    rate_cur, rate_prev = summary.funnel_cur.booking_rate, summary.funnel_prev.booking_rate
    out.append(
        f"Booking rate: {pct(rate_cur)} (before: {pct(rate_prev)})"
        f"{delta(rate_cur or 0, rate_prev or 0)}"
    )
    out.append(f"Won: {plural(summary.won_7d, 'deal')} · {money(summary.revenue_7d)}")
    if summary.pipeline:
        pipeline = " · ".join(
            f"{summary.pipeline[s]} {s.lower()}" for s in ("New", "Contacted", "Call booked") if s in summary.pipeline
        )
        out.append(f"Pipeline now: {pipeline}")
    far = cfg.window_days + cfg.maturity_days
    out.append(
        f"(Booking rate uses leads from {far} to {cfg.maturity_days} days ago, "
        "so newer leads have time to convert.)"
    )

    if not action and not watch:
        out += ["", "✅ Nothing needs action right now."]
    if action:
        out += ["", "🔴 ACTION NEEDED"]
        for alert in action:
            out += _render_alert(alert, cfg.max_items)
    if watch:
        out += ["", "🟡 KEEP AN EYE ON"]
        for alert in watch:
            out += _render_alert(alert, cfg.max_items)
    if calls:
        out += ["", f"📅 CALLS IN THE NEXT {cfg.upcoming_call_hours:g}H"]
        for alert in calls:
            out += [f"   • {item}" for item in alert.items[: cfg.max_items]]
            hidden = len(alert.items) - cfg.max_items
            if hidden > 0:
                out.append(f"   … and {hidden} more")
    if data:
        out += ["", "🧹 DATA CLEAN-UP"]
        for alert in data:
            out += _render_alert(alert, min(cfg.max_items, 3))
    return "\n".join(out)


def split_message(text: str, limit: int = TELEGRAM_LIMIT) -> List[str]:
    """Split on line breaks so each Telegram message stays under the size limit."""
    if len(text) <= limit:
        return [text]
    chunks: List[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:  # a single monster line: hard split
            head, line = line[:limit], line[limit:]
            if current:
                chunks.append(current)
                current = ""
            chunks.append(head)
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks
