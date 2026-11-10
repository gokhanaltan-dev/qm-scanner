"""Settings: defaults here, overridden by config.yaml in the repo root."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SITE = ROOT / "site"

DEFAULTS = {
    "account": {"size_usd": 20000, "risk_per_trade_pct": 0.5, "max_position_pct": 20},
    "no_trade": [],
    "universe": {
        "min_price": 5,
        "min_dollar_volume": 10e6,
        "min_adr_pct": 4.0,
        "min_rs_rank": 93,
    },
    "breakout": {
        "min_leg_gain_pct": 30,
        "min_base_days": 4,
        "max_base_days": 40,
        "max_retrace": 0.5,
    },
    "episodic_pivot": {"min_gap_pct": 8, "min_volume_multiple": 2.5, "lookback_days": 4,
                       "max_adr_from_high": 0.5, "min_pre_price": 5.0, "max_prior_adr_pct": 8.0,
                       "max_gap_pct": 50.0, "min_pre_dollar_volume": 5e6},
    "report": {"max_ready": 15, "max_building": 15, "max_episodic": 10, "min_score": 55},
}

# Market regime instruments
INDEXES = {"QQQ": "Nasdaq 100", "SPY": "S&P 500", "IWM": "Russell 2000"}

# Groups ranked in the "where the momentum is" strip
GROUPS = {
    "XLK": "Technology", "XLC": "Communication", "XLY": "Consumer discretionary",
    "XLF": "Financials", "XLI": "Industrials", "XLE": "Energy", "XLB": "Materials",
    "XLV": "Health care", "XLP": "Consumer staples", "XLU": "Utilities", "XLRE": "Real estate",
    "SMH": "Semiconductors", "IGV": "Software", "CIBR": "Cybersecurity", "BOTZ": "Robotics & AI",
    "XBI": "Biotech", "IHI": "Medical devices", "ARKK": "Innovation growth", "IPO": "Recent IPOs",
    "XHB": "Homebuilders", "XRT": "Retail", "JETS": "Airlines", "KRE": "Regional banks",
    "XOP": "Oil & gas producers", "OIH": "Oil services", "URA": "Uranium", "TAN": "Solar",
    "LIT": "Lithium & batteries", "GDX": "Gold miners", "SIL": "Silver miners",
    "COPX": "Copper miners", "XME": "Metals & mining", "ITA": "Aerospace & defence",
    "BLOK": "Crypto & blockchain", "KWEB": "China internet",
}

HISTORY_DAYS = 460  # calendar days of daily bars to download (~310 sessions)


def _merge(base: dict, over: dict) -> dict:
    out = deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        elif v is not None:
            # numbers typed as text in the YAML (e.g. 10e6) still work
            if isinstance(out.get(k), (int, float)) and not isinstance(v, bool):
                v = float(v)
                if isinstance(out[k], int) and v.is_integer():
                    v = int(v)  # day counts stay whole numbers
            out[k] = v
    return out


def load(path: Path | None = None) -> dict:
    path = path or ROOT / "config.yaml"
    user = {}
    if path.exists():
        user = yaml.safe_load(path.read_text()) or {}
    cfg = _merge(DEFAULTS, user)
    cfg["no_trade"] = sorted({str(t).upper().strip() for t in cfg.get("no_trade") or []})
    return cfg
