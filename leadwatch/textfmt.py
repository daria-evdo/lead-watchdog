"""Small text helpers used by the checks and the digest."""
from __future__ import annotations

from datetime import datetime
from typing import Optional


def plural(n: int, word: str, plural_word: Optional[str] = None) -> str:
    """plural(1, 'lead') -> '1 lead'; plural(3, 'lead') -> '3 leads'."""
    if n == 1:
        return f"{n} {word}"
    return f"{n} {plural_word or word + 's'}"


def fmt_hours(hours: float) -> str:
    return f"{hours:g}h"


def fmt_age(hours: float) -> str:
    """0.5 -> '30m', 31.4 -> '31h', 52 -> '2d 4h'."""
    hours = max(0.0, hours)
    if hours < 1:
        return f"{int(hours * 60)}m"
    if hours < 48:
        return f"{int(hours)}h"
    days = int(hours // 24)
    rest = int(hours - days * 24)
    return f"{days}d {rest}h" if rest else f"{days}d"


def fmt_when(moment: datetime, now: datetime) -> str:
    """'today 12:00', 'tomorrow 08:30', 'yesterday 17:00' or 'Fri 14 Nov 11:00'."""
    days = (moment.date() - now.date()).days
    if days == 0:
        day = "today"
    elif days == 1:
        day = "tomorrow"
    elif days == -1:
        day = "yesterday"
    else:
        day = moment.strftime("%a %d %b")
    return f"{day} {moment:%H:%M}"


def pct(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"


def delta(current: float, previous: float) -> str:
    """' ▼31%' / ' ▲12%' / '' when there is no baseline or no change."""
    if not previous:
        return ""
    change = (current - previous) / previous
    if abs(change) < 0.005:
        return ""
    arrow = "▲" if change > 0 else "▼"
    return f" {arrow}{abs(change) * 100:.0f}%"


def money(value: float) -> str:
    return f"${value:,.0f}"
