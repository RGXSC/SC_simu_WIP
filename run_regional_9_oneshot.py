"""9 regional scenarios — ONE-SHOT product (no replenishment).

prod_cap = init_total so the supplier never produces a new unit. The
chain sells whatever was pre-positioned at W0; differences between
the 9 cells reduce to "where you placed the bet" + how well the smart
allocator can rebalance from CW.
"""
import sim_regional as s

WEEKS, DEMAND = 26, [100] * 26
N_PER_REGION = 50
TOTAL_INIT = 2600          # = total demand over 26 wks
PROD_CAP   = TOTAL_INIT    # one-shot: cap = init -> 0 replenishment
# Agile distribution legs only (no production possible, so upstream LTs
# are dead weight — kept short to avoid pretending things move).
MAT_LT, SEMI_LT, FP_LT = 1, 1, 1
CW_RW_LT, RW_STORE_LT  = 1, 1
PRICE, VC, FIXED_PCT = 10.0, 5.0, 0.20

REGION_SPLITS = [50, 70, 90]
STOCK_DISTS = [
    ('0 / 0 / 100',  0,   0, 100),
    ('0 / 30 / 70',  0,  30,  70),
    ('30 / 30 / 40', 30, 30,  40),
]

def run(sp, cwp, rwp, stp):
    init_cw    = int(round(TOTAL_INIT * cwp / 100))
    init_rw    = int(round(TOTAL_INIT * rwp / 100))
    init_store = TOTAL_INIT - init_cw - init_rw
    return s.run_simulation_regional(
        weeks=WEEKS, n_per_region=N_PER_REGION, demand_curve=DEMAND,
        region_split_pct=sp,
        mat_lt=MAT_LT, semi_lt=SEMI_LT, fp_lt=FP_LT,
        cw_rw_lt=CW_RW_LT, rw_store_lt=RW_STORE_LT, order_freq=1,
        init_rawmat=0, init_semi=0,
        init_cw=init_cw, init_rw_total=init_rw, init_store_total=init_store,
        cap_start=1000, cap_ramp=0.0,
        smart_distrib=True, prod_cap=PROD_CAP,
        var_cost=VC, price=PRICE, fixed_pct=FIXED_PCT,
        base_forecast=100,
    )

results = []
for sp in REGION_SPLITS:
    for label, cwp, rwp, stp in STOCK_DISTS:
        r = run(sp, cwp, rwp, stp)
        results.append({
            'split_lbl': f"{sp}/{100-sp}", 'split_a': sp,
            'dist': label,
            'svc':   r['svc']*100,   'svc_a': r['svc_a']*100, 'svc_b': r['svc_b']*100,
            'sales': r['tot_sales'], 'produced': r['tot_produced'],
            'missed': r['tot_demand'] - r['tot_sales'],
            'sell_through': r['sell_through']*100,
            'revenue': r['revenue'], 'cogs': r['cogs'],
            'margin':  r['margin'], 'margin_pct_rev': r['margin_pct_rev']*100,
        })

# ── HTML render ─────────────────────────────────────────────────────────
def _bar_color_margin(v, vmin, vmax):
    if vmax == vmin: return "#1a8a4a"
    t = (v - vmin) / (vmax - vmin)
    if t > 0.7: return "#1a8a4a"
    if t > 0.4: return "#f1c40f"
    return "#c0392b"

def _bar_color_svc(v):
    if v >= 95: return "#1a8a4a"
    if v >= 85: return "#e67e22"
    return "#c0392b"

def _bar_color_st(v):
    if v >= 95: return "#1a8a4a"
    if v >= 80: return "#e67e22"
    return "#c0392b"

all_margins = [r['margin'] for r in results]
mmin, mmax = min(all_margins), max(all_margins)

def cell_html(r):
    mbar = max(0, min(100, (r['margin'] - mmin) / (mmax - mmin) * 100)) if mmax > mmin else 50
    mcolor = _bar_color_margin(r['margin'], mmin, mmax)
    svc_a_c = _bar_color_svc(r['svc_a']); svc_b_c = _bar_color_svc(r['svc_b'])
    st_c    = _bar_color_st(r['sell_through'])
    return f"""
    <div class="cell">
      <div class="cell-dist">{r['dist']}</div>
      <div class="cell-margin" style="color:{mcolor};">€{r['margin']:+,.0f}</div>
      <div class="cell-margin-pct">{r['margin_pct_rev']:+.1f}% of revenue</div>
      <div class="bar-track"><div class="bar-fill" style="width:{mbar:.0f}%;background:{mcolor};"></div></div>
      <div class="cell-row">
        <span class="lbl">Sell-thru</span>
        <span class="val" style="color:{st_c};font-weight:700;">{r['sell_through']:.1f}%</span>
      </div>
      <div class="cell-row">
        <span class="lbl">Service A</span>
        <span class="val" style="color:{svc_a_c};font-weight:600;">{r['svc_a']:.1f}%</span>
      </div>
      <div class="cell-row">
        <span class="lbl">Service B</span>
        <span class="val" style="color:{svc_b_c};font-weight:600;">{r['svc_b']:.1f}%</span>
      </div>
      <div class="cell-row dim">
        <span class="lbl">Sales</span><span class="val">{r['sales']:,}</span>
      </div>
      <div class="cell-row dim">
        <span class="lbl">Missed</span><span class="val">{r['missed']:,}</span>
      </div>
    </div>"""

def split_block(sp):
    cells = [r for r in results if r['split_a'] == sp]
    return f"""
    <div class="split-row">
      <div class="split-label">
        <div class="split-label-big">{sp}/{100-sp}</div>
        <div class="split-label-sub">Region A / B</div>
      </div>
      <div class="cells">{''.join(cell_html(c) for c in cells)}</div>
    </div>"""

best = max(results, key=lambda x: x['margin'])
worst = min(results, key=lambda x: x['margin'])
best_st = max(results, key=lambda x: x['sell_through'])
worst_b_svc = min(r['svc_b'] for r in results)

sorted_r = sorted(results, key=lambda x: -x['margin'])
def margin_bar(r):
    w = (r['margin'] - mmin) / (mmax - mmin) * 100 if mmax > mmin else 50
    color = _bar_color_margin(r['margin'], mmin, mmax)
    return f"""
    <div class="mb-row">
      <div class="mb-lbl">{r['split_lbl']} &middot; {r['dist']}</div>
      <div class="mb-track"><div class="mb-fill" style="width:{w:.0f}%;background:{color};"></div></div>
      <div class="mb-val" style="color:{color};">€{r['margin']:+,.0f}</div>
    </div>"""

def svc_pair(r):
    return f"""
    <div class="sb-row">
      <div class="sb-lbl">{r['split_lbl']} &middot; {r['dist']}</div>
      <div class="sb-pair">
        <div class="sb-bar">
          <div class="sb-cap">A</div>
          <div class="sb-track"><div class="sb-fill" style="width:{r['svc_a']:.0f}%;background:{_bar_color_svc(r['svc_a'])};"></div></div>
          <div class="sb-val">{r['svc_a']:.1f}%</div>
        </div>
        <div class="sb-bar">
          <div class="sb-cap">B</div>
          <div class="sb-track"><div class="sb-fill" style="width:{r['svc_b']:.0f}%;background:{_bar_color_svc(r['svc_b'])};"></div></div>
          <div class="sb-val">{r['svc_b']:.1f}%</div>
        </div>
      </div>
    </div>"""

html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>9 Regional Scenarios — One-shot product</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: 'Inter', Calibri, sans-serif; margin: 0; padding: 24px 32px;
         color:#1a2a40; background:#fafbfc; line-height:1.5; }}
  h1 {{ font-size: 24px; margin: 0 0 4px; color:#1a2a40; }}
  h2 {{ font-size: 16px; margin: 28px 0 10px; color:#2c3e56; }}
  .sub {{ color:#5a6a80; font-size:13px; margin-bottom:18px; }}
  .hyp {{ background:#fff; border-left:4px solid #2c5f8a; padding:14px 18px;
          font-size:12.5px; box-shadow: 0 1px 2px rgba(0,0,0,.05); margin: 18px 0 24px; }}
  .hyp h3 {{ margin:0 0 8px; color:#2c5f8a; font-size:13px; text-transform:uppercase; letter-spacing:.4px; }}
  .hyp ul {{ margin: 6px 0 0; padding-left: 20px; }}
  .hyp li {{ margin: 3px 0; }}
  .hyp b {{ color:#1a2a40; }}
  .hyp .new {{ background:#fff7e1; padding:2px 6px; border-radius:3px; font-weight:600; color:#9a6a00; }}
  .params {{ margin-bottom:6px; font-size:12px; color:#5a6a80; }}
  .pill {{ display:inline-block; padding:3px 9px; border-radius:11px;
           background:#eef2f6; color:#2c3e56; margin: 0 5px 5px 0; font-weight:600; font-size:11.5px; }}
  .pill.alert {{ background:#ffe5d6; color:#a14a14; }}
  .split-row {{ display:grid; grid-template-columns: 130px 1fr; gap:14px;
                margin-bottom:14px; align-items:stretch; }}
  .split-label {{ background:#1a2a40; color:#fff; border-radius:10px;
                  padding:14px 12px; display:flex; flex-direction:column;
                  justify-content:center; align-items:center; text-align:center; }}
  .split-label-big {{ font-size: 22px; font-weight:700; letter-spacing:1px; }}
  .split-label-sub {{ font-size: 10.5px; opacity:.85; margin-top:4px; text-transform:uppercase; letter-spacing:.4px; }}
  .cells {{ display:grid; grid-template-columns: repeat(3, 1fr); gap:12px; }}
  .cell {{ background:#fff; border-radius:8px; padding:12px 14px;
           box-shadow: 0 1px 2px rgba(0,0,0,.05); font-size:12px; }}
  .cell-dist {{ font-size: 10.5px; text-transform:uppercase; letter-spacing:.5px; color:#5a6a80; font-weight:600; }}
  .cell-margin {{ font-size: 22px; font-weight:700; margin-top:6px; }}
  .cell-margin-pct {{ font-size: 11px; color:#5a6a80; margin-bottom:8px; }}
  .bar-track {{ background:#ecf0f4; height:6px; border-radius:3px; margin:4px 0 10px; overflow:hidden; }}
  .bar-fill {{ height:100%; border-radius:3px; }}
  .cell-row {{ display:flex; justify-content:space-between; padding:2px 0; }}
  .cell-row.dim {{ color:#7a8a9e; }}
  .cell-row .lbl {{ color:#5a6a80; }}
  .cell-row .val {{ color:#1a2a40; font-weight:500; }}
  .charts {{ display:grid; grid-template-columns: 1fr 1fr; gap:20px; margin-top:8px; }}
  .chart {{ background:#fff; border-radius:8px; padding:16px 18px; box-shadow: 0 1px 2px rgba(0,0,0,.05); }}
  .chart h3 {{ margin: 0 0 12px; font-size:13px; color:#2c3e56; text-transform:uppercase; letter-spacing:.4px; }}
  .mb-row {{ display:grid; grid-template-columns: 170px 1fr 80px; align-items:center; gap:8px; margin-bottom:4px; font-size:11.5px; }}
  .mb-lbl {{ color:#2c3e56; }}
  .mb-track {{ background:#ecf0f4; height:18px; border-radius:3px; overflow:hidden; }}
  .mb-fill {{ height:100%; }}
  .mb-val {{ text-align:right; font-weight:600; font-size:11.5px; }}
  .sb-row {{ display:grid; grid-template-columns: 170px 1fr; gap:10px; margin-bottom:10px; font-size:11.5px; align-items:center; }}
  .sb-lbl {{ color:#2c3e56; }}
  .sb-pair {{ display:grid; grid-template-rows: 1fr 1fr; gap:3px; }}
  .sb-bar {{ display:grid; grid-template-columns: 18px 1fr 60px; align-items:center; gap:6px; font-size:10.5px; }}
  .sb-cap {{ color:#5a6a80; font-weight:600; }}
  .sb-track {{ background:#ecf0f4; height:11px; border-radius:2px; overflow:hidden; }}
  .sb-fill {{ height:100%; }}
  .sb-val {{ font-weight:600; }}
  .takeaway {{ margin-top:24px; padding:14px 18px; background:#fff;
               border-left:4px solid #1a8a4a; font-size:12.5px;
               box-shadow: 0 1px 2px rgba(0,0,0,.05); }}
  .takeaway b {{ color:#1a2a40; }}
</style></head>
<body>
<h1>9 Regional Scenarios — One-shot product (no replenishment)</h1>
<div class="sub">The chain produces nothing after week 0. The 2,600 units of total demand can only be served from initial stock.</div>

<div class="params">
  <span class="pill alert">Production cap 2,600 = init (no supplier orders)</span>
  <span class="pill">Total LT 5 wk (Agile)</span>
  <span class="pill">Flat 100/wk × 26 wks → total demand 2,600</span>
  <span class="pill">50 stores / region</span>
  <span class="pill">Smart distribution on</span>
  <span class="pill">Price €10/u · VC €5/u · Fixed €5,200</span>
</div>

<div class="hyp">
  <h3>Hypotheses fixed across the 9 cells</h3>
  <ul>
    <li><b>One-shot product</b>: lifetime production capped at <b>2,600 units = initial stock</b>. No supplier orders fire after W0. Whatever the chain holds at week 0 is all it will ever have.</li>
    <li><b>Total demand 2,600 units</b>: flat 100 pcs/wk &times; 26 wks in every cell. The split slider only changes how those 2,600 are divided between Region A and B.</li>
    <li><b>W0 stock split 50/50 between regions</b>: the operator pre-positions evenly because the regional demand split is not yet known at week 0. The planner discovers the actual split at the first review (week 1) and from then on the smart allocator routes the CW pool to whichever region is starving.</li>
    <li><b>Within each region, store stock is split by tier-bucket share</b>: 40% to the 5 high-selling stores, 46% to the 15 medium, 14% to the 30 small &mdash; tier-uniform within tier. Standard operator heuristic: give the high-sellers more stock because they sell more.</li>
    <li><b>Strict regional isolation</b>: stock at RW A only feeds Region A's stores; same for B. Region B's surplus cannot rescue Region A.</li>
    <li><b>Smart distribution on</b>: water-fills CW &rarr; RW (prioritises the starved region) and RW &rarr; Store (within region only). Cannot reroute units already at stores or already at an RW.</li>
    <li><b>Total chain LT 5 wk</b> (Agile defaults). Upstream LT is dead weight here (no production happens), so it matters only via the CW &rarr; RW &rarr; Store transit at the back end (2 wks).</li>
  </ul>
</div>

{''.join(split_block(sp) for sp in REGION_SPLITS)}

<div class="charts">
  <div class="chart">
    <h3>Margin (€) per scenario — sorted</h3>
    {''.join(margin_bar(r) for r in sorted_r)}
  </div>
  <div class="chart">
    <h3>Service Region A vs B</h3>
    {''.join(svc_pair(r) for r in results)}
  </div>
</div>

<div class="takeaway">
  <b>The 0/0/100 column tells the W0-pre-positioning story.</b> All 2,600 units are pushed to stores from week 0, split 50/50 between regions because the demand split is unknown at W0. Once the run starts, no inter-region transfer is possible and no production fills the gap.
  <ul style="margin:6px 0 10px 18px;padding:0;">
    <li><b>At 50/50</b> demand: Region A sells 1,300 from its 1,300 store stock and Region B does the same &mdash; perfect match, 100% service.</li>
    <li><b>At 70/30</b>: Region A has 1,300 units of stock but 1,820 units of demand &rarr; 520 units missed, A service <b>71.4%</b>. Region B has 1,300 units of stock for 780 units of demand &rarr; 520 units of stranded surplus, B service 100%. There is no CW or RW pool to redistribute, so B's surplus is unreachable.</li>
    <li><b>At 90/10</b>: same dynamic, sharper &mdash; A faces 2,340 demand on 1,300 units, missing 1,040 (A service <b>55.6%</b>). Margin tips negative: <b>&euro;{worst['margin']:+,.0f}</b>.</li>
  </ul>
  <b>The 0/30/70 column behaves nearly identically</b> at skewed splits: stock placed at RW A or RW B (split 50/50 by hypothesis) is locked inside that region's chain. Only the 30/30/40 column has a CW pool, and that pool can flow to whichever region the planner discovers to be short.<br><br>
  <b>Best margin:</b> {best['split_lbl']} &middot; {best['dist']} &rarr; <b>&euro;{best['margin']:+,.0f}</b> ({best['margin_pct_rev']:+.1f}%); service <b>{best['svc']:.1f}%</b>.<br>
  <b>Worst margin:</b> {worst['split_lbl']} &middot; {worst['dist']} &rarr; <b>&euro;{worst['margin']:+,.0f}</b>; Region A service <b>{worst['svc_a']:.1f}%</b>.<br>
  <b>30/30/40 wins at every skewed split</b> &mdash; the CW pool (30% of total stock = 780 units) is the only buffer the smart allocator can re-route once it learns the actual demand split.
</div>
</body></html>"""

with open('docs/regional_9_synthesis_oneshot.html', 'w') as f:
    f.write(html)
print("Wrote docs/regional_9_synthesis_oneshot.html\n")

# Console
print(f"{'split':>6} {'dist':>12} | {'svc':>6} {'svcA':>6} {'svcB':>6} | {'sales':>5} {'miss':>4} {'sellt':>5} | {'margin':>10} {'%rev':>6}")
print('-'*98)
for r in results:
    print(f"{r['split_lbl']:>6} {r['dist']:>12} | "
          f"{r['svc']:>5.1f}% {r['svc_a']:>5.1f}% {r['svc_b']:>5.1f}% | "
          f"{r['sales']:>5} {r['missed']:>4} {r['sell_through']:>4.1f}% | "
          f"€{r['margin']:>+9,.0f} {r['margin_pct_rev']:>+5.1f}%")
