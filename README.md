# Breakout sheet

A daily scan of US stocks for Qullamaggie-style setups. It runs after the US close,
decides whether the market is worth trading, finds the leaders that are setting up,
rates each one out of 100 and gives a trigger, a stop and a position size.

The result is one phone-friendly page, rebuilt every weekday at 22:30 UTC, with a second
check about 40 minutes after the US open that shows which triggers have fired.

## What it looks for

| Setup | Rule of thumb |
| --- | --- |
| Breakout | Top 7% of gainers over 1, 3 or 6 months, a run of 30% or more, then a 4 to 40 day base that gives back less than half the run, tightens, dries up in volume and rides its rising 10/20-day averages |
| Episodic pivot | A gap of 8% to 50% on at least 2.5x normal volume, in a stock that was above $5, traded $5m a day and ranged under 8% a day before it, that has not been undercut since and closes within half a daily range of its high |

Stocks must be above $5, trade $10m a day and have an average daily range of 4% or more.
The parabolic short is left out on purpose: this sheet is long only.

Each breakout is put in one of three places:

- **Ready to trigger**: tight, and within one average daily range of the trigger.
- **Broke out today**: had a valid base yesterday and closed above the trigger on volume.
- **Still building**: a leader with a base that is not tight or close enough yet.

## The market light

| Light | Condition (Nasdaq 100) | Size |
| --- | --- | --- |
| Green | Price above the 20-day, 10-day above 20-day, both rising | Full |
| Amber | Anything in between, or green with weak breadth or failing breakouts | Half |
| Red | 10-day below 20-day and price below the 20-day | None |

Green is downgraded to amber when fewer than 40% of stocks are above their 20-day average,
or when fewer than 40% of the last two weeks' breakouts are still above their breakout close.

## The rating

| Part | Points | What earns them |
| --- | --- | --- |
| Leadership | 25 | Relative-strength rank and the size of the run |
| Tightness | 25 | Last five days' ranges against the ranges during the run, tight closes, higher lows |
| Volume dry-up | 10 | Recent volume against the 50-day average |
| Moving averages | 15 | Above rising, stacked 10/20-day averages, and close to them |
| Base shape | 10 | Shallow retrace, sensible length |
| Near trigger | 10 | Distance to the trigger in daily ranges |
| Range and liquidity | 5 | Average daily range and dollar volume |

A+ is 85 or more, A is 75, B is 62.

These weights are a checklist of the pattern, not odds: in the backtest the rating did
not rank the winners (see below).

Episodic pivots are rated separately: near its high 40, strong close 20, fresh move 15,
gap size 10, volume surge 5, range and liquidity 10.

## What the backtest found

`backtest/run.py` replays the scanner over about three years (763 sessions, October 2023
to November 2026, 5,614 stocks). Each past session is scanned with only the bars known at
its close, and every setup that triggers the next session is traded by fixed rules:
entry at the trigger, stop 0.4 to 1.0 daily ranges below, half sold on the first
profitable close from day three, the rest on the first close under the 10-day average.
Results are in R, the amount risked per trade.

| Ready-list breakouts | Trades | Winners | Average |
| --- | --- | --- | --- |
| Green light | 565 | 49% | +0.08R |
| Amber | 413 | 43% | -0.05R |
| Red | 254 | 40% | -0.19R |
| All | 1,232 | 45% | -0.02R |

What that says:

- **The light is the part that held up.** Red lost money in both halves of the period.
- **Even on green the edge is thin**: +0.08R with a standard error of 0.06, +0.04R if
  every unclear entry day is counted as a loss, and before trading costs.
- **The rating did not rank the winners.** Neither did group strength, strength during
  pullbacks, base count or distance above the 50-day. A model fitted on the first half of
  the period could not rank setups in the second half, so the weights were left alone
  rather than tuned to noise.
- **Exit rules made little difference.** Eight were compared, from fixed targets to a
  20-day trail; on green they ranged from +0.02R to +0.11R.
- **Thin, wild gaps collapse.** Gaps in stocks under $5, ranging 8% a day, gapping 50% or
  more, or trading under $5m a day fell 15% against the Nasdaq over the next 10 sessions.
  They are now filtered out. Among the rest, only gaps closing within half a daily range
  of their high broke even (+0.02R on 723 trades, against -0.20R), so that is required.

Limits: daily bars cannot show whether the low or the breakout came first on the entry
day; only stocks listed today are included, which flatters results; no costs are counted.

To run it again, edit `backtest/request.txt` and push. Results land on the
`backtest-results` branch; `python -m backtest.analyse <folder>` prints the tables and
`--json backtest/summary.json` refreshes the numbers shown on the page.

## After the open

`.github/workflows/open-check.yml` runs about 40 minutes into the US session (15:10 UK).
For each stock on the ready and episodic pivot lists it reads the day's 5-minute bars and
shows whether price has traded through the trigger, the low of the day (the real stop),
the size that stop allows, and the share of a normal day's volume traded so far. The
section sits at the top of the page until the evening scan replaces the sheet.

## Earnings

Next report dates and recent results come from Nasdaq's earnings calendar, with Yahoo as
a fallback. A setup reporting within 10 days carries a warning, and each gap shows whether
it came on earnings and by how much the result beat or missed.

## Position size

The stop is planned from the last three days' lows, never closer than 0.4 and never wider
than 1.0 average daily ranges below the trigger. Shares are set so that a stop-out costs
`risk_per_trade_pct` of the account (halved on amber, zero on red), and the position is
capped at `max_position_pct`. On the day, use the actual low of the day if it is tighter,
and skip the trade if it is wider than one daily range.

## Settings

Everything adjustable is in `config.yaml`: account size, risk per trade, position cap,
the filters, and `no_trade`, a list of tickers that are never shown as setups.

## Run it

```
pip install -r requirements.txt
python -m scanner.build          # live scan, writes site/index.html
python -m scanner.build --demo   # invented data, no network needed
python -m pytest tests
```

## Put it on GitHub

1. Create a repository and push these files to it.
2. In the repository settings, open Pages and set the source to GitHub Actions.
3. Open the Actions tab, pick "Daily scan" and press "Run workflow" for the first run.

After that it runs by itself every weekday. Each run also appends the day's setups to
`data/history.csv`, which is the record to measure the scanner against later.

A free GitHub Pages site is public. Position sizes are shown as a share of the account
as well as in shares, so `size_usd` can stay at a round placeholder.

## Limits

- Prices come from Yahoo through `yfinance`. If a download is too thin, the run stops
  and yesterday's page stays up rather than publishing a half-empty scan.
- Apart from earnings, the scan cannot tell why a stock gapped: check the news.
- Earnings data is best effort and can be missing.
- Intraday prices in the after-open check can lag by up to 15 minutes.
- Bases are found by rule, not by eye. Treat the list as a shortlist to review on a
  chart, not as a set of orders.
