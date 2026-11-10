"""Turn the scan result into one self-contained, phone-first HTML page."""
from __future__ import annotations

import math
import re
from datetime import date, datetime

from jinja2 import Environment
from markupsafe import Markup

from .setups import BREAKOUT_PARTS, EP_PARTS


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------
def price(x) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    return f"{x:,.2f}"


def pct(x, digits=1, sign=True) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    s = f"{x * 100:+.{digits}f}%" if sign else f"{x * 100:.{digits}f}%"
    return s.replace("-", "−")


def money(x) -> str:
    if x >= 1e9:
        return f"${x / 1e9:.1f}bn"
    if x >= 1e6:
        return f"${x / 1e6:.0f}m"
    return f"${x:,.0f}"


_SHARE_CLASS = re.compile(
    r"\s+(?:Class [A-Z]\s+)?(?:Common Stock|Common Shares?|Ordinary Shares?|"
    r"American Depositary Shares?|Depositary Shares?|ADS|ADR)\b.*$", re.I)


def company(name: str) -> str:
    """'Grid Dynamics Holdings Inc. Class A Common Stock' -> 'Grid Dynamics Holdings Inc.'"""
    return _SHARE_CLASS.sub("", name or "").strip() or (name or "")


def nice_date(iso: str) -> str:
    d = datetime.strptime(iso, "%Y-%m-%d")
    return f"{d:%A} {d.day} {d:%B %Y}"


def _month(iso: str) -> str:
    return datetime.strptime(iso, "%Y-%m-%d").strftime("%B %Y")


def short_date(iso: str) -> str:
    d = datetime.strptime(iso, "%Y-%m-%d")
    return f"{d.day} {d:%b}"


def join_names(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


# ---------------------------------------------------------------------------
# Charts (inline SVG, coloured by CSS variables so they follow light / dark)
# ---------------------------------------------------------------------------
def candles(ch: dict, trigger: float | None = None, w: int = 640, h: int = 250,
            label: str = "") -> Markup:
    nan = float("nan")
    ch = {k: [nan if x is None else x for x in vals] for k, vals in ch.items()}
    o, hi, lo, c, v = ch["open"], ch["high"], ch["low"], ch["close"], ch["volume"]
    n = len(c)
    ok = [i for i in range(n) if not any(math.isnan(x) for x in (o[i], hi[i], lo[i], c[i]))]
    if not ok:
        return Markup("")
    pad_r = 6
    ph = h * 0.74                      # price pane height
    vh, vtop = h * 0.20, h * 0.80      # volume pane
    pmax = max(max(hi[i] for i in ok), trigger or 0)
    pmin = min(lo[i] for i in ok)
    span = (pmax - pmin) or 1
    pmax, pmin = pmax + span * 0.06, pmin - span * 0.04
    step = (w - pad_r) / n
    bw = max(step * 0.62, 1.5)
    vmax = max((v[i] for i in ok if not math.isnan(v[i])), default=1) or 1

    def y(p):
        return round((pmax - p) / (pmax - pmin) * ph, 1)

    def x(i):
        return round(i * step + step / 2, 1)

    up, down, vup, vdown = [], [], [], []
    for i in ok:
        cx, x0 = x(i), round(x(i) - bw / 2, 1)
        top, bot = y(max(o[i], c[i])), y(min(o[i], c[i]))
        body = max(bot - top, 0.8)
        seg = (f"M{cx} {y(hi[i])}V{y(lo[i])}"
               f"M{x0} {top}h{round(bw, 1)}v{round(body, 1)}h{-round(bw, 1)}z")
        vol = 0 if math.isnan(v[i]) else v[i] / vmax * vh
        vseg = f"M{x0} {round(vtop + vh - vol, 1)}h{round(bw, 1)}v{round(vol, 1)}h{-round(bw, 1)}z"
        (up if c[i] >= o[i] else down).append(seg)
        (vup if c[i] >= o[i] else vdown).append(vseg)

    def line(vals, cls):
        pts = [f"{x(i)},{y(p)}" for i, p in enumerate(vals) if p is not None and not math.isnan(p)]
        return f'<polyline class="{cls}" points="{" ".join(pts)}"/>' if len(pts) > 1 else ""

    parts = [
        f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{label}" preserveAspectRatio="none">',
        line(ch["sma20"], "ma20"), line(ch["sma10"], "ma10"),
        f'<path class="vup" d="{"".join(vup)}"/><path class="vdown" d="{"".join(vdown)}"/>',
        f'<path class="cup" d="{"".join(up)}"/><path class="cdown" d="{"".join(down)}"/>',
    ]
    if trigger:
        ty = y(trigger)
        parts.append(f'<line class="trig" x1="0" x2="{w}" y1="{ty}" y2="{ty}"/>')
    parts.append("</svg>")
    return Markup("".join(parts))


def score_bar(parts: dict, spec: list[tuple]) -> list[dict]:
    return [{"label": label, "max": mx, "got": parts.get(key, 0),
             "fill": round(min(parts.get(key, 0) / mx, 1) * 100)} for key, label, mx in spec]


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Breakout sheet {{ asof_short }}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600&family=Barlow+Condensed:wght@500;600;700&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#E7ECEF; --surface:#FAFBFC; --ink:#14202B; --muted:#566573; --rule:#C9D2D9;
  --up:#0B7F6B; --down:#B8433A; --trigger:#B86E00; --ma10:#2B5FA8; --ma20:#8FA3B8;
  --go:#0B6B5A; --wait:#9C5B00; --halt:#9E3229; --track:#DDE3E8;
}
@media (prefers-color-scheme: dark){
  :root{
    --bg:#18212A; --surface:#212C37; --ink:#E8EDF1; --muted:#9BA9B5; --rule:#374552;
    --up:#3CC2A4; --down:#EC7D72; --trigger:#F0A93A; --ma10:#7FB0F0; --ma20:#5F7386;
    --go:#0E7A66; --wait:#A86400; --halt:#AD3A30; --track:#2C3945;
  }
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 Barlow,"Helvetica Neue",Arial,sans-serif;
  font-variant-numeric:tabular-nums}
a{color:inherit}
a:focus-visible,summary:focus-visible{outline:2px solid var(--trigger);outline-offset:2px}
.wrap{max-width:720px;margin:0 auto;padding:0 16px}
h1,h2,h3,.ticker,.score,.verdict{font-family:"Barlow Condensed","Arial Narrow",Barlow,sans-serif}

.demo{background:var(--ink);color:var(--bg);padding:10px 0;font-weight:500;font-size:.9rem}

.light{color:#fff;padding:26px 0 24px}
.light.green{background:var(--go)} .light.amber{background:var(--wait)} .light.red{background:var(--halt)}
.light .when{margin:0 0 14px;font-size:.95rem;opacity:.9}
.verdict{margin:0;font-size:clamp(2.3rem,10vw,3.6rem);line-height:1;font-weight:700;letter-spacing:-.01em}
.light .do{margin:14px 0 0;font-size:1.06rem;line-height:1.45;max-width:34em}
.light ul{margin:14px 0 0;padding:14px 0 0 1.1em;border-top:1px solid rgba(255,255,255,.35);font-size:.95rem}
.light li{margin:2px 0}

section{margin:28px 0}
h2{font-size:1.75rem;line-height:1.1;font-weight:600;margin:0 0 4px}
.lede{margin:0 0 14px;color:var(--muted);max-width:38em}
.panel{background:var(--surface);border-radius:4px;padding:16px}
.panel + .panel{margin-top:12px}

.chart svg{display:block;width:100%;height:auto;aspect-ratio:64/25}
.chart .cup{fill:var(--up);stroke:var(--up);stroke-width:1}
.chart .cdown{fill:var(--down);stroke:var(--down);stroke-width:1}
.chart .vup{fill:var(--up);opacity:.35} .chart .vdown{fill:var(--down);opacity:.35}
.chart .ma10,.chart .ma20{fill:none;stroke-width:1.6;vector-effect:non-scaling-stroke}
.chart .ma10{stroke:var(--ma10)} .chart .ma20{stroke:var(--ma20)}
.chart .trig{stroke:var(--trigger);stroke-width:1.6;stroke-dasharray:7 5;vector-effect:non-scaling-stroke}
.key{display:flex;flex-wrap:wrap;gap:4px 16px;margin:8px 0 0;padding:0;list-style:none;font-size:.82rem;color:var(--muted)}
.key i{display:inline-block;width:18px;height:0;border-top:2px solid;vertical-align:middle;margin-right:6px}
.key .k10{border-color:var(--ma10)} .key .k20{border-color:var(--ma20)}
.key .kt{border-color:var(--trigger);border-top-style:dashed}

table{width:100%;border-collapse:collapse;font-size:.95rem}
th,td{padding:7px 0;text-align:right;border-bottom:1px solid var(--rule);font-weight:400}
th:first-child,td:first-child{text-align:left}
thead th{color:var(--muted);font-size:.85rem}
tbody tr:last-child td,tbody tr:last-child th{border-bottom:0}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:8px}
.dot.green{background:var(--up)} .dot.amber{background:var(--trigger)} .dot.red{background:var(--down)}
.pos{color:var(--up)} .neg{color:var(--down)}

.facts{display:grid;grid-template-columns:1fr auto;gap:0;margin:0}
.facts dt,.facts dd{padding:7px 0;border-bottom:1px solid var(--rule);margin:0}
.facts dd{text-align:right;font-weight:500}
.facts dt:nth-last-of-type(1),.facts dd:nth-last-of-type(1){border-bottom:0}

.setup header{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}
.ticker{font-size:2.3rem;line-height:.95;font-weight:700;margin:0;letter-spacing:.01em}
.ticker a{text-decoration:none}
.co{margin:4px 0 0;color:var(--muted);font-size:.9rem}
.rating{text-align:right;white-space:nowrap}
.score{font-size:2.3rem;line-height:.95;font-weight:600}
.grade{display:block;font-size:.9rem;color:var(--muted)}
.new{display:inline-block;margin-left:8px;padding:1px 7px;border:1.5px solid var(--trigger);color:var(--trigger);
  border-radius:3px;font:600 .8rem Barlow,sans-serif;vertical-align:middle;letter-spacing:0}
.headline{margin:12px 0 2px;font-weight:600;font-size:1.05rem;line-height:1.35}
.call{margin:0 0 12px;color:var(--muted)}
.plan{display:grid;grid-template-columns:repeat(3,1fr);gap:0;margin:14px 0 0;border-top:1px solid var(--rule);
  border-bottom:1px solid var(--rule)}
.plan div{padding:10px 10px 10px 0}
.plan div + div{border-left:1px solid var(--rule);padding-left:12px}
.plan dt{font-size:.82rem;color:var(--muted)}
.plan dd{margin:0;font:600 1.45rem/1.15 "Barlow Condensed","Arial Narrow",sans-serif}
.plan small{display:block;font:400 .82rem/1.3 Barlow,sans-serif;color:var(--muted)}
.cat{margin:-6px 0 12px;font-size:.95rem}
.oc + .oc{border-top:1px solid var(--rule);margin-top:12px;padding-top:12px}
.oc-h{margin:0;font:600 1.35rem/1.2 "Barlow Condensed","Arial Narrow",sans-serif}
.oc-h a{text-decoration:none}
.oc-b{margin:4px 0 0}
.state{font:500 .95rem Barlow,sans-serif;margin-left:8px}
.state.triggered{color:var(--up)} .state.failed,.state.back_under{color:var(--down)}
.warn{margin:12px 0 0;padding:9px 12px;border-left:3px solid var(--trigger);background:var(--track);font-size:.93rem}
.bar{display:flex;gap:3px;margin:14px 0 0}
.bar span{height:8px;background:var(--track);border-radius:1px;overflow:hidden}
.bar b{display:block;height:100%;background:var(--ink)}
details{margin-top:10px}
summary{cursor:pointer;color:var(--muted);font-size:.93rem}
details ul{margin:10px 0 0;padding-left:1.1em}
details li{margin:3px 0}
.parts{list-style:none;padding:0;margin:10px 0 0;font-size:.93rem}
.parts li{display:flex;justify-content:space-between;border-bottom:1px solid var(--rule);padding:5px 0}
.more{margin:12px 0 0;font-size:.9rem;color:var(--muted)}

td,th{padding-left:10px} td:first-child,th:first-child{padding-left:0}
.groups{table-layout:fixed}
.groups th:first-child{width:38%} .groups td:nth-child(2),.groups thead th:nth-child(2){width:24%}
.groups td:nth-child(n+3),.groups thead th:nth-child(n+3){width:19%;white-space:nowrap}
.gbar{display:block;height:8px;border-radius:1px;background:var(--up)}
.gbar.neg{background:var(--down)}
ol.steps{margin:10px 0 0;padding-left:1.3em}
ol.steps li{margin:6px 0}
footer{margin:32px 0 40px;color:var(--muted);font-size:.86rem}
.empty{color:var(--muted);margin:0}
</style>
</head>
<body>
{% if demo %}<div class="demo"><div class="wrap">Sample report built from invented data. None of these tickers exist.</div></div>{% endif %}

<div class="light {{ regime.state }}"><div class="wrap">
  <p class="when">Breakout sheet for the US close on {{ asof_long }}</p>
  <h1 class="verdict">{{ regime.verdict }}</h1>
  <p class="do">{{ regime.action }}</p>
  <ul>{% for r in regime.reasons %}<li>{{ r }}</li>{% endfor %}{% if light_fact %}<li>{{ light_fact }}</li>{% endif %}</ul>
</div></div>

<div class="wrap">

<section>
  <div class="panel">
    <div class="chart">{{ index_chart }}</div>
    <ul class="key"><li><i class="k10"></i>10-day average</li><li><i class="k20"></i>20-day average</li><li>Nasdaq 100 (QQQ), last 110 sessions</li></ul>
  </div>
  <div class="panel">
    <table>
      <thead><tr><th>Index</th><th>Day</th><th>Week</th><th>vs 20-day</th></tr></thead>
      <tbody>
      {% for t, ix in regime.indexes.items() %}
        <tr><th><span class="dot {{ ix.state }}"></span>{{ index_names[t] }}</th>
        <td class="{{ 'pos' if ix.chg >= 0 else 'neg' }}">{{ pct(ix.chg) }}</td>
        <td class="{{ 'pos' if ix.w1 >= 0 else 'neg' }}">{{ pct(ix.w1) }}</td>
        <td class="{{ 'pos' if ix.vs20 >= 0 else 'neg' }}">{{ pct(ix.vs20) }}</td></tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
  <div class="panel">
    <dl class="facts">
      <dt>Stocks above their 20-day average</dt><dd>{{ "%.0f"|format(breadth.above20) }}%</dd>
      <dt>Stocks above their 50-day average</dt><dd>{{ "%.0f"|format(breadth.above50) }}%</dd>
      <dt>Up 4% or more on rising volume, against down 4%</dt><dd>{{ breadth.up4 }} to {{ breadth.down4 }}</dd>
      <dt>New 12-month highs against new lows</dt><dd>{{ breadth.new_highs }} to {{ breadth.new_lows }}</dd>
      <dt>Recent breakouts still above their breakout close</dt>
      <dd>{% if breadth.followthrough is not none %}{{ "%.0f"|format(breadth.followthrough) }}% of {{ breadth.followthrough_n }}{% else %}too few to judge{% endif %}</dd>
    </dl>
  </div>
</section>

{% if open_lines %}
<section>
  <h2>After the open</h2>
  <p class="lede">Checked at {{ open_check.time_ny }} New York, {{ open_check.minutes_in }} minutes into {{ open_date }}'s session. By then a normal day has traded roughly {{ usual_volume }}% of its volume. Prices can lag by up to 15 minutes.</p>
  <div class="panel">
    {% for r in open_lines.moved %}
    <div class="oc">
      <p class="oc-h"><a href="{{ tv(r.ticker) }}">{{ r.ticker }}</a> <span class="state {{ r.state }}">{{ r.label }}</span></p>
      <p class="oc-b">{{ r.body }}</p>
    </div>
    {% else %}
    <p class="empty">Nothing has traded through its trigger yet.</p>
    {% endfor %}
    {% if open_lines.waiting %}<p class="more">Still waiting: {{ open_lines.waiting }}.</p>{% endif %}
  </div>
</section>
{% endif %}

{% if followup %}
<section>
  <div class="panel">
    From the {{ short_date(followup.date) }} ready list:
    {% if followup.triggered %}<strong>{{ join_names(followup.triggered) }}</strong> traded through {{ 'its' if followup.triggered|length == 1 else 'their' }} trigger{{ '' if followup.triggered|length == 1 else 's' }}.{% else %}nothing triggered.{% endif %}
    {% if followup.waiting %}{{ followup.waiting|length }} still waiting.{% endif %}
  </div>
</section>
{% endif %}

<section>
  <h2>Ready to trigger</h2>
  <p class="lede">Leaders in a tight base within one daily range of the trigger. Set an alert at each trigger price.</p>
  {% if ready %}{% for s in ready %}{{ card(s, 'ready') }}{% endfor %}
  {% else %}<div class="panel"><p class="empty">Nothing is ready today. {% if building %}{{ building|length }} leaders are still building bases further down.{% else %}No leaders are in a base either: the market is not offering this setup right now.{% endif %}</p></div>{% endif %}
</section>

{% if fired %}
<section>
  <h2>Broke out today</h2>
  <p class="lede">These had a valid base yesterday and closed above the trigger on volume today.</p>
  {% for s in fired %}{{ card(s, 'fired') }}{% endfor %}
</section>
{% endif %}

<section>
  <h2>Episodic pivots</h2>
  <p class="lede">Big gaps on a surge of volume, in liquid stocks, that closed within half a daily range of their high. Where the gap came on earnings, the result is shown.</p>
  {% if episodic %}{% for s in episodic %}{{ card(s, 'ep') }}{% endfor %}
  {% else %}<div class="panel"><p class="empty">No qualifying gaps in the last few sessions.</p></div>{% endif %}
</section>

{% if building %}
<section>
  <h2>Still building</h2>
  <p class="lede">Leaders with a base that is not tight enough or not close enough yet. Tomorrow's ready list comes from here.</p>
  <div class="panel">
    <table>
      <thead><tr><th>Stock</th><th>Rating</th><th>Trigger</th><th>Away</th></tr></thead>
      <tbody>
      {% for s in building %}
        <tr><th><a href="{{ tv(s.ticker) }}">{{ s.ticker }}</a></th><td>{{ s.score }} {{ s.grade }}</td>
        <td>{{ price(s.pivot) }}</td><td>{{ pct(s.pivot / s.close - 1, 1, false) }}</td></tr>
      {% endfor %}
      </tbody>
    </table>
    <details><summary>What each one is missing</summary>
      <ul>{% for s in building %}<li><strong>{{ s.ticker }}</strong>: {{ s.headline }}. {{ s.call }}</li>{% endfor %}</ul>
    </details>
  </div>
</section>
{% endif %}

{% if blocked %}
<section><div class="panel">Hidden because {{ 'it is' if blocked|length == 1 else 'they are' }} on your no-trade list: <strong>{{ join_names(blocked) }}</strong>.</div></section>
{% endif %}

<section>
  <h2>Where the momentum is</h2>
  <p class="lede">Sector and theme funds ranked by one-month and three-month gains. Setups in the top groups have the wind behind them.</p>
  <div class="panel">
    <table class="groups">
      <thead><tr><th>Strongest</th><th></th><th>1 mo</th><th>3 mo</th></tr></thead>
      <tbody>
      {% for g in top_groups %}
        <tr><th>{{ g.name }}</th><td><span class="gbar {{ 'neg' if g.m1 < 0 }}" style="width:{{ g.w }}%"></span></td>
        <td>{{ pct(g.m1) }}</td><td>{{ pct(g.m3) }}</td></tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
  <div class="panel">
    <table class="groups">
      <thead><tr><th>Weakest</th><th></th><th>1 mo</th><th>3 mo</th></tr></thead>
      <tbody>
      {% for g in bottom_groups %}
        <tr><th>{{ g.name }}</th><td><span class="gbar {{ 'neg' if g.m1 < 0 }}" style="width:{{ g.w }}%"></span></td>
        <td>{{ pct(g.m1) }}</td><td>{{ pct(g.m3) }}</td></tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
</section>

{% if bt %}
<section>
  <h2>What the backtest found</h2>
  <p class="lede">The scanner was replayed over {{ bt.sessions }} sessions, {{ bt_from }} to {{ bt_to }}, seeing only what was known each evening, and every trigger was traded by the rules below. Results are in R: 1R is the amount risked on a trade.</p>
  <div class="panel">
    <table>
      <thead><tr><th>Ready list, by light</th><th>Trades</th><th>Winners</th><th>Average</th></tr></thead>
      <tbody>
      {% for k in ['green', 'amber', 'red'] %}{% set x = bt.ready_by_light[k] %}
        <tr><th><span class="dot {{ k }}"></span>{{ k|capitalize }}</th><td>{{ x.n }}</td><td>{{ "%.0f"|format(x.win) }}%</td><td class="{{ 'pos' if x.avg_r > 0 else 'neg' }}">{{ rr(x.avg_r) }}</td></tr>
      {% endfor %}
      </tbody>
    </table>
    <p class="more">The light is the part that held up: red lost money in both halves of the period. Even on green the edge is thin, so which setups you take, and what trading costs you, decide the result.</p>
  </div>
  <div class="panel">
    <table>
      <thead><tr><th>Ready list, by rating</th><th>Trades</th><th>Winners</th><th>Average</th></tr></thead>
      <tbody>
      {% for x in bt.ready_by_rating %}
        <tr><th>{{ x.band }}</th><td>{{ x.n }}</td><td>{{ "%.0f"|format(x.win) }}%</td><td class="{{ 'pos' if x.avg_r > 0 else 'neg' }}">{{ rr(x.avg_r) }}</td></tr>
      {% endfor %}
      </tbody>
    </table>
    <p class="more">The rating did not rank the winners. Nor did group strength, strength during pullbacks, base count or distance above the 50-day: a model fitted on the first half of the period could not rank setups in the second. Read the rating as how closely a chart matches the pattern, not as its odds.</p>
  </div>
  <div class="panel">
    <table>
      <thead><tr><th>Gaps, 10 days later</th><th>Count</th><th>Against the Nasdaq</th></tr></thead>
      <tbody>
        <tr><th>Thin or wild stocks</th><td>{{ "{:,}".format(bt.ep.gap_day_thin.n) }}</td><td class="neg">{{ pc(bt.ep.gap_day_thin.vs_market_10d) }}</td></tr>
        <tr><th>The rest</th><td>{{ "{:,}".format(bt.ep.gap_day_clean.n) }}</td><td class="{{ 'pos' if bt.ep.gap_day_clean.vs_market_10d > 0 else 'neg' }}">{{ pc(bt.ep.gap_day_clean.vs_market_10d) }}</td></tr>
      </tbody>
    </table>
    <p class="more">Thin or wild means under $5 before the gap, a daily range of 8% or more, a gap of 50% or more, or under $5m traded a day. Those are now left out. Among the rest, gaps that closed within half a daily range of their high averaged {{ rr(bt.ep.trades_clean_near_high.avg_r) }} on {{ bt.ep.trades_clean_near_high.n }} trades and the others {{ rr(bt.ep.trades_clean_far.avg_r) }}, so that is now required.</p>
  </div>
  <div class="panel">
    <details><summary>Rules used, and the limits of the test</summary>
      <ul>
        <li>Entry the next session if price trades through the trigger, at the trigger or at the open if it gaps above. {{ "%.0f"|format(bt.trigger_next_day) }}% of ready setups triggered the next day and {{ "%.0f"|format(bt.trigger_in_5) }}% within five.</li>
        <li>Stop between 0.4 and 1.0 daily ranges below the entry. Half sold on the first profitable close from day three, the rest on the first close under the 10-day average.</li>
        <li>Seven other exit rules were tried, from fixed targets to a 20-day trail. None changed the picture: on green they ranged from {{ rr(exit_lo) }} to {{ rr(exit_hi) }}.</li>
        <li>Daily bars cannot show whether the low or the breakout came first on the entry day ({{ "%.0f"|format(bt.ambiguous_pct) }}% of trades). Counting all of those as losses takes green to {{ rr(bt.ready_by_light.green.avg_r_pess) }}.</li>
        <li>Only stocks listed today are included, which flatters the results, and no trading costs are counted.</li>
      </ul>
    </details>
  </div>
</section>
{% endif %}

<section>
  <h2>How to trade this sheet</h2>
  <div class="panel">
    <ol class="steps">
      <li>Read the light first. Red means no new breakouts, whatever the ratings say: it is the one filter the backtest supports.</li>
      <li>Before the US open, set a TradingView alert at each trigger on the ready list.</li>
      <li>When an alert fires, buy the break of the opening range high (1, 5 or 60-minute chart). Stop at the low of the day.</li>
      <li>If the low of the day is more than one average daily range below your entry, skip it. The sizes here assume that limit.</li>
      <li>After three to five days, sell a third to a half and move the stop to your entry price.</li>
      <li>Exit the rest on the first close below the 10-day average (the 20-day for slower stocks).</li>
    </ol>
    <details><summary>How the ratings are built</summary>
      <ul class="parts">
        {% for key, label, mx in breakout_parts %}<li><span>{{ label }}</span><span>{{ mx }} points</span></li>{% endfor %}
      </ul>
      <p class="more">Breakouts score out of 100. A+ is 85 or more, A is 75, B is 62. Leadership is the stock's rank among all liquid stocks over one, three or six months plus the size of its run. Tightness compares the last five days' ranges with the ranges during the run.</p>
      <ul class="parts">
        {% for key, label, mx in ep_parts %}<li><span>{{ label }}</span><span>{{ mx }} points</span></li>{% endfor %}
      </ul>
      <p class="more">Episodic pivots are scored separately. Most of the weight is on how close the stock is to its high, because that is what separated the gaps that worked in the backtest. Gap size and volume surge did not.</p>
      <p class="more">Sizes use an account of {{ money(account.size_usd) }}, {{ account.risk_per_trade_pct }}% risk per trade (halved on amber) and a {{ account.max_position_pct }}% cap per position. Change them in config.yaml.</p>
    </details>
  </div>
</section>

<footer>
  Scanned {{ "{:,}".format(counts.scanned) }} stocks; {{ "{:,}".format(counts.liquid) }} passed the price and volume filters and {{ counts.leaders }} qualified as leaders.
  Stock list from {{ health.stock_list }}{% if health.missing %}; {{ health.missing }} tickers had no price data{% endif %}. Built {{ generated }}.
  This is a screening tool, not advice. Check every chart yourself before trading.
</footer>

</div>
</body>
</html>
"""

CARD = r"""
<article class="panel setup">
  <header>
    <div>
      <h3 class="ticker"><a href="{{ tv(s.ticker) }}">{{ s.ticker }}</a>{% if s.new %}<span class="new">New</span>{% endif %}</h3>
      <p class="co">{{ company(s.name) }}{% if s.industry %}{{ ', ' if s.name }}{{ s.industry }}{% endif %}</p>
    </div>
    <div class="rating"><span class="score">{{ s.score }}</span><span class="grade">grade {{ s.grade }}</span></div>
  </header>
  <p class="headline">{{ s.headline }}</p>
  <p class="call">{{ s.call }}</p>
  {% if s.catalyst_text %}<p class="cat">{{ s.catalyst_text }}</p>{% endif %}
  <div class="chart">{{ chart }}</div>
  <ul class="key"><li><i class="k10"></i>10-day</li><li><i class="k20"></i>20-day</li><li><i class="kt"></i>Trigger {{ price(s.pivot) }}</li></ul>
  <dl class="plan">
    {% if kind == 'fired' %}
    <div><dt>Closed at</dt><dd>{{ price(s.close) }}<small>trigger was {{ price(s.pivot) }}</small></dd></div>
    <div><dt>Stop, today's low</dt><dd>{{ price(s.plan.stop) }}<small>{{ s.plan.risk_pct }}% below the close</small></dd></div>
    {% else %}
    <div><dt>Buy above</dt><dd>{{ price(s.plan.entry) }}<small>last close {{ price(s.close) }}</small></dd></div>
    <div><dt>Planned stop</dt><dd>{{ price(s.plan.stop) }}<small>{{ s.plan.risk_pct }}% risk per share</small></dd></div>
    {% endif %}
    <div><dt>Size</dt>
      {% if s.plan.shares > 0 %}<dd>{{ s.plan.shares }}<small>shares, {{ s.plan.position_pct }}% of account, {{ money(s.plan.risk_usd) }} at risk</small></dd>
      {% else %}<dd>None<small>{{ s.plan.no_size_reason or ('red light' if regime.state == 'red' else 'one share already risks more than the limit') }}</small></dd>{% endif %}
    </div>
  </dl>
  {% if s.plan.capped %}<p class="warn">Size is held at the {{ account.max_position_pct }}% position cap, so less than the usual amount is at risk.</p>{% endif %}
  {% if s.earnings_note %}<p class="warn">{{ s.earnings_note }}</p>{% endif %}
  <div class="bar" aria-hidden="true">{% for p in bar %}<span style="flex:{{ p.max }}" title="{{ p.label }}"><b style="width:{{ p.fill }}%"></b></span>{% endfor %}</div>
  <details><summary>Why it rates {{ s.score }}</summary>
    <ul>{% for w in s.why %}<li>{{ w }}</li>{% endfor %}</ul>
    <ul class="parts">{% for p in bar %}<li><span>{{ p.label }}</span><span>{{ "%.0f"|format(p.got) }} of {{ p.max }}</span></li>{% endfor %}</ul>
    <p class="more">{% if s.rs is not none %}Stronger than {{ "%.0f"|format([s.rs, 99]|min) }}% of stocks. {% endif %}Average daily range {{ "%.1f"|format(s.adr_pct) }}%. Trades {{ money(s.dollar_vol) }} a day.</p>
  </details>
</article>
"""


def _earnings_note(s: dict, asof: str) -> str | None:
    e = s.get("earnings")
    if not e:
        return None
    days = (date.fromisoformat(e) - date.fromisoformat(asof)).days
    if days < 0 or days > 10:
        return None
    when = "today" if days == 0 else "tomorrow" if days == 1 else f"in {days} days"
    return (f"Reports earnings {when} ({short_date(e)}). A fresh breakout has no cushion to hold "
            "through that: trade it small or wait for the report.")


def rr(x) -> str:
    """A result in R: +0.08R, −0.19R."""
    if x is None:
        return "n/a"
    return f"{x:+.2f}R".replace("-", "\u2212")


def pc(x) -> str:
    return f"{x:+.0f}%".replace("-", "\u2212")


def usual_volume(minutes_in: int) -> int:
    """Rough share of a normal day's volume traded this many minutes after the open
    (volume is heaviest at the open and the close)."""
    pts = [(0, 0), (30, 13), (60, 21), (90, 27), (120, 33), (195, 45), (390, 100)]
    for (m0, v0), (m1, v1) in zip(pts, pts[1:]):
        if minutes_in <= m1:
            return round((v0 + (v1 - v0) * (minutes_in - m0) / (m1 - m0)) / 5) * 5
    return 100


def open_lines(chk: dict | None, account: dict) -> dict | None:
    """Plain sentences for the after-open check."""
    if not chk or not chk.get("rows"):
        return None
    moved, waiting = [], []
    for r in chk["rows"]:
        vol = (f" {r['vol_pct']}% of a normal day's volume so far."
               if r.get("vol_pct") is not None else "")
        if r["state"] == "waiting":
            gap = -r["vs_trigger"]
            waiting.append(f"{r['ticker']} ({pct(gap, 1, sign=False)} below {price(r['trigger'])})")
            continue
        if r["state"] == "triggered":
            label = f"Triggered at {r['time']}"
            body = (f"Now {price(r['now'])}, {pct(r['vs_trigger'], 1, sign=False)} above the "
                    f"trigger of {price(r['trigger'])}. ")
            if r["too_wide"]:
                body += (f"The low of the day is {price(r['stop'])}, {r['risk_pct']}% below the "
                         "entry: wider than one daily range, so skip it.")
            elif r["shares"] > 0:
                body += (f"Stop at the low of the day, {price(r['stop'])} ({r['risk_pct']}% risk): "
                         f"{r['shares']} shares, {r['position_pct']}% of the account.")
            elif chk.get("regime") == "red" and r["setup"] != "ep":
                body += f"Stop would be the low of the day, {price(r['stop'])}. No size on a red light."
            else:
                body += (f"Stop would be {price(r['stop'])}, but one share already risks more "
                         "than the limit.")
            if r.get("extended", 0) > 1 and not r["too_wide"]:
                body += f" Already {r['extended']:.1f} daily ranges above the entry: late."
        elif r["state"] == "back_under":
            label = f"Triggered at {r['time']}, now back under"
            body = (f"Now {price(r['now'])}, below the trigger of {price(r['trigger'])}. "
                    f"Low of the day {price(r['low'])}.")
        else:
            label = "Failed"
            body = (f"Triggered at {r['time']} and has since broken the low it was stopped "
                    f"against, {price(r['stop'])}. Now {price(r['now'])}.")
        moved.append({"ticker": r["ticker"], "state": r["state"], "label": label, "body": body + vol})
    return {"moved": moved, "waiting": ", ".join(waiting)}


def page(p: dict) -> str:
    env = Environment(autoescape=True, trim_blocks=True, lstrip_blocks=True)
    helpers = dict(price=price, pct=pct, money=money, company=company, rr=rr, pc=pc, short_date=short_date, join_names=join_names,
                   tv=lambda t: f"https://www.tradingview.com/chart/?symbol={t.replace('-', '.')}")
    env.globals.update(helpers)
    card_t = env.from_string(CARD)

    def card(s: dict, kind: str) -> Markup:
        spec = EP_PARTS if s["setup"] == "ep" else BREAKOUT_PARTS
        s = {**s, "earnings_note": _earnings_note(s, p["asof"])}
        chart = candles(s["_chart"], s["pivot"], label=f"{s['ticker']} daily chart with trigger line")
        return Markup(card_t.render(s=s, kind=kind, chart=chart, bar=score_bar(s["parts"], spec),
                                    regime=p["regime"], account=p["account"]))

    groups = p["groups"]
    scale = max((abs(g["m1"]) for g in groups), default=1) or 1
    for g in groups:
        g["w"] = round(abs(g["m1"]) / scale * 100)
    from .config import INDEXES
    bt = p.get("backtest")
    extra = {"bt": bt, "light_fact": None}
    if bt:
        x = bt["ready_by_light"][p["regime"]["state"]]
        extra["light_fact"] = (f"In the backtest, ready-list breakouts taken on {p['regime']['state']} "
                               f"averaged {rr(x['avg_r'])} over {x['n']} trades.")
        greens = [e["ready_green"] for e in bt["exit_rules"]]
        extra.update(exit_lo=min(greens), exit_hi=max(greens),
                     bt_from=_month(bt["first"]), bt_to=_month(bt["last"]))
    return env.from_string(TEMPLATE).render(
        **extra, ep_parts=EP_PARTS,
        **{k: v for k, v in p.items() if not k.startswith("_")},
        card=card, asof_long=nice_date(p["asof"]), asof_short=short_date(p["asof"]),
        open_lines=open_lines(p.get("open_check"), p["account"]),
        usual_volume=usual_volume(p["open_check"]["minutes_in"]) if p.get("open_check") else 0,
        open_date=nice_date(p["open_check"]["date"]).split(" ")[0] if p.get("open_check") else "",
        index_chart=candles(p["_index_chart"], None, h=230, label="Nasdaq 100 daily chart"),
        index_names=INDEXES, top_groups=groups[:6], bottom_groups=groups[-3:],
        breakout_parts=BREAKOUT_PARTS,
    )
