# SOP: Handling Lead Watchdog alerts

**Owner:** Business Operations · **When:** every morning, about 10 minutes · **Version:** 1.0 (2026-11-12)

## 1. Purpose

Turn the daily digest into action. Nothing red stays red for more than a day, and every fix leaves a note so the same problem is cheaper next time.

## 2. The 10-minute morning routine

1. Open the digest in Telegram. The first line says how many items need action and how many to watch.
2. **🔴 Action needed** first, in the order shown. Time-box each to 5 minutes. If it is bigger than that, go to section 3 and escalate with facts.
3. **🟡 Keep an eye on**: assign or fix before lunch.
4. **📅 Calls**: make sure each owner knows about their call and has the lead's context.
5. **🧹 Data clean-up**: do it in one batch on Friday, unless it affects an alert today.
6. Write down what you did (section 7), even if it took two minutes.
7. No digest by 09:00, or it starts with ⚠️ "could not run"? Go to section 5. **Silence is not "all clear".**

## 3. Fix it yourself, or bring it to the founder?

| Fix it yourself, then tell the team it's done | Bring to the founder with the facts and the options |
|---|---|
| A booking link, form or automation that is broken | Anything clients see: offer, price, page copy, ad creative |
| Access or permissions (a tool, calendar, sheet) | Who does what: moving leads between setters or closers, role changes |
| Wrong or missing data: a status, a source, a duplicate | Money: ad spend, refunds, discounts, bonuses |
| A report or dashboard that shows the wrong thing | A partner's business decisions: strategy, pricing, hiring |
| A routine nobody has written down: write the SOP | |

Rule of thumb: **reversible and inside our own tools → fix it. It changes what a person experiences, or costs money → escalate.** Changes to people's work go through their setter or closer manager.

## 4. Playbooks

### 4.1 Booking rate down

*Trigger:* "Booking rate down X%". Critical if it fell 40%+ or one source is at zero.

1. **Is it real?** Read the counts in the alert (for example "14 of 58 vs 20 of 57"). With very few leads, wait a day.
2. **Which source?** The alert names the source that explains the drop and how unlikely it is to be luck.
3. **One source at or near zero:** test that channel end to end *as a lead*. Submit the form, check the confirmation, open the booking link, book a slot, confirm it lands in the calendar and the sheet. Zero bookings from one channel usually means a broken step: a link, a form, an automation.
4. **No single source:** check the whole booking flow: calendar availability, setter capacity, and the "Contacted within 24h" line in the digest.
5. **Fix** a broken link, form or automation, then test again with a dummy lead and post "Fixed: what, when, impact" to the team.
6. **Escalate** if the cause is the offer, the ad creative, pricing or capacity.

*Done when:* a test lead completes the whole flow, and the next digests show the source booking again.

### 4.2 New lead waiting too long

*Trigger:* over 24h (🟡) or over 48h (🔴) with status `New`.

1. Open the lead. Is there an owner? **No:** assign it (follow the manager's rule) and message the owner now.
2. **Yes:** ask the owner, or their manager, to make contact today. Check that the lead notification actually reached them.
3. Several unassigned leads means the assignment automation is broken: find the trigger, fix it, re-run the digest.
4. Repeated delays from the same owner: escalate (who does what).

*Done when:* status is `Contacted` and `first_contact_at` is filled in.

### 4.3 Contacted lead going cold

*Trigger:* `Contacted`, no touch for 72h+.

1. Ask the owner for the next step and a date.
2. No answer by end of day: ask their manager.
3. If the lead is dead, set it to `Lost` so it stops showing up.

### 4.4 Call time passed, status not updated

1. Ask the owner what happened: held, no-show or rescheduled.
2. Update the status. For a no-show, send the rebooking message within 2 hours.

### 4.5 Lead volume down

1. Check ad accounts, budgets and campaign status, then tracking links and that the lead form still works.
2. Anything about budget is the founder's call: send facts and options (section 6).

### 4.6 Data clean-up (weekly)

- **Duplicates:** keep the earliest row, note the merged ids.
- **No source:** fill it from the form or UTM data.
- **Unknown status:** use the allowed list: `New`, `Contacted`, `Call booked`, `Won`, `Lost`.
- **Contradictions:** complete the missing field or correct the status (a `Won` deal needs a value).
- **Unreadable cells:** use the format `yyyy-mm-dd hh:mm`.

## 5. When the watchdog itself fails

- No digest by 09:00: open the latest run in GitHub *Actions* and read the error.
- Usual causes: the sheet is no longer published, the link changed, a column was renamed, the Telegram token was revoked.
- Until it is fixed, check the sheet by hand for new leads older than 24h.

## 6. Escalation message template

```text
Subject: [Decision needed by <day, time>] <one-line problem>

What happened: <facts, numbers, time window>
Impact:        <leads, bookings or $ at risk>
Likely cause:  <what you found, and how you checked it>
Options:
  A) <option>: effort, cost, risk
  B) <option>: effort, cost, risk
My recommendation: <A or B, and why>
What I need from you: <decision or approval> by <time>
```

## 7. After-action log

| Date | Alert | Root cause | Fix | Time to resolve | How we prevent it |
|---|---|---|---|---|---|
| *Example (demo scenario)* 2026-11-12 | Booking rate down 31%, Facebook Ads 0 of 16 | Booking link on the ad landing page pointed to an old calendar | Link updated, test lead booked a slot | 40 min | Add "test every channel's link" to the Monday routine |

## 8. Definitions and default thresholds

| Term | Meaning |
|---|---|
| Speed to lead | Time from a lead arriving to the first contact. Target: under 24h |
| Booking rate | Share of leads that booked a call, measured on leads created 9 to 2 days ago, so newer leads have time to convert |
| Going cold | `Contacted` with no touch for more than 72h |
| Overdue call | `Call booked`, call time passed by more than 2h |
| Dead source | A source with 0 bookings when last week's rate says we should see several, and under a 5% chance that this is luck |

Thresholds live in `leadwatch/config.py`. Tune them with the sales managers after the first two weeks of real data.
