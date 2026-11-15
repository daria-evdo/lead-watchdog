"""Read the lead sheet (file or published Google Sheet CSV) into Lead objects.

Design rule: bad *cells* never crash the run. They become issues that the data-quality
check reports. Bad *sheets* (wrong link, HTML page, missing columns) raise DataSourceError,
because a monitor that silently checks nothing is worse than no monitor.
"""
from __future__ import annotations

import csv
import io
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .models import (
    STATUS_CALL_BOOKED,
    STATUS_CONTACTED,
    STATUS_LOST,
    STATUS_NEW,
    STATUS_WON,
    Lead,
)


class DataSourceError(Exception):
    """The lead sheet could not be read, or it does not look like a lead sheet."""


# Header names we accept for each field (after lower-casing and replacing symbols with "_").
# Google Forms writes "Timestamp" and "Email Address", so those work out of the box.
HEADER_ALIASES: Dict[str, tuple] = {
    "created_at": ("created_at", "created", "timestamp", "submitted_at", "date"),
    "lead_id": ("lead_id", "id"),
    "name": ("name", "full_name"),
    "email": ("email", "email_address", "e_mail"),
    "phone": ("phone", "phone_number", "mobile", "tel"),
    "source": ("source", "lead_source", "utm_source", "channel"),
    "status": ("status", "stage"),
    "owner": ("owner", "assigned_to", "setter"),
    "first_contact_at": ("first_contact_at", "first_contact", "first_response_at"),
    "last_contact_at": ("last_contact_at", "last_contact", "last_touch"),
    "call_at": ("call_at", "call_time", "call_date", "booked_call"),
    "deal_value": ("deal_value", "value", "amount", "revenue"),
}
_ALIAS_TO_FIELD = {
    alias: canonical for canonical, aliases in HEADER_ALIASES.items() for alias in aliases
}
REQUIRED_FIELDS = ("created_at", "status")

_STATUS_ALIASES = {
    "new": STATUS_NEW,
    "contacted": STATUS_CONTACTED,
    "call booked": STATUS_CALL_BOOKED,
    "booked": STATUS_CALL_BOOKED,
    "won": STATUS_WON,
    "closed won": STATUS_WON,
    "lost": STATUS_LOST,
    "closed lost": STATUS_LOST,
}

# Order matters only for ambiguous slash dates: "03/04/2026" is read as month/day (US,
# what Google Forms uses by default). A slash date that cannot be month/day, such as
# "25/12/2026", falls through to day/month. Dotted dates are always day.month.year.
DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d",
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y %H:%M",
    "%d.%m.%Y",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
    "%m/%d/%Y",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y",
)


def parse_datetime(text: str) -> Optional[datetime]:
    """Empty -> None. Unreadable -> ValueError (the caller records it as an issue)."""
    text = (text or "").strip()
    if not text:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"unreadable date: {text!r}")


def parse_money(text: str) -> Optional[float]:
    """'$2,500' -> 2500.0, '1 500' -> 1500.0, '' -> None."""
    cleaned = re.sub(r"[^\d.,\-]", "", text or "")
    if not cleaned:
        if (text or "").strip():  # something was typed, but it has no digits ("n/a", "tbd")
            raise ValueError(f"unreadable amount: {text!r}")
        return None
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        # "1,500" is a thousands separator; "15,5" is a decimal comma.
        cleaned = cleaned.replace(",", "") if re.search(r",\d{3}$", cleaned) else cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        raise ValueError(f"unreadable amount: {text!r}") from None


def canonical_status(text: str) -> str:
    key = re.sub(r"[\s_\-]+", " ", (text or "").strip().lower())
    return _STATUS_ALIASES.get(key, (text or "").strip())


def _norm_header(header: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", header.strip().lower()).strip("_")


@dataclass
class RowIssue:
    row: int
    label: str
    field: str
    value: str


@dataclass
class LoadResult:
    leads: List[Lead] = field(default_factory=list)
    issues: List[RowIssue] = field(default_factory=list)


def read_source(source: str, timeout: float = 20, opener: Optional[Callable] = None) -> str:
    """Return the CSV text from a URL or a local file."""
    if source.lower().startswith(("http://", "https://")):
        opener = opener or urllib.request.urlopen
        request = urllib.request.Request(source, headers={"User-Agent": "leadwatch/1.0"})
        try:
            with opener(request, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise DataSourceError(
                f"The sheet link answered HTTP {exc.code}. Is it still published to the web as CSV?"
            ) from None
        except urllib.error.URLError as exc:
            raise DataSourceError(f"Could not reach the sheet: {exc.reason}") from None
    else:
        try:
            raw = Path(source).read_bytes()
        except FileNotFoundError:
            raise DataSourceError(f"File not found: {source}") from None
        except OSError as exc:
            raise DataSourceError(f"Could not read {source}: {exc.strerror or exc}") from None
    return raw.decode("utf-8-sig", errors="replace")


def load_leads(source: str, opener: Optional[Callable] = None) -> LoadResult:
    return parse_csv(read_source(source, opener=opener))


def parse_csv(text: str) -> LoadResult:
    head = text.lstrip()[:300].lower()
    if head.startswith("<!doctype") or "<html" in head:
        raise DataSourceError(
            "Got a web page instead of CSV data. Is the sheet published to the web as CSV "
            "(File > Share > Publish to web > CSV)?"
        )
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise DataSourceError("The sheet is empty: no header row found.")

    columns: Dict[str, str] = {}
    for header in reader.fieldnames:
        canonical = _ALIAS_TO_FIELD.get(_norm_header(header or ""))
        if canonical and canonical not in columns:
            columns[canonical] = header
    missing = [name for name in REQUIRED_FIELDS if name not in columns]
    if missing:
        seen = ", ".join(h for h in reader.fieldnames if h)
        raise DataSourceError(f"Required column(s) not found: {', '.join(missing)}. Columns seen: {seen}")

    def cell(raw: dict, name: str) -> str:
        header = columns.get(name)
        return (raw.get(header) or "").strip() if header else ""

    result = LoadResult()
    spellings: Dict[str, str] = {}  # "instagram" and "Instagram" must be one source
    for index, raw in enumerate(reader, start=2):
        if not any(isinstance(v, str) and v.strip() for k, v in raw.items() if k is not None):
            continue  # fully blank row
        lead_id = cell(raw, "lead_id") or f"row-{index}"
        name = cell(raw, "name")
        label = name or lead_id

        def date_cell(field_name: str) -> Optional[datetime]:
            value = cell(raw, field_name)
            try:
                return parse_datetime(value)
            except ValueError:
                result.issues.append(RowIssue(index, label, field_name, value))
                return None

        deal_text = cell(raw, "deal_value")
        try:
            deal_value = parse_money(deal_text)
        except ValueError:
            result.issues.append(RowIssue(index, label, "deal_value", deal_text))
            deal_value = None

        source_text = re.sub(r"\s+", " ", cell(raw, "source"))
        if source_text:
            source_text = spellings.setdefault(source_text.casefold(), source_text)

        result.leads.append(
            Lead(
                row=index,
                lead_id=lead_id,
                created_at=date_cell("created_at"),
                name=name,
                email=cell(raw, "email"),
                phone=cell(raw, "phone"),
                source=source_text,
                status=canonical_status(cell(raw, "status")),
                owner=cell(raw, "owner"),
                first_contact_at=date_cell("first_contact_at"),
                last_contact_at=date_cell("last_contact_at"),
                call_at=date_cell("call_at"),
                deal_value=deal_value,
            )
        )
    return result
