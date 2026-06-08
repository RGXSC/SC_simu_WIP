"""9 regional scenarios on a LONGER upstream LT (10 wk).

Upstream: mat=5, semi=3, fp=2 (sum=10).
Distribution: cw_rw=1, rw_store=1 (unchanged, Agile downstream).
Total LT = 12 wks. Coverage = LT + freq = 13 wks.
Init sizing rule (LT+1) -> 13 wks of demand x 100/wk = 1300 units.
Flat demand 100/wk x 26 wks, N=50 per region, no prod_cap, Smart distribution.
"""
import sim_regional as s

WEEKS, DEMAND = 26, [100]*26
N_PER_REGION = 50
TOTAL_INIT = 1300
MAT_LT, SEMI_LT, FP_LT = 5, 3, 2
CW_RW_LT, RW_STORE_LT = 1, 1
PRICE, VC, FIXED_PCT = 10.0, 5.0, 0.20

REGION_SPLITS = [50, 70, 90]
STOCK_DISTS = [
    ('0/0/100',  0,   0, 100),
    ('0/30/70',  0,  30,  70),
    ('30/30/40', 30, 30,  40),
]

def run(sp, cwp, rwp, stp):
    init_cw    = int(round(TOTAL_INIT * cwp / 100))
    init_rw    = int(round(TOTAL_INIT * rwp / 100))
    init_store = TOTAL_INIT - init_cw - init_rw
    return s.run_simulation_regional(
        weeks=WEEKS, n_per_region=N_PER_REGION, demand_curve=DEMAND,
        region_split_pct=sp,
        mat_lt=MAT_LT, semi_lt=SEMI_LT, fp_lt=FP_LT,
        cw_rw_lt=CW_RW_LT, rw_store_lt=RW_STORE_LT,
        order_freq=1,
        init_rawmat=0, init_semi=0,
        init_cw=init_cw, init_rw_total=init_rw, init_store_total=init_store,
        cap_start=1000, cap_ramp=0.0,
        smart_distrib=True, prod_cap=None,
        var_cost=VC, price=PRICE, fixed_pct=FIXED_PCT,
        base_forecast=100,
    )

results = []
for sp in REGION_SPLITS:
    for label, cwp, rwp, stp in STOCK_DISTS:
        r = run(sp, cwp, rwp, stp)
        results.append({
            'split': f"{sp}/{100-sp}", 'dist': label,
            'svc': r['svc'], 'svc_a': r['svc_a'], 'svc_b': r['svc_b'],
            'sales': r['tot_sales'], 'produced': r['tot_produced'],
            'sell_through': r['sell_through'], 'end_stock': r['end_chain_stock'],
            'revenue': r['revenue'], 'cogs': r['cogs'],
            'margin': r['margin'], 'margin_pct_rev': r['margin_pct_rev'],
        })

print("="*120)
print(f"9 Regional Scenarios — UPSTREAM 10 WK (mat=5 semi=3 fp=2), dist 1+1, Flat 100/wk x 26 wk, init=1300, N=50/reg")
print("="*120)
print(f"{'split':>6} {'dist':>10} | {'svc':>6} {'svcA':>6} {'svcB':>6} | {'sales':>5} {'prod':>5} {'sellt':>6} | {'rev':>7} {'cogs':>6} {'margin':>9} {'%rev':>6}")
print('-'*120)
for r in results:
    print(f"{r['split']:>6} {r['dist']:>10} | "
          f"{r['svc']*100:>5.1f}% {r['svc_a']*100:>5.1f}% {r['svc_b']*100:>5.1f}% | "
          f"{r['sales']:>5} {r['produced']:>5} {r['sell_through']*100:>5.1f}% | "
          f"{r['revenue']:>7.0f} {r['cogs']:>6.0f} {r['margin']:>+9.0f} {r['margin_pct_rev']*100:>+5.1f}%")

# HTML synthesis
def html(results):
    rows = []
    for r in results:
        mc = "#1a8a4a" if r['margin'] > 0 else "#c0392b"
        sc = "#1a8a4a" if r['sell_through'] >= 0.80 else ("#e67e22" if r['sell_through'] >= 0.60 else "#c0392b")
        vc = "#1a8a4a" if r['svc'] >= 0.95 else ("#e67e22" if r['svc'] >= 0.80 else "#c0392b")
        rows.append(f"""
          <tr><td class="lbl">{r['split']}</td><td class="lbl">{r['dist']}</td>
            <td style="color:{vc};font-weight:600;">{r['svc']*100:.1f}%</td>
            <td>{r['svc_a']*100:.1f}% / {r['svc_b']*100:.1f}%</td>
            <td>{r['sales']:,}</td><td>{r['produced']:,}</td>
            <td style="color:{sc};font-weight:600;">{r['sell_through']*100:.1f}%</td>
            <td>{r['revenue']:,.0f}</td><td>{r['cogs']:,.0f}</td>
            <td style="color:{mc};font-weight:600;">{r['margin']:+,.0f}</td>
            <td style="color:{mc};">{r['margin_pct_rev']*100:+.1f}%</td></tr>""")
    best = max(results, key=lambda x: x['margin'])
    worst = min(results, key=lambda x: x['margin'])
    spread = best['margin'] - worst['margin']
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>9 Regional Scenarios — Longer LT</title>
<style>body{{font-family:Inter,Calibri,sans-serif;margin:20px 32px;color:#1a2a40;background:#fafbfc}}
h1{{font-size:22px;margin:0 0 4px}}.sub{{color:#5a6a80;font-size:12.5px;margin-bottom:18px}}
table{{border-collapse:collapse;width:100%;font-size:12.5px;background:#fff;box-shadow:0 1px 2px rgba(0,0,0,.05)}}
th{{background:#1a2a40;color:#fff;padding:9px 10px;text-align:left;font-weight:600;font-size:11.5px;
letter-spacing:.3px;text-transform:uppercase}}td{{padding:8px 10px;border-bottom:1px solid #ecf0f4;text-align:right}}
td.lbl{{text-align:left;font-weight:600;color:#2c3e56}}tr:hover{{background:#f5f8fb}}
.takeaway{{margin-top:18px;padding:14px 18px;background:#fff;border-left:4px solid #1a8a4a;font-size:12.5px;
line-height:1.55;box-shadow:0 1px 2px rgba(0,0,0,.05)}}.takeaway b{{color:#1a2a40}}
.params{{margin-bottom:12px;font-size:11.5px;color:#5a6a80}}
.pill{{display:inline-block;padding:2px 8px;border-radius:10px;background:#eef2f6;color:#2c3e56;
margin-right:6px;font-weight:600}}</style></head><body>
<h1>9 Regional Scenarios — Longer Upstream LT (10 wk)</h1>
<div class="sub">Upstream LTs mat=5 / semi=3 / fp=2 (sum 10 wk) · downstream CW→RW=1, RW→Store=1 · Flat 100/wk × 26 wk · 50 stores per region · 1,300 units total initial stock (= LT+1) · no production cap · Smart distribution</div>
<div class="params">
  <span class="pill">Total demand 2,600</span>
  <span class="pill">Total LT 12 wk</span>
  <span class="pill">Coverage 13 wk</span>
  <span class="pill">Price €10/u · VC €5/u · Fixed €5,200</span>
</div>
<table><thead><tr>
  <th>Region A / B split</th><th>Stock CW / RW / Stores</th>
  <th>Service</th><th>Service A / B</th>
  <th>Sales</th><th>Produced</th><th>Sell-through</th>
  <th>Revenue (€)</th><th>COGS (€)</th><th>Margin (€)</th><th>Margin / rev</th>
</tr></thead><tbody>{''.join(rows)}</tbody></table>
<div class="takeaway">
  <b>Best margin:</b> {best['split']}, dist {best['dist']} → €{best['margin']:+,.0f} ({best['margin_pct_rev']*100:+.1f}%).<br>
  <b>Worst margin:</b> {worst['split']}, dist {worst['dist']} → €{worst['margin']:+,.0f}.<br>
  <b>Spread across the 9 cells: €{spread:,.0f}</b> (vs ~€125 on the short-LT run — see other slide).
</div></body></html>"""

with open('docs/regional_9_synthesis_longLT.html', 'w') as f:
    f.write(html(results))
print("\nWrote docs/regional_9_synthesis_longLT.html")
