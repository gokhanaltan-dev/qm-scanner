# Breakout sheet

A daily scan of US stocks for Qullamaggie-style setups. It runs after the US close,
decides whether the market is worth trading, finds the leaders that are setting up,
rates each one out of 100 and gives a trigger, a stop and a position size.

The result is one phone-friendly page, rebuilt every weekday at 22:30 UTC.

## What it looks for

| Setup | Rule of thumb |
| --- | --- |
| Breakout | Top 7% of gainers over 1, 3 or 6 months, a run of 30% or more, then a 4 to 40 day base that gives back less than half the run, tightens, dries up in volume and rides its rising 10/20-day averages |
| Episodic pivot | A gap of 8% or more on at least 2.5x normal volume that closes strong, has not been undercut since, and is still within 1.5 daily ranges of its high |

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
- The scan reads price and volume only. It cannot tell why a stock gapped: check the
  catalyst on every episodic pivot.
- Earnings dates are best effort and can be missing.
- Bases are found by rule, not by eye. Treat the list as a shortlist to review on a
  chart, not as a set of orders.
