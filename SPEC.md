# BIST tight-base breakout: V1 specification

Version `bist-tightbase-v1.0`, frozen 10 November 2026 before any backtest result was seen.
Every number lives in `bist/config.yaml`; this file says how the code reads them.
All thresholds are unvalidated starting hypotheses. A change to any of them is a new version.

Research only. Nothing here places orders.

## Data

| Need | Source | Notes |
| --- | --- | --- |
| Share list, sector | TradingView screener, market "turkey" | Today's listings only. Type `stock`; funds, warrants and rights are excluded. |
| High, low, close, VWAP, TRY turnover | İş Yatırım `HisseTekil` | Raw (`HG_*`) and vendor-adjusted (`HGDG_*`) series in one response. No opening price. |
| XU100 close, USD/TRY | Same İş Yatırım rows | Aligned to the share's own dates. |
| Opening price | Yahoo Finance (`.IS`) | Only the open/close ratio is used, applied to the adjusted close. The Yahoo bar is trusted only if its high/close and low/close ratios match İş Yatırım's raw bar within 1.2%. |
| Capital increases, dividends | İş Yatırım `GetSermayeArttirimlari` | Stored for audit and for flagging pending actions. |

Downloads run on GitHub Actions; the Claude workspace cannot reach these sites directly.

**Adjustment convention.** Signals and returns use the vendor-adjusted series, which carries
splits, bonus issues (bedelsiz), rights issues (bedelli) and cash dividends in one factor
(`HGDG_KAPANIS / HG_KAPANIS`). Dividends therefore appear once, inside the price series, and are
never added again as cash. The factor is 1 on the latest bar, so every level in a scan is in
today's tradable TRY. Share volume is `turnover / adjusted VWAP`, i.e. on the adjusted share basis;
TRY turnover is kept separately and used for liquidity.

Yahoo alone is not sufficient: it records bonus issues as splits but ignores rights issues
(checked on KONTR, 9 December 2025: raw close 30.16 to 14.31, no Yahoo adjustment).

**Calendar.** Sessions are dates with an XU100 close. A bar dated on the retrieval day counts
only if retrieval was after 18:30 Europe/Istanbul. A share with no bar on a session did not trade.

**Recorded with every run** (`provenance.json`): sources, retrieval time, latest completed session,
delay note, coverage, failed downloads.

## Universe

Rebuilt each session from data up to that session: ordinary shares with at least 252 completed
sessions, ranked by the median TRY turnover of the last 60 sessions (a non-trading day counts as
zero), top 200. Younger listings that would rank are listed separately as exploratory.

## Relative strength

`RS = mean over k in {63, 126} of (C_t / C_{t-k}) / (XU100_t / XU100_{t-k}) - 1`, ranked as a
percentile inside that day's universe. Unrelated to RSI.

## Tight base

Judged after the close of session `d`, on the 40 completed sessions ending at `d`:

1. Pivot `P` = highest high. Base low = lowest low. Depth `(P - low) / P <= 20%`.
2. At least two highs `>= 0.99 P`, first and last at least 5 sessions apart.
3. High-low range of the last 10 sessions `<= 60%` of the range of the first 20.
4. Mean share volume of the last 5 sessions `<= 75%` of the mean of the 20 before them.
5. `close > SMA50 > SMA200`, and SMA50 above its value 20 sessions earlier.
6. RS percentile `>= 80`, and the share is in the universe.
7. ATR = Wilder ATR(14): seed is the mean of the first 14 true ranges.

States:

- **FORMING**: rules 1 to 6 hold; close is 1 to 2 ATR below `P` and at most 5% below.
- **ARMED**: same, close within 1 ATR below `P`. Setup ID (`TICKER-YYYYMMDD`), `P`, base low and
  window freeze here and never move.
- While armed, each later session `t` is checked in this order: low below the frozen base low
  => **INVALIDATED**; confirmation (below) => **BREAKOUT CONFIRMED**; rule 5 or 6 failing at the
  close => **INVALIDATED**; tenth session without confirmation => **EXPIRED**. A high above `P`
  without confirmation is an **UNCONFIRMED CROSS** and the setup stays armed.
- A share can arm again from the session after a setup resolves; that is a new setup ID.
  No new setup arms while a model position in the share is open.

V1 reading of the brief: a signal needs a setup that was ARMED on an earlier close. A share that
jumps from FORMING straight through the pivot is not a V1 signal.

Confirmation on a completed bar `t`:

- `close >= P + 0.10 x ATR(t-1)`
- share volume `>= 1.5 x` mean volume of sessions `t-20 .. t-1`
- `(close - low) / (high - low) >= 0.70`; a bar with no range fails
- the share traded

## Execution model

- Signal exists only after the confirming close. Entry is the next bar's open.
- `max entry = min(1.03 P, P + 0.75 x ATR(t-1))`. Enter only if `P < open <= max entry`, the open
  is verified, and the entry bar has a range. Otherwise the entry is skipped and logged.
- `stop = lowest low of sessions t-10 .. t-1, minus 0.25 x ATR(t-1)`.
- Exit precedence within a session: (1) a sale scheduled at the previous close fills at the open;
  (2) open at or below the stop fills at the open; (3) low at or below the stop fills at the stop;
  (4) at the close, a close below EMA20 or the 60th holding session schedules a sale for the next
  tradable open. EMA20 is seeded with the mean of the first 20 closes.
- No fill on a bar with `high == low` (locked price limit or single print) or on a day with no bar;
  the order carries to the next tradable bar. If the open cannot be verified, an exit fills at that
  day's low.
- Costs: 10, 25 and 50 basis points per side as all-in scenarios. Headline figures use 25.
- `R = entry - stop`. A false breakout is a close below the frozen pivot within five sessions of
  confirmation, whether or not a trade was taken.
- Tick rounding (live levels only): confirmation price rounds up, maximum entry and stop round down,
  using the Borsa İstanbul table in force since 6 November 2023. Historical tests compare
  adjusted prices without rounding.

## Portfolio test defaults

Illustrative only: TRY 1,000,000 start, 0.5% of equity risked per trade, at most 2% aggregate
initial open risk, four positions, two per sector, position notional capped at 0.1% of the trailing
20-session median turnover, idle cash earning nothing. Same-day candidates are taken in score order.
Entries fill before that day's exits, so a freed slot is reusable only the next day.

## Score (0 to 100)

Linear between the anchors in `config.yaml`, clipped outside them. Trend and prior strength 20,
base structure and contraction 25, relative strength 25, volume 15, proximity and practicality 15.
A description of how closely a chart matches the pattern. Not a probability.

## Backtest protocol

- Development 2016-01-04 to 2021-12-31, validation 2022 to 2023, final test 2024-01-01 onwards.
  No parameter is fitted, so each calendar year is out of sample by construction.
- Trades whose signal and exit fall in different periods are purged from period statistics.
- One model position per share; overlapping labels on one base are one setup.
- Baseline: close above the highest high of the prior 55 sessions, same entry, stop, exits, costs.
- Benchmark: XU100 price index, buy and hold.
- Sensitivity: eight one-at-a-time variants, development period only, all listed in `results.json`.
- Uncertainty: 90% interval for mean R from resampling whole signal-months, 2,000 draws.

## Known limits

- **Survivorship.** Only shares still listed on the retrieval date are in the data. Delisted and
  merged shares are missing, which flatters every result.
- **Tradability history.** Past VBTS measures, gross settlement, single-price trading and
  suspensions are not in the data. Locked limit days are inferred from `high == low`.
- **Price limit.** Borsa İstanbul's English price-band page lists wider margins than the data
  shows; no-range bars in 2021 and 2026 cluster at exactly +10% and -10%. Treat the current margin
  as about 10% and confirm per share before trading.
- **Benchmark mismatch.** Share prices are dividend-adjusted; XU100 is a price index.
- **Nominal TRY.** Returns are in lira through a period of very high inflation. USD context is
  reported next to them.
- **Sectors** are today's TradingView labels applied to the past.
- **Opens.** About 3% of bars have no verified open; entries on those bars are skipped.
