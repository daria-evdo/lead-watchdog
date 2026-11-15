"""Numbers behind the digest: time windows, funnel counts, per-source breakdown.

Two ideas worth knowing:

1. Conversion is measured on *mature* leads. A lead created yesterday has not had time to
   book a call, so comparing "last 7 days" with "the 7 days before" would always look like a
   drop. We shift the conversion windows back by `maturity_days`. Lead *volume* needs no
   such shift, so it uses the plain last 7 days.

2. When one source collapses we ask "could this be bad luck?" using a binomial test.
   Small samples wobble; this keeps the bot from crying wolf.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from .config import Config
from .models import (
    STATUS_CALL_BOOKED,
    STATUS_CONTACTED,
    STATUS_NEW,
    STATUS_WON,
    Lead,
)

_NO_SOURCE = {"", "n/a", "na", "none", "unknown", "-", "?", "null"}


def has_source(lead: Lead) -> bool:
    return lead.source.strip().lower() not in _NO_SOURCE


def is_booked(lead: Lead) -> bool:
    """A lead 'booked' if it has a call time or its status says so."""
    return lead.call_at is not None or lead.status in (STATUS_CALL_BOOKED, STATUS_WON)


@dataclass
class Funnel:
    leads: int = 0
    booked: int = 0
    won: int = 0

    @property
    def booking_rate(self) -> Optional[float]:
        return self.booked / self.leads if self.leads else None


def build_funnel(leads: List[Lead]) -> Funnel:
    return Funnel(
        leads=len(leads),
        booked=sum(1 for lead in leads if is_booked(lead)),
        won=sum(1 for lead in leads if lead.status == STATUS_WON),
    )


def in_window(leads: List[Lead], start: datetime, end: datetime) -> List[Lead]:
    """Leads created in (start, end]."""
    return [l for l in leads if l.created_at is not None and start < l.created_at <= end]


def by_source(leads: List[Lead]) -> Dict[str, Funnel]:
    groups: Dict[str, List[Lead]] = {}
    for lead in leads:
        if has_source(lead):
            groups.setdefault(lead.source, []).append(lead)
    return {source: build_funnel(group) for source, group in groups.items()}


def binom_cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p): the chance of seeing k or fewer successes."""
    if k < 0:
        return 0.0
    if k >= n or p <= 0:
        return 1.0
    if p >= 1:
        return 0.0
    return sum(math.comb(n, i) * (p ** i) * ((1 - p) ** (n - i)) for i in range(k + 1))


@dataclass
class Summary:
    total_leads: int
    volume_cur: int
    volume_prev: int
    volume_by_source_cur: Dict[str, int]
    volume_by_source_prev: Dict[str, int]
    funnel_cur: Funnel
    funnel_prev: Funnel
    sources_cur: Dict[str, Funnel]
    sources_prev: Dict[str, Funnel]
    sla_responded: int
    sla_eligible: int
    won_7d: int
    revenue_7d: float
    pipeline: Dict[str, int] = field(default_factory=dict)

    @property
    def sla_rate(self) -> Optional[float]:
        return self.sla_responded / self.sla_eligible if self.sla_eligible else None


def compute_summary(leads: List[Lead], now: datetime, cfg: Config) -> Summary:
    week = timedelta(days=cfg.window_days)
    mature = timedelta(days=cfg.maturity_days)

    volume_cur = in_window(leads, now - week, now)
    volume_prev = in_window(leads, now - 2 * week, now - week)
    rate_cur = in_window(leads, now - week - mature, now - mature)
    rate_prev = in_window(leads, now - 2 * week - mature, now - week - mature)

    # Speed to lead: of the leads old enough to have missed the SLA, how many got contact in time?
    # A non-New lead with no first-contact timestamp gets the benefit of the doubt here;
    # the data-quality check reports the missing timestamp separately.
    sla = timedelta(hours=cfg.sla_hours)
    eligible = in_window(leads, now - week, now - sla)
    responded = 0
    for lead in eligible:
        if lead.status == STATUS_NEW:
            continue
        if lead.first_contact_at is None or lead.first_contact_at - lead.created_at <= sla:
            responded += 1

    # Deals are counted on the day of the closing call (there is no separate "closed at" column).
    won = [
        l for l in leads
        if l.status == STATUS_WON and l.call_at is not None and now - week < l.call_at <= now
    ]

    return Summary(
        total_leads=len(leads),
        volume_cur=len(volume_cur),
        volume_prev=len(volume_prev),
        volume_by_source_cur=dict(Counter(l.source for l in volume_cur if has_source(l))),
        volume_by_source_prev=dict(Counter(l.source for l in volume_prev if has_source(l))),
        funnel_cur=build_funnel(rate_cur),
        funnel_prev=build_funnel(rate_prev),
        sources_cur=by_source(rate_cur),
        sources_prev=by_source(rate_prev),
        sla_responded=responded,
        sla_eligible=len(eligible),
        won_7d=len(won),
        revenue_7d=sum(l.deal_value or 0.0 for l in won),
        pipeline=dict(Counter(l.status for l in leads if l.status in (STATUS_NEW, STATUS_CONTACTED, STATUS_CALL_BOOKED))),
    )


@dataclass
class Driver:
    """A source whose booking rate fell more than luck explains."""

    source: str
    cur: Funnel
    prev: Funnel
    expected: float  # bookings we would expect at last week's rate
    lost: float      # expected minus actual
    chance: float    # probability of doing this badly (or worse) by pure luck


def find_drivers(summary: Summary, cfg: Config) -> List[Driver]:
    drivers: List[Driver] = []
    for source, cur in summary.sources_cur.items():
        prev = summary.sources_prev.get(source)
        if prev is None or cur.leads < cfg.min_source_leads or prev.leads < cfg.min_source_leads:
            continue
        prev_rate = prev.booking_rate
        if not prev_rate:
            continue
        expected = prev_rate * cur.leads
        lost = expected - cur.booked
        chance = binom_cdf(cur.booked, cur.leads, prev_rate)
        if lost >= 1.0 and chance < cfg.luck_threshold:
            drivers.append(Driver(source, cur, prev, expected, lost, chance))
    drivers.sort(key=lambda d: -d.lost)
    return drivers[:2]
