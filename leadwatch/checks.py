"""The monitors. Each check takes plain data and returns a list of Alert objects.

Keeping them as small pure functions means every rule can be tested with a handful of
made-up leads, and explained in one sentence.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

from .config import Config
from .loader import LoadResult
from .metrics import Summary, find_drivers, has_source
from .models import (
    CALL_OVERDUE,
    CONVERSION,
    CRITICAL,
    DATA_QUALITY,
    FOLLOW_UP,
    INFO,
    KIND_ORDER,
    SEVERITY_ORDER,
    STATUS_CALL_BOOKED,
    STATUS_CONTACTED,
    STATUS_NEW,
    STATUS_WON,
    STATUSES,
    STUCK_NEW,
    UPCOMING_CALL,
    VOLUME,
    WARNING,
    Alert,
    Lead,
)
from .textfmt import fmt_age, fmt_hours, fmt_when, pct, plural


def _hours(start: datetime, end: datetime) -> float:
    return (end - start).total_seconds() / 3600.0


def _luck(chance: float) -> str:
    return "<0.1%" if chance < 0.001 else f"{chance * 100:.1f}%"


# --------------------------------------------------------------------------- speed to lead

def check_stuck_new(leads: List[Lead], now: datetime, cfg: Config) -> List[Alert]:
    """New leads nobody has contacted yet, past the SLA (warning) or twice the SLA (critical)."""
    critical: List[Tuple[float, Lead]] = []
    warning: List[Tuple[float, Lead]] = []
    for lead in leads:
        if lead.status != STATUS_NEW or lead.created_at is None:
            continue
        age = _hours(lead.created_at, now)
        if age > cfg.critical_hours:
            critical.append((age, lead))
        elif age > cfg.sla_hours:
            warning.append((age, lead))

    alerts: List[Alert] = []
    for bucket, severity, limit in (
        (critical, CRITICAL, cfg.critical_hours),
        (warning, WARNING, cfg.sla_hours),
    ):
        if not bucket:
            continue
        bucket.sort(key=lambda pair: -pair[0])
        unassigned = sum(1 for _, lead in bucket if not lead.owner)
        hint = ""
        if unassigned:
            hint = (
                f"{unassigned} of these {'has' if unassigned == 1 else 'have'} no owner. "
                "If this keeps happening, check the lead-assignment automation."
            )
        alerts.append(
            Alert(
                kind=STUCK_NEW,
                severity=severity,
                title=f"{plural(len(bucket), 'new lead')} waiting more than {fmt_hours(limit)} without contact",
                items=[
                    f"{lead.label} · {lead.source or 'no source'} · {fmt_age(age)} · "
                    f"{lead.owner or 'unassigned'} (row {lead.row})"
                    for age, lead in bucket
                ],
                lead_ids=[lead.lead_id for _, lead in bucket],
                hint=hint,
            )
        )
    return alerts


def check_follow_ups(leads: List[Lead], now: datetime, cfg: Config) -> List[Alert]:
    """Contacted leads nobody has touched for too long are going cold."""
    cold: List[Tuple[float, Lead]] = []
    for lead in leads:
        if lead.status != STATUS_CONTACTED:
            continue
        last_touch = lead.last_contact_at or lead.first_contact_at or lead.created_at
        if last_touch is None:
            continue
        idle = _hours(last_touch, now)
        if idle > cfg.follow_up_hours:
            cold.append((idle, lead))
    if not cold:
        return []
    cold.sort(key=lambda pair: -pair[0])
    return [
        Alert(
            kind=FOLLOW_UP,
            severity=WARNING,
            title=f"{plural(len(cold), 'contacted lead')} going cold (no touch for {fmt_hours(cfg.follow_up_hours)}+)",
            items=[
                f"{lead.label} · {lead.source or 'no source'} · last touch {fmt_age(idle)} ago · "
                f"{lead.owner or 'unassigned'} (row {lead.row})"
                for idle, lead in cold
            ],
            lead_ids=[lead.lead_id for _, lead in cold],
        )
    ]


# --------------------------------------------------------------------------- calls

def check_overdue_calls(leads: List[Lead], now: datetime, cfg: Config) -> List[Alert]:
    """Call time has passed but the status still says 'Call booked'."""
    overdue: List[Tuple[float, Lead]] = []
    for lead in leads:
        if lead.status == STATUS_CALL_BOOKED and lead.call_at is not None:
            late = _hours(lead.call_at, now)
            if late > cfg.call_grace_hours:
                overdue.append((late, lead))
    if not overdue:
        return []
    overdue.sort(key=lambda pair: -pair[0])
    return [
        Alert(
            kind=CALL_OVERDUE,
            severity=WARNING,
            title=f"{plural(len(overdue), 'call')} in the past but still marked 'Call booked'",
            items=[
                f"{lead.label} · was {fmt_when(lead.call_at, now)} ({fmt_age(late)} ago) · "
                f"{lead.owner or 'unassigned'} (row {lead.row})"
                for late, lead in overdue
            ],
            lead_ids=[lead.lead_id for _, lead in overdue],
            hint="Update the status (held, no-show or rescheduled) so the pipeline stays true.",
        )
    ]


def check_upcoming_calls(leads: List[Lead], now: datetime, cfg: Config) -> List[Alert]:
    horizon = now + timedelta(hours=cfg.upcoming_call_hours)
    upcoming = sorted(
        (l for l in leads if l.status == STATUS_CALL_BOOKED and l.call_at is not None and now <= l.call_at <= horizon),
        key=lambda l: l.call_at,
    )
    if not upcoming:
        return []
    return [
        Alert(
            kind=UPCOMING_CALL,
            severity=INFO,
            title=f"{plural(len(upcoming), 'call')} in the next {fmt_hours(cfg.upcoming_call_hours)}",
            items=[
                f"{fmt_when(l.call_at, now)} · {l.label} · {l.owner or 'unassigned'} · {l.source or 'no source'}"
                for l in upcoming
            ],
            lead_ids=[l.lead_id for l in upcoming],
        )
    ]


# --------------------------------------------------------------------------- funnel

def check_conversion(summary: Summary, cfg: Config) -> List[Alert]:
    """Booking rate this week vs last week (on mature leads), with the source that explains it."""
    cur, prev = summary.funnel_cur, summary.funnel_prev
    if cur.leads < cfg.min_window_leads or prev.leads < cfg.min_window_leads:
        return []
    rate_cur, rate_prev = cur.booking_rate, prev.booking_rate
    if not rate_prev:
        return []
    drop = (rate_prev - rate_cur) / rate_prev
    if drop < cfg.drop_threshold:
        return []

    drivers = find_drivers(summary, cfg)
    items = [
        f"{cur.booked} of {cur.leads} leads booked a call "
        f"(vs {prev.booked} of {prev.leads} the week before)"
    ]
    for d in drivers:
        items.append(
            f"{d.source}: {d.cur.booked} of {d.cur.leads} booked "
            f"(before: {d.prev.booked} of {d.prev.leads}) · chance this is just luck: {_luck(d.chance)}"
        )

    if any(d.cur.booked == 0 for d in drivers):
        hint = (
            "Zero bookings from one source usually means a broken step on that channel "
            "(booking link, form or automation). Test it end to end first."
        )
    elif drivers:
        hint = f"The drop is concentrated in {drivers[0].source}. Check what changed there (offer, ad, page, booking flow)."
    else:
        hint = "No single source explains it. Check the whole booking flow and whether lead quality changed."

    severity = CRITICAL if drop >= cfg.critical_drop_threshold or any(d.cur.booked == 0 for d in drivers) else WARNING
    return [
        Alert(
            kind=CONVERSION,
            severity=severity,
            title=f"Booking rate down {drop * 100:.0f}% ({pct(rate_prev)} → {pct(rate_cur)})",
            items=items,
            hint=hint,
        )
    ]


def check_volume(summary: Summary, cfg: Config) -> List[Alert]:
    """Fewer leads coming in than last week."""
    cur, prev = summary.volume_cur, summary.volume_prev
    if prev < cfg.min_window_leads:
        return []
    drop = (prev - cur) / prev
    if drop < cfg.volume_drop_threshold:
        return []
    changes: List[Tuple[int, str]] = []
    sources = set(summary.volume_by_source_cur) | set(summary.volume_by_source_prev)
    for source in sources:
        before = summary.volume_by_source_prev.get(source, 0)
        after = summary.volume_by_source_cur.get(source, 0)
        if before - after > 0:
            changes.append((before - after, f"{source}: {after} vs {before} the week before"))
    changes.sort(key=lambda pair: -pair[0])
    return [
        Alert(
            kind=VOLUME,
            severity=CRITICAL if drop >= cfg.critical_volume_drop_threshold else WARNING,
            title=f"Lead volume down {drop * 100:.0f}% ({cur} vs {prev} leads in 7 days)",
            items=[text for _, text in changes[:2]],
            hint="Check ad accounts and budgets, tracking links and that the lead form still works.",
        )
    ]


# --------------------------------------------------------------------------- data quality

def _email_key(lead: Lead) -> str:
    email = lead.email.strip().lower()
    return email if "@" in email else ""


def _phone_key(lead: Lead) -> str:
    digits = re.sub(r"\D", "", lead.phone)
    return digits[-10:] if len(digits) >= 7 else ""  # same number with or without +1


def check_data_quality(result: LoadResult, now: datetime, cfg: Config) -> List[Alert]:
    leads = result.leads
    alerts: List[Alert] = []

    if result.issues:
        alerts.append(
            Alert(
                kind=DATA_QUALITY,
                severity=WARNING,
                title=f"{plural(len(result.issues), 'cell')} can't be read",
                items=[
                    f"{i.label} · {i.field} \"{i.value}\" (row {i.row})" for i in result.issues
                ],
                hint="These leads are skipped by the time-based checks until the cell is fixed.",
            )
        )

    # duplicates (same email, or same phone number)
    dupe_items: List[str] = []
    dupe_ids: List[str] = []
    seen_groups = set()
    for key_name, key_fn in (("email", _email_key), ("phone", _phone_key)):
        groups: Dict[str, List[Lead]] = defaultdict(list)
        for lead in leads:
            key = key_fn(lead)
            if key:
                groups[key].append(lead)
        for group in groups.values():
            if len(group) < 2:
                continue
            ids = frozenset(l.lead_id for l in group)
            if ids in seen_groups:
                continue
            seen_groups.add(ids)
            names = " / ".join(dict.fromkeys(l.label for l in group))
            rows = ", ".join(str(l.row) for l in group)
            dupe_items.append(f"{names} · same {key_name} (rows {rows})")
            dupe_ids.extend(l.lead_id for l in group)
    if dupe_items:
        alerts.append(
            Alert(
                kind=DATA_QUALITY,
                severity=WARNING,
                title=f"{plural(len(dupe_items), 'possible duplicate')}",
                items=dupe_items,
                lead_ids=dupe_ids,
                hint="Merge or mark them so one person isn't counted (or contacted) twice.",
            )
        )

    no_source = [l for l in leads if not has_source(l)]
    if no_source:
        alerts.append(
            Alert(
                kind=DATA_QUALITY,
                severity=INFO,
                title=f"{plural(len(no_source), 'lead')} with no source",
                items=[f"{l.label} · {l.status or 'no status'} (row {l.row})" for l in no_source],
                lead_ids=[l.lead_id for l in no_source],
                hint="Without a source we can't tell which channel works. Fill it from the form or UTM data.",
            )
        )

    bad_status = [l for l in leads if l.status not in STATUSES]
    if bad_status:
        alerts.append(
            Alert(
                kind=DATA_QUALITY,
                severity=WARNING,
                title=f"{plural(len(bad_status), 'lead')} with an unknown status",
                items=[f"{l.label} · \"{l.status or 'empty'}\" (row {l.row})" for l in bad_status],
                lead_ids=[l.lead_id for l in bad_status],
                hint=f"Allowed: {', '.join(STATUSES)}.",
            )
        )

    contradictions: List[Tuple[Lead, str]] = []
    for lead in leads:
        if lead.status in (STATUS_CONTACTED, STATUS_CALL_BOOKED, STATUS_WON) and lead.first_contact_at is None:
            contradictions.append((lead, f"status is {lead.status} but there is no first-contact time"))
        if lead.status == STATUS_CALL_BOOKED and lead.call_at is None:
            contradictions.append((lead, "status is Call booked but there is no call time"))
        if lead.status == STATUS_WON and not lead.deal_value:
            contradictions.append((lead, "status is Won but there is no deal value (revenue is understated)"))
        if lead.created_at and lead.call_at and lead.call_at < lead.created_at:
            contradictions.append((lead, "call time is before the lead was created"))
        if lead.created_at and lead.first_contact_at and lead.first_contact_at < lead.created_at:
            contradictions.append((lead, "first contact is before the lead was created"))
    if contradictions:
        alerts.append(
            Alert(
                kind=DATA_QUALITY,
                severity=WARNING,
                title=f"{plural(len(contradictions), 'row')} contradict themselves",
                items=[f"{lead.label}: {reason} (row {lead.row})" for lead, reason in contradictions],
                lead_ids=[lead.lead_id for lead, _ in contradictions],
            )
        )

    future = [l for l in leads if l.created_at and l.created_at > now + timedelta(hours=1)]
    if future:
        alerts.append(
            Alert(
                kind=DATA_QUALITY,
                severity=WARNING,
                title=f"{plural(len(future), 'lead')} dated in the future",
                items=[f"{l.label} · {l.created_at:%Y-%m-%d %H:%M} (row {l.row})" for l in future],
                lead_ids=[l.lead_id for l in future],
                hint="Usually a timezone mismatch. Check LEADWATCH_UTC_OFFSET against the sheet's timezone.",
            )
        )
    return alerts


# --------------------------------------------------------------------------- all together

def run_all_checks(result: LoadResult, summary: Summary, now: datetime, cfg: Config) -> List[Alert]:
    leads = result.leads
    alerts: List[Alert] = []
    alerts += check_conversion(summary, cfg)
    alerts += check_volume(summary, cfg)
    alerts += check_stuck_new(leads, now, cfg)
    alerts += check_overdue_calls(leads, now, cfg)
    alerts += check_follow_ups(leads, now, cfg)
    alerts += check_upcoming_calls(leads, now, cfg)
    alerts += check_data_quality(result, now, cfg)
    alerts.sort(key=lambda a: (SEVERITY_ORDER.index(a.severity), KIND_ORDER.index(a.kind)))
    return alerts
