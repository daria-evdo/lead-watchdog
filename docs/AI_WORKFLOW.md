# How AI was used on this project, and how it was checked

**Disclosure:** this project was built with AI assistance (Claude). The AI drafted the code, tests, demo-data generator and docs. A draft from an AI is a hypothesis, not a result. The steps below are what turn it into something trustworthy.

## What the AI did

- Proposed the rule set and default thresholds from the problem statement (a funnel that leaks quietly).
- Drafted the Python modules, the tests and the demo-data generator.
- Wrote the README, the SOP and this document.

## How the output was checked

1. **Tests with hand-computed expectations.** 52 tests. For example, a week with 4 leads and 2 bookings must be 50%; a source that fell from 40% to 0% with 10 leads has a 0.6¹⁰ ≈ 0.6% chance of being luck.
2. **Boundary tests.** 24h exactly is fine, 24h 1min is flagged; a call 1.9h late is inside the grace period, 2.1h is not.
3. **Bug injection.** `python scripts/mutation_check.py` breaks the code on purpose in 8 ways (a wrong threshold, a leaked token, day-first dates, ...). The test suite must go red every time. It does: 8 of 8.
4. **Independent recount.** Tests recompute the headline numbers of the digest (booking rates, speed to lead, stuck leads, per-source counts) straight from the CSV with the `csv` module, using none of the project's code.
5. **Reading the output, not only the tests.** The first version of the demo data was too random: the overall booking rate went *up* even though one channel was at 0%. Tests were green; the output showed the story didn't work. The generator now spreads bookings evenly. The same lesson is built into the product: overall numbers hide a dead channel, so the digest always breaks a drop down by source.
6. **A second look at the same output** found two phone numbers colliding by accident and creating duplicates nobody planted. The generator now reserves numbers.

## Risks found in review, and what changed

| Risk | What was done |
|---|---|
| New leads haven't had time to book, so "this week vs last week" always looks like a drop | Conversion windows are shifted back 2 days |
| Small samples wobble; false alarms get the bot muted | Minimum sample sizes, plus a binomial "could this be luck?" test per source |
| The bot fails silently when the sheet link breaks | It sends a warning instead of nothing |
| Lead names would show up in public CI logs | With `--send` the digest goes only to Telegram unless `--print` is added |
| A Telegram error could print the bot token | Errors are re-raised without the original exception; a test checks it |
| Time zones and date formats are silent assumptions | Explicit setting (`LEADWATCH_UTC_OFFSET`), documented date order, a "dated in the future" alert |

## Habits that keep AI output honest

- Ask the AI how the code could be wrong, not only whether it is right ("list the ways this rule could fire falsely").
- Make every assumption a setting or a test.
- Check numbers against a source the AI didn't produce.
- Never paste real customer data into an AI tool; use synthetic data.

## My part

> Edit this section so it says exactly what you did yourself, for example: read and can explain every rule; created the Telegram bot and ran the digest on my own phone; set up the daily GitHub Action; wrote the SOP in Notion; tuned the thresholds. Delete this note when done.
