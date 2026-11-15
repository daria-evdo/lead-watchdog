#!/usr/bin/env python3
"""Generate a synthetic, reproducible lead sheet for the Lead Watchdog demo.

Everything here is invented: people are random name combinations, e-mails use the
reserved example.com domain, phone numbers use the fictional 555-01xx range.

The data tells one story on purpose:
  * the booking link of one ad channel ("Facebook Ads") "breaks" 9 days before `now`,
    so that channel books zero calls from then on;
  * a handful of everyday operational problems are planted (stuck leads, cold follow-ups,
    overdue calls, duplicates, missing fields) so every check has something to find.

Usage:
    python scripts/make_demo_data.py                       # relative to the current time
    python scripts/make_demo_data.py --now "2026-11-12 09:00"   # reproducible
"""
from __future__ import annotations

import argparse
import csv
import random
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

FIELDS = [
    "lead_id", "created_at", "name", "email", "phone", "source", "status", "owner",
    "first_contact_at", "last_contact_at", "call_at", "deal_value",
]

# source: (share of all leads, chance that a lead books a call)
SOURCES = {
    "Instagram": (0.28, 0.30),
    "Facebook Ads": (0.34, 0.33),
    "YouTube": (0.14, 0.38),
    "Referral": (0.10, 0.55),
    "Webinar": (0.14, 0.40),
}
BROKEN_SOURCE = "Facebook Ads"
BROKEN_DAYS_AGO = 9
SETTERS = ["Anna", "Marco", "Priya", "Tomas"]
DEAL_VALUES = [1500, 2000, 2500, 3000, 4000, 5000]

FIRST_NAMES = [
    "Alex", "Brooke", "Carlos", "Dana", "Elena", "Felix", "Gabriela", "Hugo", "Isla", "Jamal",
    "Katya", "Leo", "Mira", "Nikhil", "Owen", "Paula", "Quinn", "Rosa", "Sam", "Tessa",
    "Umar", "Vera", "Will", "Ximena", "Yara", "Zane", "Ines", "Mateo", "Chloe", "Ravi",
]
LAST_NAMES = [
    "Adams", "Baker", "Castro", "Dubois", "Evans", "Farah", "Garcia", "Hughes", "Ivanov", "Jensen",
    "Khan", "Lopez", "Meyer", "Novak", "Ortiz", "Patel", "Quist", "Rossi", "Silva", "Tran",
    "Usman", "Vega", "Wong", "Young", "Zhou", "Becker", "Moreau", "Nair", "Olsen", "Reid",
]
AREA_CODES = ["212", "310", "415", "646", "718", "917", "305", "202"]


class People:
    """Hands out unique fake contact details (so duplicates only exist where we plant them)."""

    def __init__(self, rng: random.Random):
        self.rng = rng
        self.count = 0
        # 10-digit keys already taken. The number we plant as a duplicate is reserved up front.
        self.taken = {"4155550143"}

    def new(self):
        self.count += 1
        first, last = self.rng.choice(FIRST_NAMES), self.rng.choice(LAST_NAMES)
        email = f"{first}.{last}{self.count}@example.com".lower()
        while True:
            area, line = self.rng.choice(AREA_CODES), f"01{self.rng.randint(0, 99):02d}"
            if area + "555" + line not in self.taken:
                self.taken.add(area + "555" + line)
                break
        return f"{first} {last}", email, f"+1 {area} 555 {line}"


def snap(moment: datetime) -> datetime:
    """Round to the nearest half hour (calls start on :00 or :30)."""
    minutes = int(round((moment.hour * 60 + moment.minute) / 30.0)) * 30
    return datetime.combine(moment.date(), time(0, 0)) + timedelta(minutes=minutes)


def daytime_slots(start: datetime, end: datetime) -> List[datetime]:
    """Half-hour call slots between 10:00 and 17:30 that fall inside (start, end].

    The planted calls use these so that, whatever time of day you generate the data,
    no demo call lands at 03:30 in the morning.
    """
    slots: List[datetime] = []
    day = start.date()
    while day <= end.date():
        for hour in range(10, 18):
            for minute in (0, 30):
                slot = datetime.combine(day, time(hour, minute))
                if start < slot <= end:
                    slots.append(slot)
        day += timedelta(days=1)
    return slots


def pick(slots: List[datetime], fractions) -> List[datetime]:
    """Choose slots spread along the list, e.g. fractions (0.1, 0.5, 0.9)."""
    return [slots[min(len(slots) - 1, int(len(slots) * f))] for f in fractions]


def simulate(created: datetime, source: str, now: datetime, rng: random.Random,
             people: People, broken_since: datetime, owed: Dict[str, float]) -> Dict:
    name, email, phone = people.new()
    row = dict(created=created, created_text=None, name=name, email=email, phone=phone,
               source=source, status="New", owner="", first=None, last=None, call=None, value=None)

    delay_hours = rng.choice([0.2, 0.4, 0.7, 1, 1.5, 2, 3, 4, 6, 9]) + rng.random() * 0.3
    first = created + timedelta(hours=delay_hours)
    if first > now:  # too fresh to have been contacted yet
        row["owner"] = rng.choice(SETTERS + [""])
        return row

    row["owner"] = rng.choice(SETTERS)
    row["first"] = first
    chance_to_book = SOURCES[source][1]
    if source == BROKEN_SOURCE and created >= broken_since:
        chance_to_book = 0.0  # the broken booking link
    age_days = (now - created).total_seconds() / 86400

    # Instead of an independent coin flip per lead (which makes a 60-lead week swing wildly),
    # each source "earns" a fraction of a booking per lead and books when it has earned a
    # whole one. Weekly rates then sit close to the source's true rate, with a little jitter.
    books = False
    if chance_to_book:
        owed[source] += chance_to_book + rng.uniform(-0.12, 0.12)
        if owed[source] >= 1.0:
            owed[source] -= 1.0
            books = True

    if books:
        call_day = first.date() + timedelta(days=rng.randint(1, 4))
        call_at = datetime.combine(call_day, time(rng.randint(10, 17), rng.choice((0, 30))))
        row["call"] = call_at
        if call_at <= now:
            if rng.random() < 0.38:
                row["status"], row["value"] = "Won", rng.choice(DEAL_VALUES)
            else:
                row["status"] = "Lost"
            row["last"] = min(now, call_at + timedelta(hours=rng.randint(1, 3)))
        else:
            row["status"] = "Call booked"
            row["last"] = min(now, first + timedelta(hours=rng.randint(0, 3)))
    elif age_days >= 3:
        row["status"] = "Lost"
        row["last"] = min(now, first + timedelta(hours=rng.randint(2, 48)))
    else:
        row["status"] = "Contacted"
        row["last"] = min(now, first + timedelta(hours=rng.randint(0, 6)))
    return row


def planted(now: datetime) -> List[Dict]:
    """Hand-written rows: one for every problem the checks are supposed to find."""
    h = lambda x: timedelta(hours=x)  # noqa: E731
    d = lambda x: timedelta(days=x)   # noqa: E731
    rows: List[Dict] = []
    counter = [0]

    def add(name, source, status, owner, created, *, first=None, last=None, call=None,
            value=None, email=None, phone=None, created_text=None):
        counter[0] += 1
        first_name, last_name = name.split(" ", 1)
        rows.append(dict(
            created=created, created_text=created_text, name=name,
            email=email or f"{first_name}.{last_name}.p{counter[0]}@example.com".lower().replace(" ", "").replace("'", ""),
            phone=phone or f"+1 800 555 01{counter[0]:02d}",  # 800 is not used by the random leads
            source=source, status=status, owner=owner, first=first, last=last, call=call, value=value,
        ))

    # 1) new leads nobody has contacted (stuck)
    add("Maya Collins", "Instagram", "New", "", now - h(76))
    add("Jordan Reyes", "Webinar", "New", "Marco", now - h(52))
    add("Hannah Weiss", "Referral", "New", "Anna", now - h(31))
    add("Noah Fischer", "Instagram", "New", "", now - h(27))

    # 2) contacted, then nothing (going cold)
    c = now - d(6)
    add("Liam O'Brien", "YouTube", "Contacted", "Priya", c, first=c + h(2), last=now - h(99))
    c = now - d(5)
    add("Sara Lindqvist", "Instagram", "Contacted", "Anna", c, first=c + h(1), last=now - h(80))
    c = now - h(132)
    add("Victor Alves", "Webinar", "Contacted", "Marco", c, first=c + h(3), last=now - h(120))

    # 3) call time passed, status never updated (during working hours, 3 to 30 hours ago)
    first_missed, second_missed = pick(daytime_slots(now - h(30), now - h(3)), (0.2, 0.75))
    c = now - d(4)
    add("Amelia Grant", "Webinar", "Call booked", "Priya", c, first=c + h(1), last=c + h(1), call=first_missed)
    c = now - d(3)
    add("Kenji Mori", "Referral", "Call booked", "Tomas", c, first=c + h(1), last=c + h(1), call=second_missed)

    # 3b) calls coming up in the next 24h, during working hours (so the "calls" section has content)
    upcoming = pick(daytime_slots(now + h(1), now + h(23)), (0.1, 0.5, 0.9))
    for (name, source, owner, created_hours_ago), call_at in zip(
        (
            ("Chloe Martin", "Referral", "Priya", 30),
            ("Rahul Verma", "Webinar", "Marco", 34),
            ("Elise Fournier", "Instagram", "Anna", 20),
        ),
        upcoming,
    ):
        c = now - h(created_hours_ago)
        add(name, source, "Call booked", owner, c, first=c + h(1), last=c + h(1), call=call_at)

    # 4) duplicates: same e-mail (different capitalisation), same phone (different formatting)
    c = now - d(6)
    add("Olivia Bennett", "Instagram", "Lost", "Anna", c, first=c + h(1), last=c + h(30),
        email="olivia.bennett@example.com")
    c = now - d(2)
    add("Olivia Bennett", "Facebook Ads", "Contacted", "Marco", c, first=c + h(2), last=c + h(2),
        email="Olivia.Bennett@Example.com")
    c = now - d(8)
    add("Daniel Okafor", "Webinar", "Lost", "Tomas", c, first=c + h(1), last=c + h(20),
        email="daniel.okafor@example.com", phone="+1 415 555 0143")
    c = now - d(1)
    add("Dan Okafor", "Referral", "Contacted", "Priya", c, first=c + h(3), last=c + h(3),
        email="dokafor@example.org", phone="(415) 555-0143")

    # 5) missing source (one of them even closed a deal we can't attribute)
    c = now - d(7)
    add("Sofia Marin", "", "Lost", "Anna", c, first=c + h(2), last=c + h(26))
    c = now - d(3)
    add("Henry Cole", "n/a", "Contacted", "Marco", c, first=c + h(1), last=c + h(7))
    c = now - d(10)
    call = snap(c + d(3) + h(2))
    add("Mei Tanaka", "", "Won", "Priya", c, first=c + h(1), last=call + h(2), call=call, value=3000)

    # 6) a status that is not in the list
    c = now - d(4)
    add("Lucas Ferreira", "YouTube", "Qualified", "Priya", c, first=c + h(2), last=c + h(2))

    # 7) a date nobody can parse
    c = now - d(1) - h(3)
    add("Grace Liu", "Instagram", "Contacted", "Tomas", c, first=c + h(1), last=c + h(1), created_text="last Friday")

    # 8) rows that contradict themselves
    c = now - d(12)
    call = snap(c + d(2))
    add("Ethan Walker", "Webinar", "Won", "Anna", c, first=c + h(1), last=call + h(2), call=call, value=None)
    c = now - d(3)
    add("Nora Haddad", "Referral", "Call booked", "Marco", c, first=c + h(1), last=c + h(1), call=None)
    return rows


def generate(now: datetime, seed: int, days: int) -> List[Dict]:
    rng = random.Random(seed)
    people = People(rng)
    broken_since = now - timedelta(days=BROKEN_DAYS_AGO)
    names = list(SOURCES)
    weights = [SOURCES[n][0] for n in names]
    owed = {n: rng.random() for n in names}

    rows: List[Dict] = []
    for ago in range(days, -1, -1):
        day = (now - timedelta(days=ago)).date()
        count = rng.randint(7, 10) if day.weekday() < 5 else rng.randint(3, 5)
        for _ in range(count):
            created = datetime.combine(day, time(rng.randint(8, 21), rng.randint(0, 59)))
            if created > now - timedelta(minutes=30):
                continue
            rows.append(simulate(created, rng.choices(names, weights)[0], now, rng, people, broken_since, owed))
    rows += planted(now)
    rows.sort(key=lambda r: r["created"])
    return rows


def fmt(moment: Optional[datetime]) -> str:
    return moment.strftime("%Y-%m-%d %H:%M") if moment else ""


def write_csv(rows: List[Dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for index, row in enumerate(rows, start=1):
            writer.writerow({
                "lead_id": f"L-{index:04d}",
                "created_at": row["created_text"] or fmt(row["created"]),
                "name": row["name"],
                "email": row["email"],
                "phone": row["phone"],
                "source": row["source"],
                "status": row["status"],
                "owner": row["owner"],
                "first_contact_at": fmt(row["first"]),
                "last_contact_at": fmt(row["last"]),
                "call_at": fmt(row["call"]),
                "deal_value": "" if row["value"] is None else f"{row['value']:.0f}",
            })


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--now", help='anchor moment, e.g. "2026-11-12 09:00" (default: current time)')
    parser.add_argument("--out", default="data/leads_demo.csv")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--days", type=int, default=30, help="days of history to generate")
    parser.add_argument("--utc-offset", type=float, default=3.0, help="timezone of 'now' when --now is not given")
    args = parser.parse_args()

    if args.now:
        now = datetime.strptime(args.now, "%Y-%m-%d %H:%M")
    else:
        now = (datetime.now(timezone.utc) + timedelta(hours=args.utc_offset)).replace(
            tzinfo=None, second=0, microsecond=0
        )
    rows = generate(now, args.seed, args.days)
    write_csv(rows, Path(args.out))
    print(f"Wrote {len(rows)} synthetic leads to {args.out} (anchored at {now:%Y-%m-%d %H:%M}).")


if __name__ == "__main__":
    main()
