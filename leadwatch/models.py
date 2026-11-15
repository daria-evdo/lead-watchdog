"""Plain data containers and constants shared by the whole package."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

# Lead statuses (the only values the sheet should contain)
STATUS_NEW = "New"
STATUS_CONTACTED = "Contacted"
STATUS_CALL_BOOKED = "Call booked"
STATUS_WON = "Won"
STATUS_LOST = "Lost"
STATUSES = (STATUS_NEW, STATUS_CONTACTED, STATUS_CALL_BOOKED, STATUS_WON, STATUS_LOST)

# Alert kinds. The tuple order is the order alerts appear in the digest.
CONVERSION = "conversion"
VOLUME = "volume"
STUCK_NEW = "stuck_new"
CALL_OVERDUE = "call_overdue"
FOLLOW_UP = "follow_up"
UPCOMING_CALL = "upcoming_call"
DATA_QUALITY = "data_quality"
KIND_ORDER = (CONVERSION, VOLUME, STUCK_NEW, CALL_OVERDUE, FOLLOW_UP, UPCOMING_CALL, DATA_QUALITY)

CRITICAL = "critical"
WARNING = "warning"
INFO = "info"
SEVERITY_ORDER = (CRITICAL, WARNING, INFO)


@dataclass
class Lead:
    """One row of the lead sheet."""

    row: int  # spreadsheet row number (header is row 1), so a human can find it
    lead_id: str
    created_at: Optional[datetime]
    name: str = ""
    email: str = ""
    phone: str = ""
    source: str = ""
    status: str = ""
    owner: str = ""
    first_contact_at: Optional[datetime] = None
    last_contact_at: Optional[datetime] = None
    call_at: Optional[datetime] = None
    deal_value: Optional[float] = None

    @property
    def label(self) -> str:
        return self.name or self.lead_id


@dataclass
class Alert:
    """One finding. Items are human-readable lines; lead_ids make it checkable."""

    kind: str
    severity: str
    title: str
    items: List[str] = field(default_factory=list)
    lead_ids: List[str] = field(default_factory=list)
    hint: str = ""
