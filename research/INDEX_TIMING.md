# Swing timing on XU100, XU030 and XBANK (10 November 2026)

Long-or-cash rules on the three indices themselves. Research only; nothing places orders.

- Code: https://github.com/gokhanaltan-dev/qm-scanner/blob/bist-research/bist/index_timing.py
- Every rule tried: `research/index_timing_*.csv` on the same branch.

## Verdict

1. **In lira, no timing rule beat holding XU100 or XU030 in either period.** What timing bought was smaller drawdowns, at a cost in return.
2. **The rule worth keeping is a 20/10 channel on XU100.** It had smaller falls than the index in both periods. Measured in dollars with idle cash kept in dollars, it turned a losing six years into a gain and matched the index in the boom.
3. **Bank-index timing is not validated.** It worked in 2016 to 2021 and lost badly to holding in 2022 to 2023.
4. **Today the channel rule is in cash** on XU100 and XU030. XBANK gave a buy signal on 6 November.
5. **2024 to 2026 has not been looked at** for any of these rules.

## The channel rule

- **Buy** at the next open after the index closes above its highest close of the previous 20 sessions.
- **Sell** at the next open after it closes below its lowest close of the previous 10 sessions.
- Otherwise hold what you have. One position, long or cash.

Trades last about 21 sessions on average and the rule is invested a little under half the time.

## Results

10 basis points per side. Index levels exclude dividends. Idle cash earns nothing in the lira columns.

**XU100**

| | 2016 to 2021 | | 2022 to 2023 | |
| --- | --- | --- | --- | --- |
| | Per year | Worst fall | Per year | Worst fall |
| Hold, lira | 17.6% | -32% | 97.9% | -22% |
| Channel 20/10, lira | 13.9% | -20% | 53.5% | -13% |
| Close above SMA50, lira | 16.1% | -22% | 73.8% | -24% |
| Hold, dollars | -8.7% | -64% | 33.2% | -27% |
| Channel 20/10, dollars | 6.5% | -37% | 32.6% | -20% |
| Close above SMA50, dollars | 0.5% | -43% | 26.7% | -31% |

**XU030**

| | 2016 to 2021 | | 2022 to 2023 | |
| --- | --- | --- | --- | --- |
| | Per year | Worst fall | Per year | Worst fall |
| Hold, lira | 15.4% | -32% | 96.0% | -21% |
| Channel 20/10, lira | 11.6% | -26% | 47.3% | -15% |
| Hold, dollars | -10.3% | -67% | 31.9% | -26% |
| Channel 20/10, dollars | 1.0% | -45% | 23.7% | -18% |

**XBANK**

| | 2016 to 2021 | | 2022 to 2023 | |
| --- | --- | --- | --- | --- |
| | Per year | Worst fall | Per year | Worst fall |
| Hold, lira | 5.2% | -51% | 132.9% | -39% |
| Close above SMA50, lira | 9.4% | -23% | 55.4% | -47% |
| 55-high in, 20-low out, lira | 8.7% | -22% | 35.0% | -45% |
| Hold, dollars | -18.2% | -83% | 56.8% | -40% |
| Close above SMA50, dollars | 4.1% | -43% | 14.4% | -57% |

"Dollars" means the same trades, with the account measured in US dollars and idle cash held in dollars. Sterling figures are close to the dollar ones (in `research/index_timing_in_usd_gbp.csv`); the sterling exchange-rate series has a few suspect days, so dollars are shown.

Channel rule on XU100, trade detail:

| | 2016 to 2021 | 2022 to 2023 |
| --- | --- | --- |
| Trades | 31 | 11 |
| Winners | 55% | 45% |
| Average trade | +2.9% | +9.1% |
| Median trade | +0.1% | -0.7% |
| Best / worst | +28% / -8% | +39% / -8% |
| By year, lira | 2016 -1%, 2017 +29%, 2018 -17%, 2019 +18%, 2020 +42%, 2021 +18% | 2022 +85%, 2023 +23% |

## How the rule was chosen, and what that does to the evidence

- Seven rules on each index and two rotation rules were run on 2016 to 2021 only (23 combinations).
- "Close above the 50-session average" had the best return-to-drawdown ratio on all three indices and was committed as the primary choice. The channel rule (XU100, XU030) and a 55/20 channel (XBANK) were committed as declared seconds.
- One look at 2022 to 2023: the primary rule gave up a quarter of the index return without reducing the drawdown, and on XBANK it was worse than holding on both counts. The channel rule kept its smaller drawdown.
- The dollar view was calculated after that look. It is the same trades in another currency, but it was not part of the original choice. Treat it as a finding to confirm.
- The channel rule's neighbours on 2016 to 2021 (15/10, 25/10, 20/15, 30/15) returned 11% to 16% a year with worst falls of 20% to 25%. It does not depend on exactly 20 and 10.
- 2018 was not avoided: the rule lost 17% against the index's 22%.
- 42 trades in eight years, and the average is made by a few large ones. No range can be put on that with confidence.

## What was tried and dropped (2016 to 2021)

| Rule | XU100 | XU030 | XBANK |
| --- | --- | --- | --- |
| Close above SMA200 | 5.4% a year | 3.5% | -3.3% |
| EMA20 above EMA50 | 7.1% | 1.5% | 1.3% |
| 55-session high in, 20-session low out | 8.4% | 8.8% | 8.7% |
| 63-session return above zero, weekly | 4.6% | 3.8% | 0.9% |
| RSI(2) under 10 in an uptrend, exit above SMA5 | -2.4% | -3.0% | -2.9% |
| Hold the strongest of the three each week | -2.0% a year (rotation) | | |
| XBANK when leading, else XU100, else cash | 9.2% a year, 101 trades (rotation) | | |

Slow trend rules (100 sessions or more) were the worst: on these indices the moves are over before a slow average turns.

## Today

Index data runs to Monday 9 November (Yahoo had not published 10 November). XU100 closed at 11,661.86 on 10 November from the other source, which changes nothing below.

| Index | Close, 9 Nov | Channel rule | Next trigger |
| --- | --- | --- | --- |
| XU100 | 11,712.7 | Cash since 2 September | Buy on a close above 12,250.7 |
| XU030 | 14,611.0 | Cash since 16 September | Buy on a close above 15,191.6 |
| XBANK | 17,021.7 | Long since the close of 6 November | Sell on a close below 14,607.5 |

Trigger levels move each day as the 20-session and 10-session windows roll.

XBANK is the one index above its 50-session average (16,093). Its signal is real, but bank timing is the part that failed in 2022 to 2023.

## Trading it

- XU100 itself has no exchange-traded fund in the lists found. The practical proxy is BIST 30.
- BIST 30: exchange-traded funds (codes ZPX30, APX30, OPX30) and the BIST 30 index future on VIOP.
- Banks: funds on the BIST Liquid Banks index (ZPLIB, APLIB), which is not XBANK itself, and bank-index futures on VIOP.
- Not checked: whether these can be bought from a UK account, fund costs, and how closely each tracks its index.

## What is still unknown

- 2024 to 2026: not looked at.
- Idle cash earns nothing in the lira test and holds dollars in the dollar test. Real lira money-market returns are not modelled.
- Futures carry financing costs that an index-level test does not show.
- Dividends are excluded on both sides.
