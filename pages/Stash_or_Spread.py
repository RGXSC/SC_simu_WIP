"""'Where should the stock sit?' — a teaching page for non-SC people.

One lesson: dumping all your stock into stores on day 1 leaves it stuck
in the low-selling store and missing in the high-selling one. Keep some
central and the warehouse feeds whoever is actually selling.

UI: two side-by-side animated panels (one per policy). Warehouse sits on
top, the two stores below; weekly shipments appear as little flying
packages between them. Inputs: forecast/wk, sell-through %, high-selling
store share %, actual demand/wk, and the lever — % kept central on day 1.
"""
from __future__ import annotations
import json
import streamlit as st

from sim_stash import simulate, totals, WEEKS
from ui_nav import top_nav

st.set_page_config(layout="wide", page_title="Where should the stock sit?",
                   page_icon="\U0001F4E6")

st.markdown("""
<style>
.block-container { padding-top: 1.4rem; max-width: 1400px; }
section[data-testid="stSidebar"] { width: 0 !important; min-width: 0 !important; }
</style>
""", unsafe_allow_html=True)

top_nav("pages/Stash_or_Spread.py")

st.markdown(
    "<h1 style='margin:0 0 4px 0; font-size:28px;'>\U0001F4E6 "
    "Where should the stock sit?</h1>",
    unsafe_allow_html=True,
)
st.markdown(
    "<div style='color:#5a6a80; font-size:14px; margin-bottom:14px;'>"
    "Same stock bought. Same stores. Same demand. The only difference is "
    "<b>where the stock starts</b> on day 1.</div>",
    unsafe_allow_html=True,
)

# ── Inputs ────────────────────────────────────────────────────────────────
c1, c2, c3, c4 = st.columns(4)
with c1:
    forecast_per_week = st.number_input(
        "Forecast (units / week)",
        min_value=20, max_value=500, value=100, step=10,
        help="What you THINK weekly sales will be — used to decide how much "
             "stock to buy for the 26-week season.",
    )
with c2:
    target_sell_through = st.slider(
        "Target sell-through for buy (%)",
        min_value=10, max_value=100, value=80, step=5,
        help="How much of the bought stock you aim to actually sell — this sets "
             "how much you buy. 100% = buy exactly the forecast. 50% = buy double "
             "the forecast (you only expect to sell half).",
    )
with c3:
    big_share_pct = st.slider(
        "High-selling store share of demand (%)",
        min_value=50, max_value=95, value=75, step=5,
        help="How uneven the two stores are. 50% = identical stores. "
             "95% = the low-selling store barely sells.",
    )
with c4:
    actual_per_week = st.slider(
        "Actual demand (units / wk)",
        min_value=20, max_value=400, value=100, step=10,
        help="What ACTUALLY happens — flat each week. Try setting it above or "
             "below the forecast to see what under/over-buying looks like.",
    )

# Money parameters (second row)
m1, m2, m3, m4 = st.columns(4)
with m1:
    price = st.slider(
        "Selling price (€ / unit)",
        min_value=1000, max_value=5000, value=2000, step=200,
        help="What each unit sells for.",
    )
with m2:
    var_cost = st.slider(
        "Cost of goods sold (€ / unit)",
        min_value=200, max_value=2000, value=1000, step=100,
        help="What each unit costs you to buy.",
    )
with m3:
    fix_pct = st.slider(
        "Fixed cost (% of forecast sales)",
        min_value=10, max_value=70, value=30, step=5,
        help="Overheads (rent, staff, …) as a % of the total sales value you "
             "forecast over the 26 weeks. Same for both scenarios.",
    )
with m4:
    hold_pct = st.slider(
        "**% kept central on day 1** \U0001F441 (the lever)",
        min_value=0, max_value=100, value=30, step=5,
        help="0% = all stock dumped to stores on day 1. "
             "100% = everything kept central. The truth is in between.",
    )

big_share   = big_share_pct / 100.0
forecast    = forecast_per_week * WEEKS
# Buy is set by the target sell-through: bought = forecast / target.
# 100% target -> buy the forecast; 50% target -> buy double the forecast.
bought      = int(round(forecast * 100 / target_sell_through))
big_rate    = int(round(actual_per_week * big_share))
small_rate  = int(round(actual_per_week - big_rate))  # mass-conservative
actual_total = actual_per_week * WEEKS

# Fixed cost = a % of the total sales value forecast over the season.
forecast_sales_value = forecast * price
fixed_cost = fix_pct / 100.0 * forecast_sales_value

st.caption(
    f"Forecast season total = **{forecast:,}** units "
    f"(€{forecast_sales_value:,.0f} of forecast sales) · "
    f"You buy **{bought:,} units** at €{var_cost:,.0f} each = "
    f"€{bought * var_cost:,.0f} of stock · Fixed cost €{fixed_cost:,.0f}. "
    f"High-selling store sells **{big_rate}/wk**, Low-selling store sells **{small_rate}/wk** "
    f"(actual season total = **{actual_total:,}**)."
)

# ── Run the two policies ──────────────────────────────────────────────────
dump_states = simulate(bought, hold_pct=0.0,
                       big_rate=big_rate, small_rate=small_rate)
hold_states = simulate(bought, hold_pct=hold_pct / 100.0,
                       big_rate=big_rate, small_rate=small_rate)

dump_tot = totals(dump_states, price=price, var_cost=var_cost)
hold_tot = totals(hold_states, price=price, var_cost=var_cost)

# ── Full P&L on top, one column per scenario ──────────────────────────────
# ONE definition of margin, used identically in the cards AND the animation:
#   margin = sales − stock cost − fixed cost
#   sales      = units actually sold × price
#   stock cost = ALL units bought × cost   (you paid for the lot; leftover
#                stock is money you didn't get back)
#   fixed cost = scenario-independent overhead
stock_cost = bought * var_cost
dump_margin = dump_tot["turnover"] - stock_cost - fixed_cost
hold_margin = hold_tot["turnover"] - stock_cost - fixed_cost


def _row(label, value):
    return (
        f"<div style='display:flex; justify-content:space-between; font-size:13px; "
        f"color:#5a6a80; padding:2px 0;'><span>{label}</span>"
        f"<b style='color:#1a2a40;'>{value}</b></div>"
    )


def _pnl_card(title, t, margin, accent):
    turnover     = t["turnover"]
    sold         = t["sold"]
    lost         = t["lost"]
    mpct         = (margin / turnover * 100.0) if turnover else 0.0
    mcol         = "#1a8a4a" if margin >= 0 else "#c0392b"
    sell_through = (sold / bought * 100.0) if bought else 0.0
    sales_str    = f"€{turnover:,.0f}  ({sold:,} sold)"
    cost_str     = f"€{stock_cost:,.0f}"
    fixed_str    = f"€{fixed_cost:,.0f}"
    lost_str     = f"{lost:,} units"
    st_str       = f"{sell_through:.0f}%"
    return (
        f"<div style='flex:1; background:#fff; border:1px solid #e6ecf2; "
        f"border-top:3px solid {accent}; border-radius:10px; padding:12px 16px;'>"
        f"<div style='font-size:12px; font-weight:700; text-transform:uppercase; "
        f"letter-spacing:.5px; color:{accent}; margin-bottom:8px;'>{title}</div>"
        f"{_row('Sales (turnover)', sales_str)}"
        f"{_row('Stock cost (all bought)', cost_str)}"
        f"{_row('Fixed cost', fixed_str)}"
        f"{_row('Lost sales', lost_str)}"
        f"{_row('Sell-through', st_str)}"
        f"<div style='display:flex; justify-content:space-between; align-items:baseline; "
        f"font-size:15px; padding-top:6px; margin-top:4px; "
        f"border-top:1px dashed #ecf0f4;'><span style='color:#5a6a80;'>Margin</span>"
        f"<b style='font-size:20px; color:{mcol};'>€{margin:,.0f} "
        f"<span style='font-size:14px;'>({mpct:.0f}%)</span></b></div>"
        f"</div>"
    )


# Headline delta
delta = hold_margin - dump_margin
delta_colour = "#1a8a4a" if delta >= 0 else "#c0392b"
delta_label = "earned" if delta >= 0 else "LOST"

st.markdown(
    f"<div style='display:flex; gap:14px; margin:14px 0 6px;'>"
    f"{_pnl_card('Dump everything to stores', dump_tot, dump_margin, '#c0392b')}"
    f"{_pnl_card(f'Keep {hold_pct}% central', hold_tot, hold_margin, '#1a8a4a')}"
    f"</div>",
    unsafe_allow_html=True,
)
st.markdown(
    f"<div style='text-align:center; font-size:14px; color:#5a6a80; margin:0 0 16px;'>"
    f"Keeping {hold_pct}% central vs dumping everything: "
    f"<b style='font-size:22px; color:{delta_colour};'>"
    f"{delta_label} €{abs(delta):,.0f}</b> of margin</div>",
    unsafe_allow_html=True,
)

# Convert states → JSON for the animation component
def _states_to_json(states):
    return [
        {
            "wh": s.wh, "big": s.big, "small": s.small,
            "soldB": s.sold_big, "soldS": s.sold_small,
            "lostB": s.lost_big, "lostS": s.lost_small,
            "inB":  s.in_transit_big, "inS": s.in_transit_small,
        }
        for s in states
    ]

# Cumulative lost per panel, per week (for the running "missed" badge)
def _cum_lost(states):
    cum_b = cum_s = 0
    out = []
    for s in states:
        cum_b += s.lost_big; cum_s += s.lost_small
        out.append({"B": cum_b, "S": cum_s})
    return out

# Cumulative units SOLD per week — the single source of truth for sales/margin
# in the animation (never inferred from inventory, which can mislead).
def _cum_sold(states):
    c = 0
    out = []
    for s in states:
        c += s.sold_big + s.sold_small
        out.append(c)
    return out

# Tank heights are scaled to the largest value across BOTH panels so the two
# sides remain visually comparable.
peak = max(
    max(s.wh for s in dump_states + hold_states),
    max(s.big for s in dump_states + hold_states),
    max(s.small for s in dump_states + hold_states),
    100,
)

payload = {
    "dump":     _states_to_json(dump_states),
    "hold":     _states_to_json(hold_states),
    "cumDump":  _cum_lost(dump_states),
    "cumHold":  _cum_lost(hold_states),
    "soldDump": _cum_sold(dump_states),
    "soldHold": _cum_sold(hold_states),
    "peak":     peak,
    "weeks":    WEEKS,
    "bigRate":  big_rate,
    "smallRate": small_rate,
    "holdPct":  hold_pct,
    "bought":   bought,
    "price":    price,
    "vc":       var_cost,
    "fixed":    fixed_cost,
}

# ── Animation component ───────────────────────────────────────────────────
HTML = """
<!doctype html>
<html><head><meta charset='utf-8'>
<style>
  body { margin: 0; font-family: Inter, Calibri, sans-serif; color:#1a2a40; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
  .panel {
    background: #fff; border: 1px solid #e6ecf2; border-radius: 10px;
    padding: 14px 16px 16px; box-shadow: 0 1px 2px rgba(0,0,0,.04);
  }
  .panel h2 {
    margin: 0 0 4px; font-size: 15px;
    text-transform: uppercase; letter-spacing: .5px;
  }
  .panel.dump h2 { color: #c0392b; }
  .panel.hold h2 { color: #1a8a4a; }
  .panel .sub { color:#5a6a80; font-size: 11.5px; margin-bottom: 10px; }

  /* Vertical topology: warehouse on top, two stores below */
  .topo {
    position: relative;
    width: 100%;
    height: 340px;
  }
  .flow-svg {
    position: absolute; inset: 0;
    width: 100%; height: 100%;
    pointer-events: none;
    z-index: 1;
  }
  .flow-svg line {
    stroke: #c5d2e0;
    stroke-width: 2;
    stroke-dasharray: 4 4;
  }
  .node {
    position: absolute;
    z-index: 2;
    display: flex; flex-direction: column;
    align-items: center; gap: 4px;
    width: 26%;
  }
  .wh-node {
    left: 50%;
    top: 0;
    transform: translateX(-50%);
  }
  .big-node {
    left: 4%;
    bottom: 0;
  }
  .small-node {
    right: 4%;
    bottom: 0;
  }
  .tank-label {
    font-size: 11px; color: #5a6a80; font-weight: 600;
    text-transform: uppercase; letter-spacing: .4px;
    text-align: center;
  }
  .tank-stack {
    position: relative;
    width: 100%;
    height: 130px;
    background: #f3f6fa;
    border: 1px solid #d8e0e8;
    border-radius: 4px;
    overflow: hidden;
  }
  .tank-fill {
    position: absolute; bottom: 0; left: 0; right: 0;
    background: linear-gradient(180deg, #5fa3d8 0%, #3a7fb8 100%);
    transition: height .18s linear;
  }
  .tank-fill.empty { background: #e6ecf2; }
  .tank-val { font-size: 14px; font-weight: 700; color: #1a2a40; }
  .tank-val.lost { color: #c0392b; }
  .ghost {
    position: absolute; top: -22px; left: 50%; transform: translateX(-50%);
    background: #fee5e2; color: #c0392b; border: 1px solid #f5a89e;
    padding: 2px 6px; border-radius: 3px; font-size: 10.5px;
    font-weight: 700; letter-spacing: .3px; white-space: nowrap;
    opacity: 0; transition: opacity .25s;
  }
  .ghost.show { opacity: 1; }
  .cap {
    position: absolute; left:0; right:0;
    background: repeating-linear-gradient(45deg,
      rgba(45,108,170,.6) 0 6px, rgba(45,108,170,.85) 6px 12px);
    pointer-events: none;
  }

  /* Flying packages: yellow chips that travel from warehouse to a store */
  .pkg {
    position: absolute;
    z-index: 5;
    background: #f1c40f;
    color: #1a2a40;
    padding: 3px 8px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 700;
    box-shadow: 0 2px 6px rgba(0,0,0,.18);
    transform: translate(-50%, -50%);
    opacity: 0;
    pointer-events: none;
    white-space: nowrap;
  }
  @keyframes fly-big {
    0%   { left: 50%; top: 18%;  opacity: 0; }
    12%  { opacity: 1; }
    88%  { opacity: 1; }
    100% { left: 17%; top: 85%; opacity: 0; }
  }
  @keyframes fly-small {
    0%   { left: 50%; top: 18%;  opacity: 0; }
    12%  { opacity: 1; }
    88%  { opacity: 1; }
    100% { left: 83%; top: 85%; opacity: 0; }
  }
  .pkg.flying.pkg-big   { animation: fly-big   1.6s ease-in-out forwards; }
  .pkg.flying.pkg-small { animation: fly-small 1.6s ease-in-out forwards; }

  .controls { display: flex; align-items: center; gap: 10px; margin-top: 14px; }
  .play { padding: 6px 14px; border-radius: 6px; border: 0;
          background: #1a2a40; color:#fff; font-weight: 600; cursor: pointer; }
  .play:hover { background:#2c3e56; }
  .play.playing { background: #c97a2c; }
  .week-readout { font-size: 13px; color:#5a6a80; min-width: 80px; }
  .week-readout b { color:#1a2a40; font-size: 14px; }
  .speed-label { font-size: 13px; color:#5a6a80; white-space: nowrap; }
  .speed-label b { color:#1a2a40; }
  input[type=range] { flex: 1; accent-color: #1a2a40; }
  .pnl { margin-top: 14px; border-top: 1px solid #ecf0f4; padding-top: 10px; }
  .pnl-row { display: flex; justify-content: space-between;
             font-size: 13px; color:#5a6a80; padding: 2px 0; }
  .pnl-row b { color: #1a2a40; }
  .pnl-row.margin { font-size: 16px; padding-top: 6px;
                    border-top: 1px dashed #ecf0f4; margin-top: 4px; }
  .pnl-row.margin b { font-size: 18px; }
  .pnl-row.margin.positive b { color: #1a8a4a; }
  .pnl-row.margin.negative b { color: #c0392b; }
  .summary { font-size: 11px; color:#7a8a9e; margin-top: 6px; }
  .summary .lost   { color:#c0392b; font-weight:600; }
  .summary .stuck  { color:#2c5f8a; font-weight:600; }
</style>
</head>
<body>

<div class="grid">

<div class="panel dump">
  <h2>Dump everything to stores</h2>
  <div class="sub">All bought stock split 50/50 between stores on day 1. Warehouse empty.</div>
  <div class="topo" id="dump-topo">
    <svg class="flow-svg" preserveAspectRatio="none" viewBox="0 0 100 100">
      <line x1="50" y1="18" x2="17" y2="85"></line>
      <line x1="50" y1="18" x2="83" y2="85"></line>
    </svg>
    <div class="node wh-node">
      <div class="tank-label">Warehouse</div>
      <div id="dump-wh-stack" class="tank-stack">
        <div id="dump-wh-fill" class="tank-fill empty"></div>
        <div id="dump-wh-cap" class="cap" style="display:none;"></div>
      </div>
      <div class="tank-val"><span id="dump-wh-val">0</span></div>
    </div>
    <div class="node big-node">
      <div class="tank-label">High-selling store (__BIG__/wk)</div>
      <div id="dump-big-stack" class="tank-stack">
        <div id="dump-big-fill" class="tank-fill"></div>
        <div id="dump-big-cap" class="cap" style="display:none;"></div>
        <div id="dump-big-ghost" class="ghost">LOST 0</div>
      </div>
      <div class="tank-val"><span id="dump-big-val">0</span></div>
    </div>
    <div class="node small-node">
      <div class="tank-label">Low-selling store (__SMALL__/wk)</div>
      <div id="dump-small-stack" class="tank-stack">
        <div id="dump-small-fill" class="tank-fill"></div>
        <div id="dump-small-cap" class="cap" style="display:none;"></div>
        <div id="dump-small-ghost" class="ghost">LOST 0</div>
      </div>
      <div class="tank-val"><span id="dump-small-val">0</span></div>
    </div>
    <div id="dump-pkg-big"   class="pkg pkg-big">+0</div>
    <div id="dump-pkg-small" class="pkg pkg-small">+0</div>
  </div>
  <div class="summary">
    <span class="lost">LOST sales:</span> <span id="dump-lost-tot">0</span>
    &nbsp;·&nbsp;
    <span class="stuck">Stuck stock at W26:</span> <span id="dump-stuck">0</span>
  </div>
  <div class="pnl">
    <div class="pnl-row"><span>Sales (sold × €__PRICE__)</span><b>€<span id="dump-turn">0</span></b></div>
    <div class="pnl-row"><span>Stock cost (bought × €__VC__)</span><b>€<span id="dump-cost">0</span></b></div>
    <div class="pnl-row"><span>Fixed cost</span><b>€<span id="dump-fixed">0</span></b></div>
    <div class="pnl-row margin"><span>Margin</span><b>€<span id="dump-margin">0</span></b></div>
  </div>
</div>

<div class="panel hold">
  <h2>Keep <span id="hold-pct-readout">30</span>% central</h2>
  <div class="sub">Some stock stays at the warehouse; refills stores each week as they sell.</div>
  <div class="topo" id="hold-topo">
    <svg class="flow-svg" preserveAspectRatio="none" viewBox="0 0 100 100">
      <line x1="50" y1="18" x2="17" y2="85"></line>
      <line x1="50" y1="18" x2="83" y2="85"></line>
    </svg>
    <div class="node wh-node">
      <div class="tank-label">Warehouse</div>
      <div id="hold-wh-stack" class="tank-stack">
        <div id="hold-wh-fill" class="tank-fill"></div>
        <div id="hold-wh-cap" class="cap" style="display:none;"></div>
      </div>
      <div class="tank-val"><span id="hold-wh-val">0</span></div>
    </div>
    <div class="node big-node">
      <div class="tank-label">High-selling store (__BIG__/wk)</div>
      <div id="hold-big-stack" class="tank-stack">
        <div id="hold-big-fill" class="tank-fill"></div>
        <div id="hold-big-cap" class="cap" style="display:none;"></div>
        <div id="hold-big-ghost" class="ghost">LOST 0</div>
      </div>
      <div class="tank-val"><span id="hold-big-val">0</span></div>
    </div>
    <div class="node small-node">
      <div class="tank-label">Low-selling store (__SMALL__/wk)</div>
      <div id="hold-small-stack" class="tank-stack">
        <div id="hold-small-fill" class="tank-fill"></div>
        <div id="hold-small-cap" class="cap" style="display:none;"></div>
        <div id="hold-small-ghost" class="ghost">LOST 0</div>
      </div>
      <div class="tank-val"><span id="hold-small-val">0</span></div>
    </div>
    <div id="hold-pkg-big"   class="pkg pkg-big">+0</div>
    <div id="hold-pkg-small" class="pkg pkg-small">+0</div>
  </div>
  <div class="summary">
    <span class="lost">LOST sales:</span> <span id="hold-lost-tot">0</span>
    &nbsp;·&nbsp;
    <span class="stuck">Stuck stock at W26:</span> <span id="hold-stuck">0</span>
  </div>
  <div class="pnl">
    <div class="pnl-row"><span>Sales (sold × €__PRICE__)</span><b>€<span id="hold-turn">0</span></b></div>
    <div class="pnl-row"><span>Stock cost (bought × €__VC__)</span><b>€<span id="hold-cost">0</span></b></div>
    <div class="pnl-row"><span>Fixed cost</span><b>€<span id="hold-fixed">0</span></b></div>
    <div class="pnl-row margin"><span>Margin</span><b>€<span id="hold-margin">0</span></b></div>
  </div>
</div>

</div>

<div class="controls">
  <button id="play" class="play">▶ Play</button>
  <span class="week-readout">Week <b id="weekRead">0</b> / __WEEKS__</span>
  <input type="range" id="scrub" min="0" max="__WEEKS__" value="0" step="1">
  <span class="speed-label">🐢</span>
  <input type="range" id="speed" min="0.25" max="4" value="1" step="0.25" style="flex:0 0 120px;">
  <span class="speed-label">🐇 <b id="speedRead">1.0×</b></span>
</div>

<script>
const DATA = __PAYLOAD__;
const PRICE = DATA.price;
const VC    = DATA.vc;
const peak  = DATA.peak;
const WK    = DATA.weeks;
const MS_PER_WEEK = 2000;   // 2 sec per week, requested pacing

function setFill(id, val) {
  const fill = document.getElementById(id + "-fill");
  const pct  = Math.max(0, Math.min(100, 100 * val / peak));
  fill.style.height = pct + "%";
  fill.classList.toggle("empty", val <= 0);
}
function setVal(id, val, lost) {
  const el = document.getElementById(id + "-val");
  el.textContent = val;
  el.classList.toggle("lost", lost > 0);
}
function setGhost(id, lostCum) {
  const g = document.getElementById(id + "-ghost");
  if (lostCum > 0) {
    g.textContent = "LOST " + lostCum;
    g.classList.add("show");
  } else {
    g.classList.remove("show");
  }
}
function flyPkg(prefix, target, qty) {
  const pkg = document.getElementById(prefix + "-pkg-" + target);
  if (qty > 0) {
    pkg.textContent = "+" + qty;
    pkg.classList.remove("flying");
    void pkg.offsetWidth;   // restart the keyframe animation
    pkg.classList.add("flying");
  } else {
    pkg.classList.remove("flying");
  }
}
function setCap(id, base, final) {
  // Render the "stuck stock at W26" as a blue striped cap on top of the
  // current fill so the audience can see what's stranded.
  const cap = document.getElementById(id + "-cap");
  if (final > 0 && base <= final + 0.01) {
    const bottom = 100 * base  / peak;
    const top    = 100 * final / peak;
    cap.style.bottom = bottom + "%";
    cap.style.height = Math.max(0, top - bottom) + "%";
    cap.style.display = "block";
  } else {
    cap.style.display = "none";
  }
}

function renderPanel(prefix, states, cum, week, flyPackages) {
  const s = states[week];
  setFill(prefix + "-wh", s.wh);
  setFill(prefix + "-big", s.big);
  setFill(prefix + "-small", s.small);
  setVal(prefix + "-wh", s.wh, 0);
  setVal(prefix + "-big",   s.big,   s.lostB);
  setVal(prefix + "-small", s.small, s.lostS);

  setGhost(prefix + "-big",   cum[week].B);
  setGhost(prefix + "-small", cum[week].S);

  // Only fly packages when advancing through Play — not when scrubbing.
  if (flyPackages) {
    flyPkg(prefix, "big",   s.inB);
    flyPkg(prefix, "small", s.inS);
  }

  // Stuck cap is visible only on the LAST frame.
  if (week === WK) {
    const finalState = states[WK];
    setCap(prefix + "-big",   finalState.big,   finalState.big);
    setCap(prefix + "-small", finalState.small, finalState.small);
    setCap(prefix + "-wh",    finalState.wh,    finalState.wh);
  } else {
    document.getElementById(prefix + "-big-cap").style.display   = "none";
    document.getElementById(prefix + "-small-cap").style.display = "none";
    document.getElementById(prefix + "-wh-cap").style.display    = "none";
  }
}

function renderAll(week, flyPackages) {
  document.getElementById("weekRead").textContent = week;
  document.getElementById("scrub").value = week;
  renderPanel("dump", DATA.dump, DATA.cumDump, week, flyPackages);
  renderPanel("hold", DATA.hold, DATA.cumHold, week, flyPackages);
  document.getElementById("hold-pct-readout").textContent = DATA.holdPct;

  // P&L numbers — ONE formula, identical to the Python cards:
  //   sales  = cumulative units sold (NEVER inferred from inventory) × price
  //   margin = sales − (all units bought × cost) − fixed cost
  // Stock cost and fixed cost are charged in full from week 0 (cash already
  // committed), so the running margin starts negative and climbs as sales land.
  const stockCost = DATA.bought * VC;
  function updatePnl(prefix, states, cum, soldArr) {
    const sold   = soldArr[week];
    const turn   = sold * PRICE;
    const margin = turn - stockCost - DATA.fixed;
    const onHand = states[week].wh + states[week].big + states[week].small;
    document.getElementById(prefix + "-turn").textContent   = Math.round(turn).toLocaleString();
    document.getElementById(prefix + "-cost").textContent   = Math.round(stockCost).toLocaleString();
    document.getElementById(prefix + "-fixed").textContent  = Math.round(DATA.fixed).toLocaleString();
    document.getElementById(prefix + "-margin").textContent = Math.round(margin).toLocaleString();
    const mrow = document.getElementById(prefix + "-margin").parentElement;
    mrow.classList.toggle("positive", margin >= 0);
    mrow.classList.toggle("negative", margin <  0);
    document.getElementById(prefix + "-lost-tot").textContent = cum[week].B + cum[week].S;
    document.getElementById(prefix + "-stuck").textContent    = onHand;
  }
  updatePnl("dump", DATA.dump, DATA.cumDump, DATA.soldDump);
  updatePnl("hold", DATA.hold, DATA.cumHold, DATA.soldHold);
}

// Initial state = end-of-season (so the page renders the headline view on load)
renderAll(WK, false);

const playBtn  = document.getElementById("play");
const scrub    = document.getElementById("scrub");
const speedEl  = document.getElementById("speed");
const speedRead = document.getElementById("speedRead");

let playing = false;
let raf = null;

// Speed multiplier (1× = the requested 2 s/week). Read live so dragging the
// slider mid-film speeds it up / slows it down immediately.
function speedMult() { return parseFloat(speedEl.value); }
function showSpeed() { speedRead.textContent = speedMult().toFixed(2).replace(/0$/, "") + "×"; }
speedEl.addEventListener("input", showSpeed);
showSpeed();

scrub.addEventListener("input", e => {
  // Scrubbing always cancels Play (clean state) and never triggers packages.
  if (playing) pause();
  renderAll(parseInt(e.target.value), false);
});

function play() {
  if (playing) { pause(); return; }
  // Always cancel any pending frame and start fresh from week 0.
  if (raf) { cancelAnimationFrame(raf); raf = null; }
  playing = true;
  playBtn.classList.add("playing");
  playBtn.textContent = "⏸ Pause";
  // Accumulate elapsed time in "week units" so the live speed slider applies
  // mid-film: each frame advances weekFloat by dt / (current ms-per-week).
  let weekFloat = 0;
  let lastWeek  = -1;
  let lastTime  = performance.now();
  renderAll(0, false);
  function frame(now) {
    if (!playing) return;
    const dt = now - lastTime;
    lastTime = now;
    weekFloat += dt / (MS_PER_WEEK / speedMult());
    const w = Math.min(WK, Math.floor(weekFloat));
    if (w !== lastWeek) {
      // Pass flyPackages=true so each new week kicks off the flying chip.
      renderAll(w, true);
      lastWeek = w;
    }
    if (weekFloat < WK) {
      raf = requestAnimationFrame(frame);
    } else {
      renderAll(WK, false);
      pause();
    }
  }
  raf = requestAnimationFrame(frame);
}
function pause() {
  playing = false;
  playBtn.classList.remove("playing");
  playBtn.textContent = "▶ Play";
  if (raf) { cancelAnimationFrame(raf); raf = null; }
}
playBtn.addEventListener("click", play);
</script>
</body></html>
"""

html = (HTML
        .replace("__PAYLOAD__", json.dumps(payload))
        .replace("__PRICE__",   f"{price:,.0f}")
        .replace("__VC__",      f"{var_cost:,.0f}")
        .replace("__WEEKS__",   str(WEEKS))
        .replace("__BIG__",     str(big_rate))
        .replace("__SMALL__",   str(small_rate)))

st.iframe(html, height=720)

# ── Footer hint ─────────────────────────────────────────────────────────
st.markdown(
    "<div style='color:#7a8a9e; font-size:11.5px; text-align:center; margin-top:8px;'>"
    "Press <b>▶ Play</b> to watch the season unfold (2 sec / week). Use the sliders above "
    "to change the inputs — both panels re-animate."
    "</div>",
    unsafe_allow_html=True,
)
