# BIST swing-trading research (branch `bist-research`)

Separate from the US scanner on `main`; nothing here touches it.

- `SPEC.md`: the frozen V1 rules and how the code reads them.
- `bist/config.yaml`: every threshold, versioned.
- `bist/fetch.py`: downloads prices, corporate actions and the share list (run on GitHub Actions).
- `bist/core.py`: indicators, setup state machine, trade model, score.
- `bist/scan.py`: end-of-day scan. `bist/backtest.py`: backtest, baseline, benchmark, ledgers.
- `tests/test_bist.py`: unit tests on synthetic bars.

To run everything: change `bist/run_request.txt` on this branch. Data lands on the `bist-data`
branch, results (scan, backtest, ledgers, alert history) on `bist-results`.

There is no daily schedule yet: GitHub only starts scheduled workflows from the default branch.
