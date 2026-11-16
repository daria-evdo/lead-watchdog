"""Thresholds and settings. Secrets (Telegram token) are NOT stored here on purpose."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_env_file(path: str = ".env") -> None:
    """Tiny .env reader so beginners don't need extra packages.

    Real environment variables always win over the file.
    """
    env_path = Path(path)
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class Config:
    # Where the leads live: a file path or a "published to web" CSV link.
    source: str = "data/leads_demo.csv"
    # Hours between UTC and the timezone the sheet's timestamps are written in.
    # Minsk = 3, Dubai = 4. (No DST in either, so a plain number is enough.)
    utc_offset_hours: float = 3.0

    # --- speed-to-lead and follow-up rules (hours) ---
    sla_hours: float = 24.0          # a new lead must be contacted within this
    critical_hours: float = 48.0     # ...after this it becomes "act now"
    follow_up_hours: float = 72.0    # a contacted lead untouched this long is going cold
    call_grace_hours: float = 2.0    # a booked call this far in the past needs a status update
    upcoming_call_hours: float = 24.0

    # --- funnel comparison ---
    window_days: int = 7             # compare the last 7 days with the 7 before
    maturity_days: int = 2           # skip the newest leads when measuring conversion
    drop_threshold: float = 0.20     # booking rate down 20%+ -> warning
    critical_drop_threshold: float = 0.40
    volume_drop_threshold: float = 0.30
    critical_volume_drop_threshold: float = 0.50
    min_window_leads: int = 15       # don't judge a week with fewer leads than this
    min_source_leads: int = 6        # ...or a single source with fewer than this
    luck_threshold: float = 0.05     # a source drop must be <5% likely to be pure luck

    # --- message size ---
    max_items: int = 4               # lines shown per alert before "... and N more"

    @classmethod
    def from_env(cls) -> "Config":
        def number(name: str, default: float) -> float:
            raw = os.environ.get(name, "").strip()
            if not raw:
                return default
            try:
                return float(raw)
            except ValueError:
                raise ValueError(f"{name} must be a number, got {raw!r}") from None

        return cls(
            source=os.environ.get("LEADWATCH_SOURCE", "").strip() or cls.source,
            utc_offset_hours=number("LEADWATCH_UTC_OFFSET", cls.utc_offset_hours),
            sla_hours=number("LEADWATCH_SLA_HOURS", cls.sla_hours),
        )
