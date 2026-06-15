"""'Where should the stock sit?' — a teaching page for non-SC people.

One lesson: dumping all your stock into shops on day 1 leaves it stuck
in the small shop and missing in the big one. Keep some central and
the warehouse feeds whoever is actually selling.

UI: two side-by-side animated panels (one per policy), three input
controls (forecast, stock%, hold%), and a mini P&L for each side plus
the delta as the headline.

The animation is rendered as a single SVG-based HTML component to keep
movement smooth and let us pack the whole teaching slide into one
deterministic frame.
"""
from __future__ import annotations
import json
import streamlit as st
import streamlit.components.v1 as components

from sim_stash import simulate, totals, PRICE, VAR_COST, WEEKS

# Big / Small split is fixed; only the absolute demand level moves with
# the slider. 75/25 keeps the lesson crisp at any volume.
BIG_SHARE   = 0.75
SMALL_SHARE = 0.25

st.set_page_config(layout="wide", page_title="Where should the stock sit?",
                   page_icon="\U0001F4E6")

st.markdown("""
<style>
.block-container { padding-top: 1.4rem; max-width: 1400px; }
section[data-testid="stSidebar"] { width: 0 !important; min-width: 0 !important; }
</style>
""", unsafe_allow_html=True)

st.markdown(
    "<h1 style='margin:0 0 4px 0; font-size:28px;'>\U0001F4E6 "
    "Where should the stock sit?</h1>",
    unsafe_allow_html=True,
)
st.markdown(
    "<div style='color:#5a6a80; font-size:14px; margin-bottom:14px;'>"
    "Same stock bought. Same shops. Same demand. The only difference is "
    "<b>where the stock starts</b> on day 1.</div>",
    unsafe_allow_html=True,
)

# ── Inputs ────────────────────────────────────────────────────────────────
c1, c2, c3, c4 = st.columns([1, 1, 1, 2])
with c1:
    forecast = st.number_input(
        "Forecast (units / 26 wks)",
        min_value=1000, max_value=10000, value=2600, step=100,
        help="What you THINK sales will be — used to decide how much stock to buy.",
    )
with c2:
    stock_pct = st.slider(
        "Bought as % of forecast",
        min_value=50, max_value=120, value=80, step=5,
        help="How much stock you actually buy. 100% = buy exactly the forecast.",
    )
with c3:
    actual_per_week = st.slider(
        "Actual demand (units / wk)",
        min_value=20, max_value=400, value=100, step=10,
        help="What ACTUALLY happens — flat each week. Try setting it above or "
             "below the forecast to see what under/over-buying looks like.",
    )
with c4:
    hold_pct = st.slider(
        "**% kept in the warehouse on day 1** \U0001F441",
        min_value=0, max_value=100, value=30, step=5,
        help="The lever. 0% = all stock dumped to shops on day 1. "
             "100% = everything kept central. The truth is in between.",
    )

bought     = int(round(forecast * stock_pct / 100))
big_rate   = int(round(actual_per_week * BIG_SHARE))
small_rate = int(round(actual_per_week - big_rate))  # mass-conservative
actual_total = actual_per_week * WEEKS

st.caption(
    f"You buy **{bought:,} units** at €{VAR_COST:.0f} each = "
    f"€{bought * VAR_COST:,.0f} of stock. "
    f"Big shop sells **{big_rate}/wk**, Small shop sells **{small_rate}/wk** "
    f"(actual demand over 26 wks = **{actual_total:,}**)."
)

# ── Run the two policies ──────────────────────────────────────────────────
dump_states = simulate(bought, hold_pct=0.0,
                       big_rate=big_rate, small_rate=small_rate)
hold_states = simulate(bought, hold_pct=hold_pct / 100.0,
                       big_rate=big_rate, small_rate=small_rate)

dump_tot = totals(dump_states)
hold_tot = totals(hold_states)

# Headline delta (above the visualisation)
delta = hold_tot["margin"] - dump_tot["margin"]
delta_colour = "#1a8a4a" if delta >= 0 else "#c0392b"
delta_label = "earned" if delta >= 0 else "LOST"
st.markdown(
    f"<div style='background:#fff; border:1px solid #e6ecf2; border-radius:10px; "
    f"padding:14px 18px; text-align:center; margin:14px 0 18px;'>"
    f"<span style='color:#5a6a80; font-size:13px;'>"
    f"Keeping {hold_pct}% in the warehouse vs dumping everything to shops:</span>"
    f"<span style='font-size:28px; font-weight:700; color:{delta_colour}; "
    f"margin-left:14px;'>{delta_label} €{abs(delta):,.0f}</span></div>",
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
    "peak":     peak,
    "weeks":    WEEKS,
    "bigRate":  big_rate,
    "smallRate": small_rate,
    "holdPct":  hold_pct,
    "bought":   bought,
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
  .chain { display: flex; gap: 14px; align-items: flex-end; min-height: 220px; }
  .tank-wrap { flex: 1; display: flex; flex-direction: column; align-items: center; gap: 4px; }
  .tank-label { font-size: 11px; color: #5a6a80; font-weight: 600;
                text-transform: uppercase; letter-spacing: .4px; }
  .tank-stack { position: relative; width: 80%; height: 200px;
                background: #f3f6fa; border: 1px solid #d8e0e8;
                border-radius: 4px; overflow: hidden; }
  .tank-stack.wide { width: 95%; }
  .tank-fill {
    position: absolute; bottom: 0; left: 0; right: 0;
    background: linear-gradient(180deg, #5fa3d8 0%, #3a7fb8 100%);
    transition: height .12s linear;
  }
  .tank-fill.empty { background: #e6ecf2; }
  .tank-stack.delivering::after {
    content: ''; position: absolute; left: 50%; top: -14px;
    width: 14px; height: 14px; background: #f1c40f;
    border-radius: 2px; transform: translateX(-50%);
    box-shadow: 0 2px 4px rgba(0,0,0,.15);
    animation: drop .5s ease-in;
  }
  @keyframes drop { from { top: -40px; opacity: 0; } to { top: -14px; opacity: 1; } }
  .tank-val { font-size: 14px; font-weight: 700; color: #1a2a40; }
  .tank-val.lost { color: #c0392b; }
  .ghost {
    position: absolute; top: -38px; left: 50%; transform: translateX(-50%);
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
  .controls { display: flex; align-items: center; gap: 10px; margin-top: 14px; }
  .play { padding: 6px 14px; border-radius: 6px; border: 0;
          background: #1a2a40; color:#fff; font-weight: 600; cursor: pointer; }
  .play:hover { background:#2c3e56; }
  .play.playing { background: #c97a2c; }
  .week-readout { font-size: 13px; color:#5a6a80; min-width: 80px; }
  .week-readout b { color:#1a2a40; font-size: 14px; }
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
  <h2>Dump everything to shops</h2>
  <div class="sub">All bought stock split 50/50 between shops on day 1. Warehouse empty.</div>
  <div class="chain">
    <div class="tank-wrap">
      <div class="tank-label">Warehouse</div>
      <div id="dump-wh-stack" class="tank-stack">
        <div id="dump-wh-fill" class="tank-fill empty"></div>
        <div id="dump-wh-cap" class="cap" style="display:none;"></div>
      </div>
      <div class="tank-val"><span id="dump-wh-val">0</span></div>
    </div>
    <div class="tank-wrap">
      <div class="tank-label">Big shop (__BIG__/wk)</div>
      <div id="dump-big-stack" class="tank-stack">
        <div id="dump-big-fill" class="tank-fill"></div>
        <div id="dump-big-cap" class="cap" style="display:none;"></div>
        <div id="dump-big-ghost" class="ghost">LOST 0</div>
      </div>
      <div class="tank-val"><span id="dump-big-val">0</span></div>
    </div>
    <div class="tank-wrap">
      <div class="tank-label">Small shop (__SMALL__/wk)</div>
      <div id="dump-small-stack" class="tank-stack">
        <div id="dump-small-fill" class="tank-fill"></div>
        <div id="dump-small-cap" class="cap" style="display:none;"></div>
        <div id="dump-small-ghost" class="ghost">LOST 0</div>
      </div>
      <div class="tank-val"><span id="dump-small-val">0</span></div>
    </div>
  </div>
  <div class="summary">
    <span class="lost">LOST sales:</span> <span id="dump-lost-tot">0</span>
    &nbsp;·&nbsp;
    <span class="stuck">Stuck stock at W26:</span> <span id="dump-stuck">0</span>
  </div>
  <div class="pnl">
    <div class="pnl-row"><span>Turnover (sold × €__PRICE__)</span><b>€<span id="dump-turn">0</span></b></div>
    <div class="pnl-row"><span>Stock cost (bought × €__VC__)</span><b>€<span id="dump-cost">0</span></b></div>
    <div class="pnl-row margin"><span>Margin</span><b>€<span id="dump-margin">0</span></b></div>
  </div>
</div>

<div class="panel hold">
  <h2>Keep <span id="hold-pct-readout">30</span>% central</h2>
  <div class="sub">Some stock stays at the warehouse; refills shops each week as they sell.</div>
  <div class="chain">
    <div class="tank-wrap">
      <div class="tank-label">Warehouse</div>
      <div id="hold-wh-stack" class="tank-stack">
        <div id="hold-wh-fill" class="tank-fill"></div>
        <div id="hold-wh-cap" class="cap" style="display:none;"></div>
      </div>
      <div class="tank-val"><span id="hold-wh-val">0</span></div>
    </div>
    <div class="tank-wrap">
      <div class="tank-label">Big shop (__BIG__/wk)</div>
      <div id="hold-big-stack" class="tank-stack">
        <div id="hold-big-fill" class="tank-fill"></div>
        <div id="hold-big-cap" class="cap" style="display:none;"></div>
        <div id="hold-big-ghost" class="ghost">LOST 0</div>
      </div>
      <div class="tank-val"><span id="hold-big-val">0</span></div>
    </div>
    <div class="tank-wrap">
      <div class="tank-label">Small shop (__SMALL__/wk)</div>
      <div id="hold-small-stack" class="tank-stack">
        <div id="hold-small-fill" class="tank-fill"></div>
        <div id="hold-small-cap" class="cap" style="display:none;"></div>
        <div id="hold-small-ghost" class="ghost">LOST 0</div>
      </div>
      <div class="tank-val"><span id="hold-small-val">0</span></div>
    </div>
  </div>
  <div class="summary">
    <span class="lost">LOST sales:</span> <span id="hold-lost-tot">0</span>
    &nbsp;·&nbsp;
    <span class="stuck">Stuck stock at W26:</span> <span id="hold-stuck">0</span>
  </div>
  <div class="pnl">
    <div class="pnl-row"><span>Turnover (sold × €__PRICE__)</span><b>€<span id="hold-turn">0</span></b></div>
    <div class="pnl-row"><span>Stock cost (bought × €__VC__)</span><b>€<span id="hold-cost">0</span></b></div>
    <div class="pnl-row margin"><span>Margin</span><b>€<span id="hold-margin">0</span></b></div>
  </div>
</div>

</div>

<div class="controls">
  <button id="play" class="play">▶ Play</button>
  <span class="week-readout">Week <b id="weekRead">0</b> / __WEEKS__</span>
  <input type="range" id="scrub" min="0" max="__WEEKS__" value="0" step="1">
</div>

<script>
const DATA = __PAYLOAD__;
const PRICE = __PRICE__;
const VC    = __VC__;
const peak  = DATA.peak;
const WK    = DATA.weeks;

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
function setGhost(id, lostThisWeek, lostCum) {
  const g = document.getElementById(id + "-ghost");
  if (lostCum > 0) {
    g.textContent = "LOST " + lostCum;
    g.classList.add("show");
  } else {
    g.classList.remove("show");
  }
}
function setDelivery(id, inFlight) {
  const stack = document.getElementById(id + "-stack");
  if (inFlight > 0) {
    stack.classList.remove("delivering");
    void stack.offsetWidth;     // restart the animation
    stack.classList.add("delivering");
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

function renderPanel(prefix, states, cum, week) {
  const s = states[week];
  setFill(prefix + "-wh", s.wh);
  setFill(prefix + "-big", s.big);
  setFill(prefix + "-small", s.small);
  setVal(prefix + "-wh", s.wh, 0);
  setVal(prefix + "-big",   s.big,   s.lostB);
  setVal(prefix + "-small", s.small, s.lostS);

  setGhost(prefix + "-big",   s.lostB, cum[week].B);
  setGhost(prefix + "-small", s.lostS, cum[week].S);

  setDelivery(prefix + "-big",   s.inB);
  setDelivery(prefix + "-small", s.inS);

  // Stuck cap is visible only on the LAST frame so the audience reads it
  // as "this is what's still sitting there at the end of the season".
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

function renderAll(week) {
  document.getElementById("weekRead").textContent = week;
  document.getElementById("scrub").value = week;
  renderPanel("dump", DATA.dump, DATA.cumDump, week);
  renderPanel("hold", DATA.hold, DATA.cumHold, week);
  document.getElementById("hold-pct-readout").textContent = DATA.holdPct;

  // P&L numbers update only on the final frame (cleanest reveal).
  const showPnl = (week === WK);
  function updatePnl(prefix, states, cum) {
    const fin  = states[WK];
    const sold = (DATA.bought) - (fin.wh + fin.big + fin.small);
    const lost = cum[WK].B + cum[WK].S;
    const turn = sold * PRICE;
    const cost = DATA.bought * VC;
    const margin = turn - cost;
    if (showPnl) {
      document.getElementById(prefix + "-turn").textContent   = turn.toLocaleString();
      document.getElementById(prefix + "-cost").textContent   = cost.toLocaleString();
      document.getElementById(prefix + "-margin").textContent = margin.toLocaleString();
      const mrow = document.getElementById(prefix + "-margin").parentElement;
      mrow.classList.toggle("positive", margin >= 0);
      mrow.classList.toggle("negative", margin <  0);
      document.getElementById(prefix + "-lost-tot").textContent = lost;
      document.getElementById(prefix + "-stuck").textContent =
        (fin.wh + fin.big + fin.small);
    } else {
      // Show running cumulative sold & lost during the animation
      let runSold = 0;
      for (let w = 1; w <= week; w++) {
        runSold += states[w].soldB + states[w].soldS;
      }
      document.getElementById(prefix + "-turn").textContent =
        (runSold * PRICE).toLocaleString();
      document.getElementById(prefix + "-cost").textContent =
        (DATA.bought * VC).toLocaleString();
      const m = runSold * PRICE - DATA.bought * VC;
      document.getElementById(prefix + "-margin").textContent = m.toLocaleString();
      const mrow = document.getElementById(prefix + "-margin").parentElement;
      mrow.classList.toggle("positive", m >= 0);
      mrow.classList.toggle("negative", m <  0);
      document.getElementById(prefix + "-lost-tot").textContent =
        (cum[week].B + cum[week].S);
      document.getElementById(prefix + "-stuck").textContent =
        (states[week].wh + states[week].big + states[week].small);
    }
  }
  updatePnl("dump", DATA.dump, DATA.cumDump);
  updatePnl("hold", DATA.hold, DATA.cumHold);
}

// Initial state = end-of-season (so the page renders the headline view on load)
renderAll(WK);

const playBtn = document.getElementById("play");
const scrub   = document.getElementById("scrub");

scrub.addEventListener("input", e => renderAll(parseInt(e.target.value)));

let playing = false;
let raf = null;
function play() {
  if (playing) { stop(); return; }
  playing = true;
  playBtn.classList.add("playing");
  playBtn.textContent = "⏸ Pause";
  const total_ms = 6000;
  const start = performance.now();
  // Always start from W0 when Play is pressed
  let startWeek = 0;
  renderAll(0);
  function frame(now) {
    if (!playing) return;
    const t = Math.min(1, (now - start) / total_ms);
    const w = Math.min(WK, Math.floor(startWeek + (WK - startWeek) * t));
    renderAll(w);
    if (t < 1) {
      raf = requestAnimationFrame(frame);
    } else {
      stop();
      renderAll(WK);
    }
  }
  raf = requestAnimationFrame(frame);
}
function stop() {
  playing = false;
  playBtn.classList.remove("playing");
  playBtn.textContent = "▶ Play";
  if (raf) cancelAnimationFrame(raf);
}
playBtn.addEventListener("click", play);
</script>
</body></html>
"""

html = (HTML
        .replace("__PAYLOAD__", json.dumps(payload))
        .replace("__PRICE__",   str(PRICE))
        .replace("__VC__",      str(VAR_COST))
        .replace("__WEEKS__",   str(WEEKS))
        .replace("__BIG__",     str(big_rate))
        .replace("__SMALL__",   str(small_rate)))

components.html(html, height=600, scrolling=False)

# ── Footer hint ─────────────────────────────────────────────────────────
st.markdown(
    "<div style='color:#7a8a9e; font-size:11.5px; text-align:center; margin-top:8px;'>"
    "Press <b>▶ Play</b> to watch the season unfold. Use the slider above "
    "to change how much stock stays central — both panels re-animate."
    "</div>",
    unsafe_allow_html=True,
)
