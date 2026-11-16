"""Unit tests. Run from the project folder with:  python -m unittest discover -v

Every expected number below was worked out by hand first (see the comments), so the tests
check the code against arithmetic, not against itself. Nothing here touches the network:
Telegram and Google are replaced by tiny fakes.
"""
import contextlib
import csv
import importlib.util
import io
import json
import os
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from leadwatch import checks, cli, loader, metrics, report, telegram
from leadwatch.config import Config, load_env_file
from leadwatch.loader import DataSourceError, LoadResult, parse_csv, parse_datetime, parse_money
from leadwatch.models import Lead
from leadwatch.textfmt import delta, fmt_age, fmt_when

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 12, 9, 0)  # a Thursday, 09:00
CFG = Config()


def make_lead(row=2, **overrides) -> Lead:
    values = dict(
        lead_id=f"L-{row}",
        created_at=NOW - timedelta(days=1),
        name=f"Lead {row}",
        source="Instagram",
        status="Contacted",
    )
    values.update(overrides)
    return Lead(row=row, **values)


def spread(n, booked, source, oldest_days, newest_days, start_row=2):
    """n leads created evenly between `oldest_days` and `newest_days` ago; the first `booked` have a call."""
    leads = []
    for i in range(n):
        days_ago = newest_days + (oldest_days - newest_days) * (i + 0.5) / n
        created = NOW - timedelta(days=days_ago)
        leads.append(
            make_lead(
                start_row + i,
                created_at=created,
                source=source,
                status="Lost",
                call_at=(created + timedelta(days=1)) if i < booked else None,
            )
        )
    return leads


class FakeResponse:
    def __init__(self, body: bytes):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# --------------------------------------------------------------------------- loading

class ParsingTests(unittest.TestCase):
    def test_datetime_formats(self):
        self.assertEqual(parse_datetime("2026-11-12 09:30"), datetime(2026, 11, 12, 9, 30))
        self.assertEqual(parse_datetime("12.11.2026 09:30"), datetime(2026, 11, 12, 9, 30))  # dotted = day first
        self.assertEqual(parse_datetime("11/12/2026 9:30:15"), datetime(2026, 11, 12, 9, 30, 15))  # slash = month first
        self.assertEqual(parse_datetime("03/04/2026"), datetime(2026, 3, 4))  # ambiguous: month/day wins
        self.assertEqual(parse_datetime("25/12/2026"), datetime(2026, 12, 25))  # cannot be month/day
        self.assertIsNone(parse_datetime("  "))
        with self.assertRaises(ValueError):
            parse_datetime("last Friday")

    def test_money(self):
        self.assertEqual(parse_money("$2,500"), 2500.0)
        self.assertEqual(parse_money("1 500"), 1500.0)
        self.assertEqual(parse_money("15,5"), 15.5)
        self.assertEqual(parse_money("1,234.50"), 1234.5)
        self.assertIsNone(parse_money(""))
        with self.assertRaises(ValueError):
            parse_money("n/a")

    def test_google_form_headers_and_status_aliases(self):
        text = (
            "Timestamp,Name,Email Address,Phone number,Status\n"
            "11/12/2026 9:05:12,Ann Lee,a@x.com,555,call_booked\n"
        )
        lead = parse_csv(text).leads[0]
        self.assertEqual(lead.created_at, datetime(2026, 11, 12, 9, 5, 12))
        self.assertEqual(lead.email, "a@x.com")
        self.assertEqual(lead.status, "Call booked")
        self.assertEqual(lead.row, 2)

    def test_bad_cells_become_issues_not_crashes(self):
        text = "created_at,name,status,deal_value,call_at\nlast Friday,Ann,Won,n/a,soon\n"
        result = parse_csv(text)
        self.assertEqual(len(result.leads), 1)
        self.assertIsNone(result.leads[0].created_at)
        self.assertEqual(sorted(i.field for i in result.issues), ["call_at", "created_at", "deal_value"])

    def test_a_wrong_sheet_is_an_error_not_silence(self):
        with self.assertRaises(DataSourceError):
            parse_csv("<!DOCTYPE html><html><body>Sign in</body></html>")
        with self.assertRaises(DataSourceError):
            parse_csv("name,email\nAnn,a@x.com\n")  # no created_at / status column
        with self.assertRaises(DataSourceError):
            parse_csv("")

    def test_spelling_variants_of_a_source_are_merged(self):
        text = (
            "created_at,status,source\n"
            "2026-11-01 10:00,New,Instagram\n"
            "2026-11-01 11:00,New,instagram\n"
            "2026-11-01 12:00,New, INSTAGRAM \n"
        )
        self.assertEqual({l.source for l in parse_csv(text).leads}, {"Instagram"})

    def test_blank_rows_are_skipped_and_ids_generated(self):
        text = "created_at,status\n2026-11-01 10:00,New\n,\n2026-11-02 10:00,Lost\n"
        leads = parse_csv(text).leads
        self.assertEqual([l.row for l in leads], [2, 4])
        self.assertEqual([l.lead_id for l in leads], ["row-2", "row-4"])

    def test_reading_files_and_urls(self):
        with self.assertRaises(DataSourceError):
            loader.read_source("definitely/not/here.csv")

        ok = lambda request, timeout=None: FakeResponse(b"created_at,status\n2026-11-01 10:00,New\n")  # noqa: E731
        self.assertEqual(len(loader.load_leads("https://example.com/sheet.csv", opener=ok).leads), 1)

        def not_found(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

        with self.assertRaises(DataSourceError) as ctx:
            loader.load_leads("https://secret.example/sheet?token=abc", opener=not_found)
        self.assertIn("404", str(ctx.exception))
        self.assertNotIn("secret", str(ctx.exception))  # the link never leaks into a message


# --------------------------------------------------------------------------- speed to lead

class StuckLeadTests(unittest.TestCase):
    def alerts(self, *leads):
        return checks.check_stuck_new(list(leads), NOW, CFG)

    def test_thresholds_are_strict_boundaries(self):
        self.assertEqual(self.alerts(make_lead(2, status="New", created_at=NOW - timedelta(hours=24))), [])
        [alert] = self.alerts(make_lead(3, status="New", created_at=NOW - timedelta(hours=24, minutes=1)))
        self.assertEqual(alert.severity, "warning")
        [alert] = self.alerts(make_lead(4, status="New", created_at=NOW - timedelta(hours=48, minutes=1)))
        self.assertEqual(alert.severity, "critical")

    def test_only_new_leads_with_a_date_count(self):
        leads = [
            make_lead(2, status="Contacted", created_at=NOW - timedelta(days=5)),
            make_lead(3, status="New", created_at=None),
        ]
        self.assertEqual(self.alerts(*leads), [])

    def test_critical_and_warning_are_separate_and_oldest_first(self):
        leads = [
            make_lead(2, status="New", created_at=NOW - timedelta(hours=30)),
            make_lead(3, status="New", created_at=NOW - timedelta(hours=100)),
            make_lead(4, status="New", created_at=NOW - timedelta(hours=60)),
        ]
        alerts = self.alerts(*leads)
        self.assertEqual([a.severity for a in alerts], ["critical", "warning"])
        self.assertEqual(alerts[0].lead_ids, ["L-3", "L-4"])  # 100h before 60h
        self.assertIn("4d 4h", alerts[0].items[0])  # 100h = 4 days + 4 hours
        self.assertIn("unassigned", alerts[0].items[0])
        self.assertIn("2 of these have no owner", alerts[0].hint)


class FollowUpAndCallTests(unittest.TestCase):
    def test_cold_follow_up_uses_the_last_touch(self):
        cold = make_lead(2, last_contact_at=NOW - timedelta(hours=73))
        warm = make_lead(3, last_contact_at=NOW - timedelta(hours=71))
        [alert] = checks.check_follow_ups([cold, warm], NOW, CFG)
        self.assertEqual(alert.lead_ids, ["L-2"])

    def test_follow_up_falls_back_to_creation_time(self):
        never_touched = make_lead(2, created_at=NOW - timedelta(days=4))
        self.assertEqual(len(checks.check_follow_ups([never_touched], NOW, CFG)), 1)

    def test_overdue_call_has_two_hours_of_grace(self):
        fine = make_lead(2, status="Call booked", call_at=NOW - timedelta(hours=2))
        late = make_lead(3, status="Call booked", call_at=NOW - timedelta(hours=2, minutes=1))
        [alert] = checks.check_overdue_calls([fine, late], NOW, CFG)
        self.assertEqual(alert.lead_ids, ["L-3"])

    def test_upcoming_calls_window_and_order(self):
        leads = [
            make_lead(2, status="Call booked", call_at=NOW + timedelta(hours=23)),
            make_lead(3, status="Call booked", call_at=NOW + timedelta(hours=3)),
            make_lead(4, status="Call booked", call_at=NOW + timedelta(hours=25)),  # outside the window
            make_lead(5, status="Won", call_at=NOW + timedelta(hours=1)),  # not 'Call booked'
        ]
        [alert] = checks.check_upcoming_calls(leads, NOW, CFG)
        self.assertEqual(alert.lead_ids, ["L-3", "L-2"])
        self.assertTrue(alert.items[0].startswith("today 12:00"))


# --------------------------------------------------------------------------- funnel

class FunnelTests(unittest.TestCase):
    def summary(self, leads):
        return metrics.compute_summary(leads, NOW, CFG)

    def build(self, prev, cur):
        """prev / cur: {source: (leads, booked)} for the previous and the current conversion window."""
        leads, row = [], 2
        for window, (oldest, newest) in ((prev, (15.5, 9.5)), (cur, (8.5, 2.5))):
            for source, (n, booked) in window.items():
                leads += spread(n, booked, source, oldest, newest, start_row=row)
                row += n
        return leads

    def test_hand_calculated_funnel(self):
        # 4 leads, created 3.6 / 4.9 / 6.1 / 7.4 days ago, the first two booked a call.
        s = self.summary(spread(4, 2, "Instagram", oldest_days=8, newest_days=3))
        self.assertEqual((s.funnel_cur.leads, s.funnel_cur.booked), (4, 2))
        self.assertEqual(s.funnel_cur.booking_rate, 0.5)
        self.assertEqual(s.funnel_prev.leads, 0)
        self.assertIsNone(s.funnel_prev.booking_rate)
        # The volume windows are different (plain last 7 days): 3.6, 4.9, 6.1 are inside, 7.4 is not.
        self.assertEqual((s.volume_cur, s.volume_prev), (3, 1))

    def test_newest_leads_count_for_volume_but_not_for_conversion(self):
        s = self.summary([make_lead(2, created_at=NOW - timedelta(days=1), status="New")])
        self.assertEqual(s.volume_cur, 1)
        self.assertEqual(s.funnel_cur.leads, 0)  # too young to judge

    def test_a_dead_source_is_found_and_flagged_critical(self):
        leads = self.build(prev={"A": (10, 4), "B": (10, 4)}, cur={"A": (10, 0), "B": (10, 4)})
        [alert] = checks.check_conversion(self.summary(leads), CFG)
        # overall 8/20 = 40% before, 4/20 = 20% now -> down (40-20)/40 = 50%.
        # source A: expected 0.4 * 10 = 4 bookings, got 0; chance of that by luck = 0.6^10 = 0.6%.
        self.assertEqual(alert.severity, "critical")
        self.assertIn("Booking rate down 50%", alert.title)
        self.assertTrue(any(item.startswith("A: 0 of 10") for item in alert.items))
        self.assertTrue(any("0.6%" in item for item in alert.items))
        self.assertIn("Zero bookings", alert.hint)

    def test_small_samples_do_not_cry_wolf(self):
        leads = self.build(prev={"A": (5, 3)}, cur={"A": (5, 0)})  # 10 leads: below the minimum of 15
        self.assertEqual(checks.check_conversion(self.summary(leads), CFG), [])

    def test_a_small_drop_is_ignored(self):
        leads = self.build(prev={"A": (20, 7)}, cur={"A": (20, 6)})  # 35% -> 30% is only down 14%
        self.assertEqual(checks.check_conversion(self.summary(leads), CFG), [])

    def test_a_drop_without_a_culprit_is_a_warning(self):
        # 50% -> 35% overall (down 30%); each source alone is within what luck explains
        # (P(<=7 of 20 | p=0.5) = 13%), so no single source is blamed.
        leads = self.build(prev={"A": (20, 10), "B": (20, 10)}, cur={"A": (20, 7), "B": (20, 7)})
        [alert] = checks.check_conversion(self.summary(leads), CFG)
        self.assertEqual(alert.severity, "warning")
        self.assertIn("No single source", alert.hint)

    def test_volume_drop(self):
        leads = spread(30, 0, "A", 13.5, 7.5, start_row=2) + spread(12, 0, "A", 6.5, 0.5, start_row=40)
        s = self.summary(leads)
        self.assertEqual((s.volume_prev, s.volume_cur), (30, 12))
        [alert] = checks.check_volume(s, CFG)
        self.assertIn("Lead volume down 60%", alert.title)  # (30 - 12) / 30
        self.assertEqual(alert.severity, "critical")  # 50% or more
        self.assertEqual(alert.items, ["A: 12 vs 30 the week before"])

    def test_volume_critical_threshold_comes_from_config(self):
        leads = spread(30, 0, "A", 13.5, 7.5, start_row=2) + spread(18, 0, "A", 6.5, 0.5, start_row=40)
        s = self.summary(leads)
        [alert] = checks.check_volume(s, CFG)
        self.assertEqual(alert.severity, "warning")  # (30 - 18) / 30 = 40%, below the 50% default
        [alert] = checks.check_volume(s, Config(critical_volume_drop_threshold=0.35))
        self.assertEqual(alert.severity, "critical")  # the same 40% drop passes a 35% limit

    def test_binomial_matches_hand_calculation(self):
        self.assertAlmostEqual(metrics.binom_cdf(0, 3, 0.5), 0.125)  # (1/2)^3
        self.assertAlmostEqual(metrics.binom_cdf(1, 3, 0.5), 0.5)  # (1 + 3) / 8
        self.assertAlmostEqual(metrics.binom_cdf(0, 10, 0.4), 0.6 ** 10)
        self.assertEqual(metrics.binom_cdf(10, 10, 0.4), 1.0)

    def test_speed_to_lead_counts_late_and_missing_contacts(self):
        base = NOW - timedelta(days=3)
        leads = [
            make_lead(2, created_at=base, first_contact_at=base + timedelta(hours=2)),  # in time
            make_lead(3, created_at=base, first_contact_at=base + timedelta(hours=30)),  # too late
            make_lead(4, created_at=base, status="New"),  # never contacted
            make_lead(5, created_at=NOW - timedelta(hours=5), status="New"),  # too young to judge
        ]
        s = self.summary(leads)
        self.assertEqual((s.sla_responded, s.sla_eligible), (1, 3))

    def test_revenue_counts_won_deals_by_call_date(self):
        recent = make_lead(2, status="Won", call_at=NOW - timedelta(days=2), deal_value=3000.0)
        old = make_lead(3, status="Won", call_at=NOW - timedelta(days=9), deal_value=5000.0)
        s = self.summary([recent, old])
        self.assertEqual((s.won_7d, s.revenue_7d), (1, 3000.0))


# --------------------------------------------------------------------------- data quality

class DataQualityTests(unittest.TestCase):
    def run_check(self, *leads, issues=None):
        return checks.check_data_quality(LoadResult(leads=list(leads), issues=issues or []), NOW, CFG)

    def test_clean_data_raises_nothing(self):
        self.assertEqual(self.run_check(make_lead(2, status="Lost")), [])

    def test_duplicates_ignore_email_case_and_phone_formatting(self):
        leads = [
            make_lead(2, name="Olivia", email="olivia@x.com"),
            make_lead(3, name="Olivia", email="Olivia@X.com"),
            make_lead(4, name="Daniel", email="d1@x.com", phone="+1 415 555 0143"),
            make_lead(5, name="Dan", email="d2@x.com", phone="(415) 555-0143"),
            make_lead(6, name="Solo", email="solo@x.com", phone="12"),  # too short to compare
        ]
        [alert] = [a for a in self.run_check(*leads) if "duplicate" in a.title]
        self.assertEqual(len(alert.items), 2)
        self.assertEqual(sorted(alert.lead_ids), ["L-2", "L-3", "L-4", "L-5"])

    def test_missing_source_unknown_status_and_contradictions(self):
        leads = [
            make_lead(2, source="n/a", status="New"),
            make_lead(3, status="Qualified"),
            make_lead(4, status="Won", first_contact_at=NOW - timedelta(days=3), deal_value=None),
            make_lead(5, status="Call booked", first_contact_at=NOW - timedelta(days=1), call_at=None),
            make_lead(6, status="Lost", created_at=NOW - timedelta(days=2), call_at=NOW - timedelta(days=3)),
        ]
        alerts = self.run_check(*leads)
        find = lambda text: next(a for a in alerts if text in a.title)  # noqa: E731
        self.assertEqual(find("no source").lead_ids, ["L-2"])
        self.assertEqual(find("unknown status").lead_ids, ["L-3"])
        reasons = " | ".join(find("contradict").items)
        self.assertIn("Won but there is no deal value", reasons)
        self.assertIn("Call booked but there is no call time", reasons)
        self.assertIn("call time is before the lead was created", reasons)

    def test_future_dates_hint_at_a_timezone_problem(self):
        [alert] = [a for a in self.run_check(make_lead(2, created_at=NOW + timedelta(hours=3))) if "future" in a.title]
        self.assertIn("LEADWATCH_UTC_OFFSET", alert.hint)
        within_tolerance = self.run_check(make_lead(3, created_at=NOW + timedelta(minutes=30)))
        self.assertFalse(any("future" in a.title for a in within_tolerance))

    def test_unreadable_cells_are_reported(self):
        issue = loader.RowIssue(row=7, label="Grace", field="created_at", value="last Friday")
        [alert] = self.run_check(issues=[issue])
        self.assertIn("can't be read", alert.title)
        self.assertIn("row 7", alert.items[0])


# --------------------------------------------------------------------------- report, text, telegram, config

class TextFormatTests(unittest.TestCase):
    def test_ages(self):
        self.assertEqual(fmt_age(0.5), "30m")
        self.assertEqual(fmt_age(31.4), "31h")
        self.assertEqual(fmt_age(52), "2d 4h")
        self.assertEqual(fmt_age(72), "3d")

    def test_when(self):
        self.assertEqual(fmt_when(datetime(2026, 11, 12, 12, 0), NOW), "today 12:00")
        self.assertEqual(fmt_when(datetime(2026, 11, 13, 8, 30), NOW), "tomorrow 08:30")
        self.assertEqual(fmt_when(datetime(2026, 11, 11, 17, 0), NOW), "yesterday 17:00")
        self.assertEqual(fmt_when(datetime(2026, 11, 20, 11, 0), NOW), "Fri 20 Nov 11:00")

    def test_delta(self):
        self.assertEqual(delta(75, 100), " ▼25%")
        self.assertEqual(delta(110, 100), " ▲10%")
        self.assertEqual(delta(5, 0), "")
        self.assertEqual(delta(100, 100), "")


class ReportTests(unittest.TestCase):
    def test_split_keeps_lines_and_respects_the_limit(self):
        text = "\n".join(f"line {i} " + "x" * 30 for i in range(100))
        parts = report.split_message(text, limit=500)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= 500 for p in parts))
        self.assertEqual("\n".join(parts), text)

    def test_a_single_huge_line_is_hard_split(self):
        self.assertEqual([len(p) for p in report.split_message("y" * 1250, limit=500)], [500, 500, 250])

    def test_digest_says_all_clear_when_nothing_is_wrong(self):
        result = LoadResult(leads=[make_lead(2, status="Lost")])
        summary = metrics.compute_summary(result.leads, NOW, CFG)
        alerts = checks.run_all_checks(result, summary, NOW, CFG)
        self.assertEqual(alerts, [])
        self.assertIn("Nothing needs action", report.build_digest(summary, alerts, NOW, CFG, "test.csv"))

    def test_the_sheet_link_is_never_printed(self):
        secret = "https://docs.google.com/spreadsheets/d/e/SECRET/pub?output=csv"
        self.assertEqual(report.source_label(secret), "Google Sheet (published CSV)")
        self.assertEqual(report.source_label("data/leads_demo.csv"), "leads_demo.csv")


class TelegramTests(unittest.TestCase):
    TOKEN = "123456:SECRET-TOKEN"

    def test_send_message_builds_the_right_request(self):
        seen = {}

        def opener(request, timeout=None):
            seen["url"] = request.full_url
            seen["payload"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse(b'{"ok": true, "result": {}}')

        telegram.send_message(self.TOKEN, "42", "hello", opener=opener)
        self.assertEqual(seen["url"], f"https://api.telegram.org/bot{self.TOKEN}/sendMessage")
        self.assertEqual(seen["payload"]["chat_id"], "42")
        self.assertEqual(seen["payload"]["text"], "hello")

    def test_errors_never_contain_the_token(self):
        def http_401(request, timeout=None):
            raise urllib.error.HTTPError(
                request.full_url, 401, "Unauthorized", {}, io.BytesIO(b'{"ok": false, "description": "Unauthorized"}')
            )

        def offline(request, timeout=None):
            raise urllib.error.URLError("no route to host")

        for opener, expected in ((http_401, "HTTP 401"), (offline, "Could not reach")):
            with self.assertRaises(telegram.TelegramError) as ctx:
                telegram.send_message(self.TOKEN, "42", "x", opener=opener)
            self.assertIn(expected, str(ctx.exception))
            self.assertNotIn("SECRET", str(ctx.exception))
            self.assertTrue(ctx.exception.__suppress_context__)  # the original error (with the URL) is hidden

    def test_a_long_digest_goes_out_as_several_messages(self):
        sent = []

        def opener(request, timeout=None):
            sent.append(json.loads(request.data.decode("utf-8"))["text"])
            return FakeResponse(b'{"ok": true, "result": {}}')

        text = "\n".join("z" * 100 for _ in range(100))  # about 10,000 characters
        with mock.patch("leadwatch.telegram.time.sleep"):
            count = telegram.send_digest(self.TOKEN, "42", text, opener=opener)
        self.assertEqual(count, len(sent))
        self.assertGreater(count, 1)
        self.assertTrue(all(len(part) <= 4000 for part in sent))

    def test_find_chats_lists_each_chat_once(self):
        body = json.dumps({"ok": True, "result": [
            {"update_id": 1, "message": {"chat": {"id": 555, "first_name": "Test", "type": "private"}}},
            {"update_id": 2, "message": {"chat": {"id": 555, "first_name": "Test", "type": "private"}}},
            {"update_id": 3, "message": {"chat": {"id": -100, "title": "Ops", "type": "group"}}},
        ]}).encode()
        chats = telegram.find_chats(self.TOKEN, opener=lambda request, timeout=None: FakeResponse(body))
        self.assertEqual({c["id"] for c in chats}, {"555", "-100"})


class ConfigTests(unittest.TestCase):
    def test_env_file_never_overrides_the_real_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text('# a comment\nWATCH_TEST_A=from_file\nWATCH_TEST_B="quoted"\n', encoding="utf-8")
            with mock.patch.dict(os.environ, {"WATCH_TEST_A": "from_env"}):
                load_env_file(str(path))
                self.assertEqual(os.environ["WATCH_TEST_A"], "from_env")
                self.assertEqual(os.environ["WATCH_TEST_B"], "quoted")

    def test_a_non_numeric_setting_is_rejected(self):
        with mock.patch.dict(os.environ, {"LEADWATCH_UTC_OFFSET": "three"}):
            with self.assertRaises(ValueError):
                Config.from_env()


# --------------------------------------------------------------------------- end to end on the demo story

def load_generator():
    spec = importlib.util.spec_from_file_location("make_demo_data", ROOT / "scripts" / "make_demo_data.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_cli(*args, env=None):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), mock.patch.dict(os.environ, env or {}):
        code = cli.main(list(args))
    return code, out.getvalue(), err.getvalue()


class CliTestCase(unittest.TestCase):
    def setUp(self):
        guard = mock.patch("urllib.request.urlopen", side_effect=AssertionError("tests must not use the network"))
        guard.start()
        self.addCleanup(guard.stop)


class DemoStoryTests(CliTestCase):
    DEMO_NOW = datetime(2026, 11, 12, 9, 0)
    SEND_ENV = {"TELEGRAM_BOT_TOKEN": "test-token", "TELEGRAM_CHAT_ID": "1"}

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.csv_path = Path(cls.tmp.name) / "leads.csv"
        generator = load_generator()
        generator.write_csv(generator.generate(cls.DEMO_NOW, seed=7, days=30), cls.csv_path)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def digest(self):
        code, out, err = run_cli("--source", str(self.csv_path), "--now", "2026-11-12 09:00")
        self.assertEqual(code, 0, err)
        return out

    def test_every_planted_problem_is_found(self):
        text = self.digest()
        action = text.split("🔴 ACTION NEEDED")[1].split("🟡")[0]
        self.assertIn("Booking rate down", action)
        self.assertIn("Facebook Ads: 0 of", action)
        for name in ("Maya Collins", "Jordan Reyes"):  # stuck for more than 48h
            self.assertIn(name, action)
        for name in (
            "Hannah Weiss", "Noah Fischer",  # stuck for more than 24h
            "Amelia Grant", "Kenji Mori",  # call in the past, status never updated
            "Victor Alves", "Liam O'Brien", "Sara Lindqvist",  # going cold
            "Chloe Martin", "Rahul Verma", "Elise Fournier",  # calls in the next 24h
            "Olivia Bennett", "Daniel Okafor / Dan Okafor",  # duplicates
            "Lucas Ferreira", "Grace Liu", "Ethan Walker", "Nora Haddad", "Sofia Marin",  # data problems
        ):
            self.assertIn(name, text)

    def test_the_digest_fits_in_one_telegram_message(self):
        self.assertLess(len(self.digest()), 4000)

    def test_numbers_match_an_independent_recalculation(self):
        """Recompute the key numbers straight from the CSV with plain code and compare."""
        with self.csv_path.open(encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))

        def when(text):
            return datetime.strptime(text, "%Y-%m-%d %H:%M") if text[:2] == "20" else None

        def booked(row):
            return bool(row["call_at"]) or row["status"] in ("Call booked", "Won")

        def window(first_days_ago, last_days_ago):
            lo, hi = self.DEMO_NOW - timedelta(days=first_days_ago), self.DEMO_NOW - timedelta(days=last_days_ago)
            return [r for r in rows if when(r["created_at"]) and lo < when(r["created_at"]) <= hi]

        summary = metrics.compute_summary(loader.load_leads(str(self.csv_path)).leads, self.DEMO_NOW, Config())
        for funnel, rows_in_window in ((summary.funnel_cur, window(9, 2)), (summary.funnel_prev, window(16, 9))):
            self.assertEqual(funnel.leads, len(rows_in_window))
            self.assertEqual(funnel.booked, sum(booked(r) for r in rows_in_window))
        facebook = [r for r in window(9, 2) if r["source"] == "Facebook Ads"]
        self.assertEqual(summary.sources_cur["Facebook Ads"].leads, len(facebook))
        self.assertEqual(sum(booked(r) for r in facebook), 0)  # the broken link: not a single booking

        # leads created in the last 7 days
        self.assertEqual(summary.volume_cur, len(window(7, 0)))

        # new leads past the 24h SLA, found with a plain filter
        stuck_names = {
            r["name"] for r in rows
            if r["status"] == "New" and when(r["created_at"]) and (self.DEMO_NOW - when(r["created_at"])) > timedelta(hours=24)
        }
        reported = {
            item.split(" · ")[0]
            for alert in checks.run_all_checks(
                loader.load_leads(str(self.csv_path)), summary, self.DEMO_NOW, Config()
            )
            if alert.kind == "stuck_new"
            for item in alert.items
        }
        self.assertEqual(reported, stuck_names)

    def test_send_mode_keeps_lead_names_out_of_the_log(self):
        with mock.patch("leadwatch.cli.send_digest", return_value=1) as sender:
            code, out, _ = run_cli(
                "--source", str(self.csv_path), "--now", "2026-11-12 09:00", "--send", env=self.SEND_ENV
            )
        self.assertEqual(code, 0)
        self.assertIn("Digest sent", out)
        self.assertNotIn("Maya Collins", out)  # CI logs can be public: no personal data there
        self.assertIn("Maya Collins", sender.call_args[0][2])  # but the Telegram message has it

    def test_print_flag_shows_the_digest_too(self):
        with mock.patch("leadwatch.cli.send_digest", return_value=1):
            _, out, _ = run_cli(
                "--source", str(self.csv_path), "--now", "2026-11-12 09:00", "--send", "--print", env=self.SEND_ENV
            )
        self.assertIn("Maya Collins", out)

    def test_an_unreadable_sheet_sends_a_warning_and_fails_loudly(self):
        missing = str(Path(self.tmp.name) / "missing.csv")
        with mock.patch("leadwatch.cli.send_message") as sender:
            code, _, err = run_cli("--source", missing, "--send", env=self.SEND_ENV)
        self.assertEqual(code, 1)
        self.assertIn("could not run", err)
        warning = sender.call_args[0][2]
        self.assertIn("could not run", warning)
        self.assertIn("File not found", warning)
        self.assertIn("silence", warning)

    def test_send_without_telegram_settings_is_a_clear_error(self):
        code, _, err = run_cli(
            "--source", str(self.csv_path), "--send", env={"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""}
        )
        self.assertEqual(code, 2)
        self.assertIn("not configured", err)

    def test_bad_now_value_is_rejected(self):
        code, _, err = run_cli("--source", str(self.csv_path), "--now", "yesterday")
        self.assertEqual(code, 2)
        self.assertIn("--now", err)

    def test_ping_and_find_chat(self):
        with mock.patch("leadwatch.cli.send_message") as sender:
            code, out, _ = run_cli("--ping", env=self.SEND_ENV)
        self.assertEqual(code, 0)
        self.assertEqual(sender.call_count, 1)
        with mock.patch("leadwatch.cli.find_chats", return_value=[{"id": "555", "name": "Test", "type": "private"}]):
            code, out, _ = run_cli("--find-chat", env=self.SEND_ENV)
        self.assertEqual(code, 0)
        self.assertIn("TELEGRAM_CHAT_ID=555", out)


if __name__ == "__main__":
    unittest.main()
