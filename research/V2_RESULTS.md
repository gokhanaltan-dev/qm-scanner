# BIST V2 candidate: entry rule and chart patterns (10 November 2026)

Follows the first-run report (`bist-scanner`), and replaces its "Proposed V2" section.
Rules version `bist-v2.0-candidate`. Research only.

- Code: https://github.com/gokhanaltan-dev/qm-scanner/blob/bist-research/bist/research.py
- Every variant tried, with its numbers: `research/*.csv` on the same branch.

## Verdict

1. **Entry: use a resting buy-stop at the pivot.** Per trade it earns the same as waiting for a confirmed close, and it gets filled on 81% of triggers instead of 15%.
2. **Patterns: useful for defining risk, not proven as extra edge.** They gave tighter stops and better R in 2016 to 2021. In 2022 to 2023 they did no better than simply buying a leader near its 55-session high.
3. **Bull flags are the workhorse** (about 50 a year). Ascending triangles and VCPs looked best per trade but are rare.
4. **2024 to 2026 has not been looked at** for any V2 rule. It is the one clean test left.
5. **Nothing is armed today.** Six shares pass the leader filter; one has a pattern forming.

## How this was done

- Designed on 2016 to 2021 only: the data was cut at 31 December 2021 before any V2 code ran.
- Choices were committed to the repository, then 2022 to 2023 was looked at once.
- Checked: a rerun on data to 2023 reproduces all 467 development trades exactly (no look-ahead); one trade checked by hand against the bars.
- Net of 25 basis points per side. Stop-order fills assume a further 0.05 ATR of slippage.
- 90% ranges resample whole signal-months.

## Shared rules

**Leader filter (unchanged from V1):** in the 200 most liquid shares; close > SMA50 > SMA200 with SMA50 rising; relative-strength percentile 80 or more.

**Armed:** a pattern holds and the close is within 1 ATR and 5% below its pivot. Pivot and invalidation low freeze. Expires after 10 sessions; invalidated if the low breaks or the leader filter fails.

**Exits (unchanged):** stop at the 10-session low minus 0.25 ATR; sell at the next open after a close below EMA20; 60-session limit.

## 1. Entry rule

Tested on every leader within 1 ATR of its 55-session high, 2016 to 2021, so the sample is large.

| Entry | Triggers filled | Trades | Mean R | 90% range | Return per trade |
| --- | --- | --- | --- | --- | --- |
| V1: confirmed close, next open, cap 0.75 ATR | 15% | 149 | +0.23 | +0.02 to +0.43 | +2.5% |
| Same, cap 1.5 ATR | 35% | 312 | +0.20 | +0.07 to +0.33 | +2.5% |
| Same, cap 1.5 ATR, no volume rule | 48% | 672 | +0.21 | +0.10 to +0.32 | +2.9% |
| Confirmed close, next open, skip a gap over 0.5 ATR | 77% | 577 | +0.16 | +0.05 to +0.27 | +1.9% |
| **Buy-stop at the pivot** | 81% | 1,304 | +0.22 | +0.12 to +0.31 | +3.2% |
| Buy-stop, sell if day one closes back under the pivot | 84% | 1,711 | +0.14 | +0.07 to +0.20 | +1.9% |

What this says:

- Waiting for the close costs most of the trades and buys no extra expectancy.
- The 1.5x volume rule added nothing measurable (+0.20 with, +0.21 without).
- Selling a breakout that closes back under the pivot on day one hurt. About 70% of breakouts close back under the pivot within five sessions and the rule is still profitable overall, so that is noise to sit through, not a signal.

**The entry rule adopted**

- The session after a setup arms, and for up to 10 sessions: buy-stop at the pivot.
- Skip that day if the open is above min(pivot x 1.03, pivot + 0.75 ATR).
- No volume or close condition.
- If stop orders are not available: buy the next open after a confirmed close, unless it opens more than 0.5 ATR above that close. In these tests that earned between half and four-fifths of the buy-stop's expectancy.

A tighter stop (no more than 1.5 ATR below entry) was also tested. It raises mean R (+0.40) because each R is smaller, but return per trade falls (+2.1%), win rate drops to 30% and the range is wide. Kept as a secondary variant, not adopted.

## 2. Chart patterns

Numeric definitions (all on completed sessions; swing highs need two lower highs each side, so they are known two sessions late):

| Pattern | Rule |
| --- | --- |
| Flat base | 30 sessions. Two separate swing highs within 1.5% of the high, 5+ sessions apart. Depth 20% or less. Last-10 range at most 60% of the base range. |
| Ascending triangle | 40 sessions. Two swing highs within 1.5% of the high, 8+ sessions apart. The low after the last touch is at least 0.5 ATR above the low between the touches. Depth 25% or less. |
| VCP | Blocks of 30, 20 and 10 sessions, each range at most 75% of the one before; first 35% or less, last 10% or less; rising lows; last block within 3% of the pivot; volume below its 60-session mean. |
| Bull flag | The 20-session high is 3 to 15 sessions old; price rose 25% or more into it within 15 sessions; the pullback has given back at most half the rise. Pivot is that high. |

Where two labels fit one base, the share has one setup. Order of labels: triangle, flat base, VCP, flag.

**Buy-stop entry, V1 stop**

| | 2016 to 2021 | | | 2022 to 2023 | | |
| --- | --- | --- | --- | --- | --- | --- |
| | Trades | Mean R | Return | Trades | Mean R | Return |
| Leader near high, no pattern | 1,304 | +0.22 | +3.2% | 564 | +0.70 | +13.2% |
| **All four patterns, one per share** | 467 | +0.40 | +4.4% | 218 | +0.78 | +12.1% |
| Ascending triangle | 73 | +0.61 | +5.6% | 8 | too few | |
| VCP | 149 | +0.50 | +3.7% | 26 | +1.21 | +12.0% |
| Flat base | 104 | +0.46 | +4.2% | 9 | too few | |
| Bull flag | 299 | +0.31 | +4.3% | 200 | +0.74 | +12.0% |
| V1 tight base | 34 | +0.18 | +0.7% | not run | | |

Pattern book, 90% ranges: +0.21 to +0.60 (2016 to 2021), +0.40 to +1.15 (2022 to 2023).

Reading it:

- 2016 to 2021: the pattern book beat the no-pattern reference in R (+0.40 against +0.22) and slightly in return per trade. The ranges overlap.
- 2022 to 2023: no advantage. Month by month the pattern book was ahead in 9 of 24 months. Return per trade was lower than the reference.
- The reliable difference is the stop. Median risk per trade was 12% for patterns against 18% for the reference in 2016 to 2021, which allows a larger position for the same risk.
- 2022 to 2023 was an exceptional lira bull market (XU100 up 288%). Almost any long rule made money, so it mainly shows the rules were not broken.
- Pattern book by year: 2016 +0.07, 2017 +0.78, 2018 -0.03, 2019 +0.64, 2020 +0.69, 2021 +0.02, 2022 +0.81, 2023 +0.74. Three flat years in eight.
- Not dependent on a few trades: without the best five, +0.30R and +0.57R.
- At 50 basis points per side: +0.35R and +0.75R.
- Flat bases, triangles and VCPs almost vanished in 2022 to 2023 (9, 8 and 26 trades). Quiet bases are rare in a runaway market.

## Today (session of 10 November 2026)

Nothing armed. Market context is unchanged from the first-run report: XU100 below both averages, 12% of universe shares above their 50-session average. In the baseline test, breakouts with breadth under 50% earned about half as much.

Six shares pass the leader filter:

| Share | Close | Nearest pivot | Below pivot | State |
| --- | --- | --- | --- | --- |
| AHGAZ | 45.46 | 49.06 (VCP) | 7.3%, 2.1 ATR | Pattern forming, too far to arm |
| ENERY | 14.15 | 14.75 (55-session high) | 4.1%, 1.0 ATR | No pattern |
| ENJSA | 119.00 | 124.00 (55-session high) | 4.0%, 1.1 ATR | No pattern |
| RYGYO | 48.90 | 54.80 (55-session high) | 10.8% | No pattern |
| CELHA | 29.62 | 36.06 (55-session high) | 17.9% | No pattern |
| HRKET | 7.90 | 10.43 (55-session high) | 24.3% | No pattern |

VBTS and gross-settlement status were not checked for these, because none is a setup yet.

## What is still unknown

- Whether any of this holds from 2024 onwards. Not looked at.
- Delisted shares are missing from the data, which flatters every figure.
- Results are in lira through very high inflation.
- Real stop-order fills on Borsa İstanbul. The 0.05 ATR slippage is an assumption.
- Past VBTS and gross-settlement measures are not modelled.
- About thirty pattern, entry and stop combinations were compared on 2016 to 2021 before choosing. Some of the development result is selection.

## Next

1. Open the 2024 to 2026 holdout once for the frozen V2 candidate, or leave it sealed and forward-track on paper instead.
2. Move the code to its own repository and add the daily run.
3. Fix the missing latest-session opening price before forward tracking.
4. Add a lira cash yield to the portfolio test.
