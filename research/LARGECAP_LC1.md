# BIST large-cap swing strategy LC-1 (10 November 2026)

Banks, a BIST30-like group and a BIST100-like group. Rules version `bist-lc1-candidate`.
Research only; nothing places orders.

- Code: https://github.com/gokhanaltan-dev/qm-scanner/blob/bist-research/bist/largecap.py
- Every candidate and variant tried: `research/largecap_*.csv` on the same branch.

## Verdict

1. **LC-1 made money per trade in both test periods and kept drawdowns small**, but it did not beat simply holding the index. It is invested about a third of the time; the index is invested all of the time.
2. **It says stand aside today.** XU100 is below its 50-session average, so no new entries are allowed.
3. **Banks are the one strong group right now**, and GARAN and YKBNK sit just under their highs. They still fail the leader filter, so they are a watch list, not setups.
4. **Nothing tested comes close to doubling capital in a year.** The best stretch was +135% over two years, in a market that rose 288%.
5. **2024 to 2026 has not been looked at.**

## The groups

Index membership history is not in the data, so each group is rebuilt every session from what was known then.

| Group | Definition | Stands in for |
| --- | --- | --- |
| L100 | 100 largest by market value among the 200 most liquid shares | BIST100 |
| L30 | 30 most traded shares inside L100 | BIST30 |
| Banks | Listed banks among the 200 most liquid shares (8 to 11 names) | Bank index |

At the end of 2021 L30 was a recognisable BIST30 list. Today it also contains KTLEV, which the official index would not: treat the groups as approximations.

## LC-1 rules

| Part | Rule |
| --- | --- |
| Universe | L100 (includes the banks and L30) |
| Market filter | New setups only while XU100 closes above its 50-session average. Armed setups are cancelled when it fails. |
| Leader filter | Close > SMA50 > SMA200, SMA50 rising, relative strength in the top 20% of L100 |
| Setup | Ascending triangle, flat base, VCP or bull flag (definitions in the V2 doc); close within 1 ATR and 5% below the pivot |
| Entry | Buy-stop at the pivot for up to 10 sessions. Skip a day that opens above min(pivot x 1.03, pivot + 0.75 ATR). |
| Stop | 10-session low minus 0.25 ATR |
| Exit | Next open after a close below SMA50, or after 60 sessions |
| Size | 1% of equity at risk per trade, at most 15% of equity per position, 10 positions, 3 per sector |

Changes from the V2 candidate: large caps only, the market filter, and the SMA50 exit in place of EMA20.

## Results

Net of 25 basis points per side. Idle cash earns nothing. Lira returns.

| | 2016 to 2021 (designed on) | 2022 to 2023 (one look) |
| --- | --- | --- |
| LC-1 return per year | 10.5% | 53.6% |
| LC-1 worst fall | -14% | -7% |
| LC-1 average invested | 28% | 39% |
| XU100 return per year | 17.5% | 97.9% |
| XU100 worst fall | -32% | -22% |
| Equal-weight L100 per year (no costs) | 28.0% | 100.3% |
| Signals / trades taken | 211 / 150 | 96 / 64 |
| Winners | 42% | 58% |
| Mean R per signal | +0.70 | +1.52 |
| 90% range of mean R | +0.32 to +1.06 | +0.73 to +2.26 |
| Return per trade | +6.0% | +21.9% |
| Average hold, sessions | 33 | 43 |

- LC-1 by calendar year, portfolio: 2016 +3%, 2017 +46%, 2018 -5%, 2019 +5%, 2020 +20%, 2021 -1%. Two good years carried the first period.
- By group, mean R (2016 to 2021 / 2022 to 2023): L30 +0.65 / +2.04; rest of L100 +0.72 / +1.27; banks +0.98 on 17 trades / 3 trades only.
- At 50 basis points per side: +0.64R and +1.48R. Without the best five trades in 2016 to 2021: +0.41R.
- In US dollars 2022 to 2023: LC-1 +3% a year, XU100 +33% a year.
- 55 signals in 2016 to 2021 were skipped because the liquidity cap or cash left too small a position; 29 in 2022 to 2023 because the portfolio was full.

**Why it trails the index.** About two-thirds of the account sits in cash, and the test pays that cash nothing. Each 10 points of annual yield on idle lira would add roughly 6 to 7 points a year to LC-1. Lira deposit and money-market rates were far above zero throughout, so the test understates a real account; by how much is not measured.

## What was tried and dropped (2016 to 2021 only)

| Candidate | Result | Why dropped |
| --- | --- | --- |
| Weekly relative-strength rotation, L100 top 10 | 20.0% a year, worst fall -41% | Worse than equal-weight L100 on both counts |
| Rotation, L30 top 5 | 1.3% a year, worst fall -56% | Failed outright; equal-weight L30 made 23% |
| Rotation, banks top 3 | 17.7% a year, worst fall -59% | Drawdown |
| Rotation with an XU100 200-session filter | Lower return in L100 and banks, no real change in L30 | Filter cost more than it saved |
| Pullback to EMA20 in an uptrend, L100 | +0.06R, 25% winners | No edge; in banks only 10% of pullback trades won |
| Bank-only breakouts | +0.25R on 90 signals, 7% invested | Too few trades to run on their own |
| Leader near its 55-session high, no pattern | +0.28R, 12.0% a year, worst fall -17% | Kept as the comparator; made 57.8% a year in 2022 to 2023 against LC-1's 53.6% |

Variants on the breakout rules (16 combinations): the market filter raised mean R in 4 of 4 pairs; the SMA50 exit raised return per trade in 4 of 4; doubling risk to 2% per trade raised the drawdown more than the return. LC-1 was the variant with the best return-to-drawdown ratio and was committed before 2022 to 2023 was looked at.

## Today (session of 10 November 2026)

- **Market filter: off.** XU100 11,661.86 against a 50-session average of 12,517.5. Last on: 14 September 2026.
- 9 of the 30 L30 shares are above their own 50-session average.
- Four L100 shares pass the leader filter (AHGAZ, ENERY, ENJSA, RYGYO). None is a bank or in L30.

Banks are stronger than the market: the bank index was up 12.1% over 63 sessions to 9 November while XU100 was down 14.5%.

| Bank | Close | Below 55-session high | Relative strength in L100 | Fails on |
| --- | --- | --- | --- | --- |
| GARAN | 155.40 | 0.3% (155.90) | 94 | SMA50 131.94 below SMA200 134.38 |
| YKBNK | 38.48 | 1.6% (39.10) | 86 | SMA50 below SMA200 |
| AKBNK | 70.70 | 7.3% (76.30) | 78 | SMA50 below SMA200, not rising; RS under 80 |
| ISCTR | 13.66 | 7.4% (14.75) | 84 | SMA50 below SMA200 |
| VAKBN | 33.48 | 12.1% (38.08) | 89 | SMA50 below SMA200 |
| HALKB | 43.74 | 19.5% (54.35) | 95 | Close below SMA50 |
| TSKB | 10.45 | 10.8% (11.72) | 65 | SMA50 below SMA200, RS under 80 |
| ALBRK | 8.60 | 7.5% (9.30) | 81 | SMA50 not rising |

Under LC-1 none of these is tradable now. GARAN would need its 50-session average above its 200, and XU100 back above its own 50-session average. Whether a sector-level filter (bank index instead of XU100) is better is an untested idea, not part of LC-1.

## What is still unknown

- 2024 to 2026: not looked at.
- Delisted shares are missing, which flatters every figure. Large caps are less affected than small caps.
- The groups approximate the indices; official constituent history was not available.
- Lira results through very high inflation; cash yield not modelled.
- Stop-order fills assume 0.05 ATR slippage on top of costs.
- LC-1 was the best of about 30 large-cap candidates and variants on 2016 to 2021. Some of that first-period result is selection; 2022 to 2023 is the cleaner read, and it was an exceptional bull market.
