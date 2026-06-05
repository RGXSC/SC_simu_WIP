"""Generate user guide and dev guide as .docx files."""
from docx import Document
from docx.shared import Pt, RGBColor, Inches, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.enum.table import WD_ALIGN_VERTICAL
from docx.oxml.ns import qn
from docx.oxml import OxmlElement


# ─── helpers ─────────────────────────────────────────────────────────

def _shade(cell, hex_color):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement('w:shd')
    shd.set(qn('w:val'), 'clear')
    shd.set(qn('w:fill'), hex_color)
    tc_pr.append(shd)


def style_doc(doc):
    """Set default Calibri 10pt + tighter margins."""
    style = doc.styles['Normal']
    style.font.name = 'Calibri'
    style.font.size = Pt(10.5)
    for section in doc.sections:
        section.top_margin = Cm(2)
        section.bottom_margin = Cm(2)
        section.left_margin = Cm(2.2)
        section.right_margin = Cm(2.2)


def H1(doc, text):
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.bold = True
    r.font.size = Pt(20)
    r.font.color.rgb = RGBColor(0x1A, 0x2A, 0x40)
    p.paragraph_format.space_before = Pt(12)
    p.paragraph_format.space_after = Pt(8)


def H2(doc, text):
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.bold = True
    r.font.size = Pt(14)
    r.font.color.rgb = RGBColor(0x2C, 0x3E, 0x56)
    p.paragraph_format.space_before = Pt(10)
    p.paragraph_format.space_after = Pt(4)


def H3(doc, text):
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.bold = True
    r.font.size = Pt(11.5)
    r.font.color.rgb = RGBColor(0x4A, 0x62, 0x80)
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after = Pt(2)


def P(doc, text, *, italic=False, small=False):
    p = doc.add_paragraph()
    r = p.add_run(text)
    if italic: r.italic = True
    if small: r.font.size = Pt(9.5)
    p.paragraph_format.space_after = Pt(4)
    return p


def BULLETS(doc, items):
    for item in items:
        p = doc.add_paragraph(style='List Bullet')
        p.add_run(item)
        p.paragraph_format.space_after = Pt(2)


def NUMBERED(doc, items):
    for item in items:
        p = doc.add_paragraph(style='List Number')
        p.add_run(item)
        p.paragraph_format.space_after = Pt(2)


def CODE(doc, text):
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.font.name = 'Consolas'
    r.font.size = Pt(9.5)
    r.font.color.rgb = RGBColor(0x1A, 0x2A, 0x40)
    p.paragraph_format.left_indent = Cm(0.5)
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(6)
    return p


def CALLOUT(doc, text):
    """Light grey box for tips / notes."""
    table = doc.add_table(rows=1, cols=1)
    cell = table.rows[0].cells[0]
    _shade(cell, 'F0F2F5')
    p = cell.paragraphs[0]
    r = p.add_run(text)
    r.font.size = Pt(10)
    return table


def TABLE(doc, headers, rows, widths_cm=None):
    t = doc.add_table(rows=1 + len(rows), cols=len(headers))
    t.style = 'Light Grid Accent 1'
    hdr = t.rows[0].cells
    for i, h in enumerate(headers):
        hdr[i].text = ''
        p = hdr[i].paragraphs[0]
        r = p.add_run(h)
        r.bold = True
        r.font.size = Pt(10)
        _shade(hdr[i], 'DCE3ED')
    for ri, row in enumerate(rows):
        cells = t.rows[ri + 1].cells
        for ci, val in enumerate(row):
            cells[ci].text = ''
            p = cells[ci].paragraphs[0]
            rr = p.add_run(str(val))
            rr.font.size = Pt(9.5)
    if widths_cm:
        for col, w in zip(t.columns, widths_cm):
            for cell in col.cells:
                cell.width = Cm(w)
    return t


def PAGE_BREAK(doc):
    p = doc.add_paragraph()
    r = p.add_run()
    r.add_break(WD_BREAK.PAGE)


# ─── content builders (filled in next chunks) ────────────────────────

def build_user_guide(path):
    doc = Document()
    style_doc(doc)
    # USER_GUIDE_BODY is filled in by subsequent edits
    USER_GUIDE_BODY(doc)
    doc.save(path)
    print(f"wrote {path}")


def build_dev_guide(path):
    doc = Document()
    style_doc(doc)
    DEV_GUIDE_BODY(doc)
    doc.save(path)
    print(f"wrote {path}")


def USER_GUIDE_BODY(doc):
    # ─── Page 1: Executive summary ──────────────────────────────
    H1(doc, "Supply Chain Agility Simulator — User Guide")
    P(doc, "Version 2 · April 2026 · single-file Streamlit app", italic=True)

    H2(doc, "Executive Summary")
    P(doc,
        "This tool simulates a 26-week, two-store supply chain across four physical "
        "stages (Material → Semi → Finishing+CW → Distribution). It compares Push and "
        "Agile architectures across three demand regimes (Flat, Growth, Drop) plus "
        "Seasonal patterns, and quantifies the financial impact of lead time, order "
        "frequency, stock distribution, and reactive distribution.")

    H3(doc, "What the simulator demonstrates")
    BULLETS(doc, [
        "Agility wins across regimes — short lead times + frequent reviews beat "
        "long lead times + heavy pre-positioning on margin and capital efficiency.",
        "Medium lead time is the dangerous middle — neither Push's brute-force stock "
        "nor Agile's short pipeline.",
        "Pre-positioned store stock sells immediately; pre-positioned upstream stock "
        "(RM/Semi) only flows once the planner places an order.",
        "Initial inventory is real pre-investment — valued at stage cost (RM 50%, "
        "Semi 75%, Finished 100%) and reflected in the P&L. Nothing is free.",
    ])

    H3(doc, "The four levers")
    BULLETS(doc, [
        "Lead time per stage — drives reactivity.",
        "Order frequency — periodic-review blindness between reviews.",
        "Initial stock total + distribution across the four buffers.",
        "Smart vs blind store allocation when CW pushes finished goods downstream.",
    ])

    H3(doc, "Quick start")
    NUMBERED(doc, [
        "Open the app in your browser.",
        "Click any of the 18 quick-scenario buttons in the left sidebar to load a preset.",
        "Use the week navigation buttons (W0 / −1 / +1 / W26) to step through time.",
        "Read the cumulative KPIs at the top, expand the P&L for the financial summary, "
        "and the Charts expander for trend views.",
        "Compare scenarios with the Save Scenario expander — first save is the baseline, "
        "subsequent saves show Δ vs base.",
    ])

    PAGE_BREAK(doc)

    # ─── Page 2: Introduction ───────────────────────────────────
    H1(doc, "1. Introduction")

    H2(doc, "Purpose")
    P(doc,
        "Supply chain debates often reduce to dogma: \"agility is always better\" vs "
        "\"economies of scale need long campaigns.\" Both positions ignore that the right "
        "answer depends on demand profile, lead times, and pre-investment. This simulator "
        "lets the numbers settle the argument under explicit, editable hypotheses.")

    H2(doc, "The physical model")
    P(doc,
        "A single product flows through four sequential stages, each with its own lead "
        "time and capacity. Two retail stores draw from a shared central warehouse (CW). "
        "Demand is split between the stores by a configurable percentage. The supplier "
        "ships raw material into the Material pipe; capacity ramps linearly once the "
        "factory is activated by the first order.")

    TABLE(doc,
          ["Stage", "Holds", "Cost valuation", "Default Agile LT", "Default Push LT"],
          [
              ["Material",   "Raw material in transit + RM buffer",       "50% of VC",  "4 wk",  "12 wk"],
              ["Semi",       "Semi-finished in transit + Semi buffer",    "75% of VC",  "2 wk",  "6 wk"],
              ["Finishing",  "Finished goods in transit + CW buffer",     "100% of VC", "1 wk",  "3 wk"],
              ["Distribution","Goods en route to a specific store",        "100% of VC", "1 wk",  "3 wk"],
          ],
          widths_cm=[2.7, 5.2, 2.6, 2.5, 2.5])

    H2(doc, "Two stores, smart distribution")
    P(doc,
        "Store A receives the demand share configured in the sidebar (default 60%); "
        "Store B receives the remainder (default 40%). Initial store stock is always "
        "split 50/50 — the planner has not yet reviewed when the simulation begins. "
        "From the first planning review onward, smart distribution rebalances CW pushes "
        "to equalize weeks-of-cover between stores before splitting by demand rate.")

    PAGE_BREAK(doc)

    # ─── Page 3: Lead times, coverage, frequency ────────────────
    H1(doc, "2. Lead Times, Coverage, and Frequency")

    H2(doc, "Physical lead time")
    P(doc,
        "Physical lead time = sum of stage lead times. With Agile defaults (4+2+1+1) it "
        "is 8 weeks; with Push defaults (12+6+3+3) it is 24 weeks. This is the time "
        "between placing a supplier order and the resulting unit being available at "
        "a store.")

    H2(doc, "Order frequency and coverage")
    P(doc,
        "The order frequency is the cadence at which the planner reviews stock and "
        "places replenishment orders. Between reviews, the planner is blind to demand "
        "changes — the forecast value is only updated on review weeks.")
    P(doc,
        "Coverage = physical lead time + order frequency. This is the number of weeks "
        "of demand the system must hold to avoid stockouts between reviews. It drives "
        "both the recommended initial stock and the in-loop ordering rule.")

    H2(doc, "Per-stage coverage (each push point has its own horizon)")
    P(doc,
        "The planner does not place a single order to the supplier. Each of the four "
        "push points reviews its own position and places its own order, against a "
        "coverage window that spans only the lead time DOWNSTREAM of that point plus "
        "the order frequency. A unit released at a downstream stage needs less lead "
        "time to reach a store, so it carries a shorter coverage target:")
    TABLE(doc,
          ["Push point", "Order target", "Coverage window (weeks)"],
          [
              ["Supplier (RM in)",  "od_sup",  "mat_lt + semi_lt + fp_lt + dist_lt + freq"],
              ["Semi (RM → Semi)",  "od_semi", "semi_lt + fp_lt + dist_lt + freq"],
              ["Finishing (Semi → FP)", "od_fp",   "fp_lt + dist_lt + freq"],
              ["CW push (FP → store)",  "od_ship", "dist_lt + freq"],
          ],
          widths_cm=[3.8, 2.6, 7.2])

    H2(doc, "The ordering rule (periodic review, multi-echelon targets)")
    CODE(doc,
        "if w in review_weeks:\n"
        "    for stage in [sup, semi, fp, ship]:\n"
        "        target_x   = forecast × coverage_x        # demand to cover\n"
        "        existing_x = (stores + every unit already AT or DOWNSTREAM\n"
        "                      of this stage's push point) + this stage's backlog\n"
        "        order_x    = max(0, target_x − existing_x)\n"
        "        backlog_x += order_x                       # accrues to the stage")
    P(doc,
        "Each stage's existing-count includes only inventory from that stage's push "
        "point downstream, plus that stage's own outstanding backlog — so consecutive "
        "reviews never double-count, and an upstream order does not suppress a "
        "downstream one. The four orders accrue to four independent backlogs (supplier "
        "pre-buffer, semi, fp, ship). Each processing or shipping step later in the "
        "week consumes against its OWN backlog: RM→Semi draws down the semi backlog, "
        "Semi→FP the fp backlog, and the CW→store push the ship backlog. This is what "
        "‘advanced stage ordering' means — every stage pulls work forward on its own "
        "shorter horizon instead of waiting for one chain-level order to propagate.")
    P(doc,
        "On non-review weeks, all orders are zero. The forecast updates only on review "
        "weeks (the planner re-bases on the latest observed demand). This intentionally "
        "models the periodic-review information lag that hurts Push more than Agile.")

    H2(doc, "Per-store gap adjustment (smart distribution only)")
    P(doc,
        "Pooled stage targets answer ‘does the chain hold enough in aggregate?' but can "
        "miss the case where one store is starving while another sits on surplus. When "
        "smart distribution is on, each stage also computes a per-store gap: it works "
        "out the cover-equalizing share of the common upstream pool that the CW push "
        "will actually deliver, then sums each store's remaining shortfall. The final "
        "stage order is the MAX of the pooled order and this per-store gap, so ordering "
        "and execution use the same allocation logic.")

    H2(doc, "Capacity ramp")
    P(doc,
        "Each stage has a starting weekly capacity (default 100 pcs/wk) and a linear "
        "ramp-up percentage (default +20%/wk). Ramp counters only advance after the "
        "first supplier order is placed — the factory does not warm up on the calendar "
        "but on commercial signal.")

    PAGE_BREAK(doc)

    # ─── Page 4: Forecasting and replenishment ──────────────────
    H1(doc, "3. Forecasting and Replenishment Logic")

    H2(doc, "What the planner sees (two forecast regimes)")
    P(doc,
        "The simulator runs one of two forecast regimes depending on the demand "
        "profile:")
    BULLETS(doc, [
        "Flat / Ramp / Drop (no seasonal curve): on each review week the planner "
        "observes the current week's actual demand and treats it as the flat "
        "forward-looking rate. Each stage target is simply forecast × coverage_x. "
        "No smoothing layer — the simplest possible signal, so the comparison "
        "isolates the structural effects of lead time and review frequency.",
        "Seasonal: the planner holds a believed demand SHAPE (the planner curve) "
        "but does not know its MAGNITUDE until the season starts. Targets are a "
        "forward look-ahead over that curve, scaled by a factor discovered at the "
        "first review (next subsection).",
    ])

    H2(doc, "Seasonal: actual sales guessed in the first review period")
    P(doc,
        "A seasonal planner knows the season is, say, ‘steep, peaking around week 6,' "
        "but cannot know in advance whether this year sells at an average of 30, 100 "
        "or 300 a week. The simulator models exactly this. The planner curve encodes "
        "the believed shape, normalized to an average of 100/wk. On the FIRST review "
        "week the planner compares cumulative actual demand so far against the "
        "cumulative curve over the same weeks and locks a single scaling factor:")
    CODE(doc,
        "# locked once, at the first review week only\n"
        "f = Σ actual_demand[1..w]  /  Σ planner_curve[1..w]")
    P(doc,
        "From then on, every stage target is a forward sum over the believed curve, "
        "scaled by that discovered factor:")
    CODE(doc,
        "target_x = f × Σ planner_curve[w+1 .. w+coverage_x]   (capped at sim end)")
    P(doc,
        "So if the believed average was 100/wk but the first period sells through at "
        "roughly 3×, the planner locks f ≈ 3.0 and scales its entire forward plan up "
        "by 3× — it has ‘guessed' the season's magnitude from the opening weeks and "
        "commits to it. The factor is snapped to a clean integer (or 0.1 increment) "
        "when the observed ratio is within rounding-drift distance, because the "
        "integer weekly demand the presets generate produces ratios that are nearly "
        "but not exactly clean (e.g. an avg-300 curve against an avg-100 belief reads "
        "as ~2.99 rather than 3.00). Snapping keeps the displayed factor readable "
        "without changing behaviour. Once locked, the factor never re-bases — a wrong "
        "first guess is carried for the rest of the season, which is the whole point: "
        "long-cycle planners live with their opening-period read.")

    H2(doc, "What goes into the existing-stock count")
    P(doc,
        "When computing existing inventory for the order calculation, the simulator "
        "counts every unit in the system that has already been paid for or committed:")
    BULLETS(doc, [
        "Stocks at both stores",
        "All units in transit (Material, Semi, Finishing, and Distribution pipes)",
        "Buffers between stages (RM, Semi, CW)",
        "Supplier backlog (orders placed but not yet shipped)",
    ])
    P(doc,
        "This means that as the supplier accumulates a backlog, the planner does NOT "
        "re-order the same units — they are already on the books.")
    P(doc,
        "The list above is the SUPPLIER stage's existing-count. Each downstream stage "
        "uses a narrower slice: the Semi order ignores raw material and the Material "
        "pipe (those cannot reach a store within its shorter coverage), the Finishing "
        "order ignores everything upstream of the FP pipe, and the CW push counts only "
        "store stock plus distribution pipes. Each stage also adds its own backlog, "
        "never another stage's, so the four orders stay independent.")

    H2(doc, "Initial stock recommendation")
    P(doc,
        "The sidebar shows a smart recommendation that adapts to the demand shape: it "
        "sums the actual demand over the first ‘coverage' weeks of the curve. This "
        "matters most for ramps and seasonal profiles where the early weeks differ from "
        "the average.")

    H2(doc, "Smart vs Push distribution")
    P(doc,
        "Push 50/50 always allocates CW shipments equally between stores, ignoring the "
        "demand split. Smart distribution, after the first review, computes weeks-of-"
        "cover per store and prioritizes the worst-covered store before splitting the "
        "remainder by demand rate. The first CW shipment is still 50/50 because the "
        "planner has not yet reviewed.")

    PAGE_BREAK(doc)

    # ─── Page 5-6: Cost accounting ──────────────────────────────
    H1(doc, "4. Cost Accounting (Entering-Stage)")

    H2(doc, "Three valorization rates")
    P(doc,
        "A finished unit costs €200 in variable cost (default). Costs accumulate as "
        "the unit moves through the chain:")
    TABLE(doc,
          ["Stage entered", "Cumulative valorization", "Incremental cost"],
          [
              ["Raw Material (entry into Material pipe)", "50% of VC = €100", "€100 booked"],
              ["Semi (entry into Semi pipe)",             "75% of VC = €150", "+€50 booked"],
              ["Finished (entry into FP pipe)",            "100% of VC = €200", "+€50 booked"],
          ],
          widths_cm=[5.5, 4.8, 4.5])

    H2(doc, "When costs are booked")
    P(doc,
        "Costs are booked at the moment a unit ENTERS each stage — i.e., when the "
        "supplier ships it into the Material pipe, when RM-to-Semi processing happens, "
        "and when Semi-to-FP processing happens. There is no anticipation and no double "
        "counting. The Debug expander asserts unit and value conservation at end of run.")

    CODE(doc,
        "cost_mat  = shipped × VC × 0.50   # supplier ship → mat_pipe\n"
        "cost_semi = si      × VC × 0.25   # RM → Semi processing\n"
        "cost_fp   = fi      × VC × 0.25   # Semi → FP processing")

    H2(doc, "Initial stock value (the pre-investment)")
    P(doc,
        "Initial stock is treated as a real, sunk pre-investment. It is valued at "
        "the stage where it sits at sim start and added to total variable cost in the "
        "P&L:")
    CODE(doc,
        "init_stock_value = init_store    × VC × 1.00\n"
        "                 + init_cw       × VC × 1.00\n"
        "                 + init_semi     × VC × 0.75\n"
        "                 + init_rawmat   × VC × 0.50")
    P(doc,
        "At W0 there are zero production costs. This avoids double-counting the initial "
        "stock both via init_stock_value and via the production-cost columns.")

    H2(doc, "Fixed cost")
    P(doc,
        "Fixed cost is computed as a percentage (default 45%) of the simulation-period "
        "FORECAST revenue, not the actual revenue. This represents factory overhead, "
        "salaries, depreciation — costs that are committed regardless of how many units "
        "actually sell.")

    H2(doc, "P&L identity")
    CODE(doc,
        "Revenue = total_sales × price\n"
        "Total VC = init_stock_value + Σ cost_mat + Σ cost_semi + Σ cost_fp\n"
        "Gross margin = Revenue − Total VC\n"
        "Net margin   = Gross margin − Fixed cost\n"
        "\n"
        "Identity (must hold exactly):\n"
        "    Revenue == Total VC + Fixed + Margin")
    P(doc,
        "Below the net margin, the P&L shows a separate \"Leftover Stock + WIP\" line "
        "as an asset — units that were paid for but not sold. Not subtracted from "
        "margin (the variable cost was already booked); shown as a measure of stranded "
        "capital.")

    PAGE_BREAK(doc)

    # ─── Page 7: Permanent presets ──────────────────────────────
    H1(doc, "5. Permanent Presets (Flat / Growth / Drop)")

    H2(doc, "What ‘Permanent' means")
    P(doc,
        "These nine presets — three lead-time profiles × three demand shapes — represent "
        "the steady-state operating modes of a real supply chain. Demand is either "
        "permanently flat at 100/wk, ramping up to 300/wk, or dropping to 30/wk.")

    H2(doc, "Stock sizing rule")
    P(doc,
        "Initial stock is auto-sized to coverage × base forecast (= 100). For Agile "
        "(coverage 9) this gives 900 pcs; for Medium (coverage 18) 1 800; for Push "
        "(coverage 28) 2 800.")

    H2(doc, "Distributions")
    TABLE(doc,
          ["Profile", "Store %", "WH %", "Semi %", "RM %", "Order freq"],
          [
              ["Agile",  "60",  "20",  "10",  "10",  "1 wk"],
              ["Medium", "80",  "20",  "0",   "0",   "2 wk"],
              ["Push",   "100", "0",   "0",   "0",   "4 wk"],
          ],
          widths_cm=[2.5, 2.0, 2.0, 2.0, 2.0, 2.5])

    H2(doc, "What this teaches")
    BULLETS(doc, [
        "Flat demand: Push survives because there is no surprise. Margin gap to Agile "
        "is mostly the order-frequency overhead of carrying coverage stock for 4 weeks "
        "instead of 1.",
        "Growth: Agile wins decisively. Push cannot ramp inside its 24-week pipeline; "
        "much of the demand becomes lost sales.",
        "Drop: Agile wins by NOT producing. With initial RM/Semi gated behind the first "
        "order, the factory stays idle and the firm avoids producing inventory it "
        "cannot sell. Push, with all stock at store, sells what it has but writes off "
        "the rest.",
    ])

    PAGE_BREAK(doc)

    # ─── Page 8: Seasonal presets ───────────────────────────────
    H1(doc, "6. Seasonal Presets (Steep curve, three averages)")

    H2(doc, "What ‘Seasonal' means here")
    P(doc,
        "These nine presets share a Steep gamma demand curve — peaking at week 6, with "
        "a magnitude roughly 2.4× the weekly average. The three averages 30 / 100 / "
        "300 stress different parts of the agility argument: a low-demand seasonal "
        "(total 780 pcs) tests over-investment cost; the high-demand seasonal (total "
        "7 800 pcs) tests pipeline reactivity at peak.")

    H2(doc, "Stock sizing rule (sell-through hypothesis)")
    P(doc,
        "Longer lead times must over-order to hedge demand uncertainty. The simulator "
        "encodes this via a sell-through target: initial stock = base seasonal stock "
        "(2 600 pcs) divided by the LT profile's sell-through target.")
    TABLE(doc,
          ["Profile", "Sell-through target", "Initial stock"],
          [
              ["Agile",  "100% (no hedge needed — can react)", "2 600 pcs"],
              ["Medium", "85%  (15% buffer)",                  "3 059 pcs"],
              ["Push",   "60%  (40% buffer)",                  "4 333 pcs"],
          ],
          widths_cm=[2.5, 7.5, 2.5])
    P(doc,
        "The hypothesis is observable: ask any actor in a long-cycle business and "
        "they will quote a planned shortfall — \"we know 30-40% of pre-positioned stock "
        "will end up in clearance.\" The simulator turns this into a quantifiable "
        "P&L line.")

    H2(doc, "Distributions")
    TABLE(doc,
          ["Profile", "Store %", "WH %", "Semi %", "RM %"],
          [
              ["Agile",  "40",  "20",  "10",  "30"],
              ["Medium", "70",  "20",  "10",  "0"],
              ["Push",   "100", "0",   "0",   "0"],
          ],
          widths_cm=[2.5, 2.5, 2.5, 2.5, 2.5])

    H2(doc, "What this teaches")
    P(doc,
        "On low-demand seasonal (avg 30), both architectures lose money — the pre-"
        "investment dwarfs realized revenue. On high-demand seasonal (avg 300), "
        "Push captures more absolute margin (it pre-positioned correctly) but Agile "
        "delivers higher margin percentage and far better capital efficiency. The "
        "real story: Push's high-margin scenario is funded by the catastrophic loss it "
        "takes on the low-demand scenario.")

    PAGE_BREAK(doc)


    # ─── Page 9: Initial WIP gating + CW push ───────────────────
    H1(doc, "7. Initial WIP Gating and CW Push")

    H2(doc, "The gating rule")
    P(doc,
        "Initial Raw Material and Semi-finished stock do NOT flow forward at simulation "
        "start. They sit in their buffers until the planner places the first supplier "
        "order, which activates the factory. From that week onward, Semi (RM→Semi) and "
        "FP (Semi→FP) processing run every week, capacity-limited.")
    P(doc,
        "This rule is essential to the Drop scenario: when demand drops, the planner "
        "sees that existing stock is sufficient and never orders. The factory therefore "
        "never activates, the initial RM/Semi stays put, and the firm avoids producing "
        "useless inventory. If RM/Semi were processed automatically, the Drop scenario "
        "would lose its core teaching value.")

    H2(doc, "Kickstart toggle (default OFF)")
    P(doc,
        "The sidebar exposes a Kickstart toggle. With Kickstart ON, the factory does "
        "one round of Semi+FP processing at W1 even before the first order. This is "
        "a workaround for very-low-demand seasonal scenarios where the planner would "
        "otherwise never order — but it is OFF by default in all 18 presets, because "
        "the cleaner teaching message is: ‘over-positioned upstream stock that never "
        "gets used is wasted pre-investment.'")

    H2(doc, "CW → Store push (always ON)")
    P(doc,
        "CW (the central warehouse) holds finished goods and pushes them downstream "
        "every week regardless of order activity. The reasoning: CW-to-store is internal "
        "logistics, not procurement — once goods are finished, the firm allocates them "
        "to wherever they sell. The first CW shipment always splits 50/50 between "
        "stores; from the first review onward, smart distribution prioritizes the worst-"
        "covered store.")

    H2(doc, "What you will observe in the diagram")
    BULLETS(doc, [
        "Drop scenarios: si=0 and fi=0 (no Semi or FP processing) all the way through. "
        "RM and Semi buffers do not move.",
        "Flat / Growth / Seasonal at peak: factory activates at the first review, then "
        "you see the supplier shipping, capacity ramping each week, and the pipes "
        "filling.",
        "Seasonal at low average (e.g., avg=30): if the planner never orders, the upstream "
        "buffers stay frozen for all 26 weeks. Final P&L shows a large leftover-stock "
        "asset and a deeply negative net margin — the teaching point.",
    ])

    PAGE_BREAK(doc)

    # ─── Page 10: Reading the diagram ───────────────────────────
    H1(doc, "8. Reading the Flow Diagram")

    H2(doc, "Layout")
    P(doc,
        "The diagram reads left-to-right as physical flow: Supplier → Material → Semi → "
        "Finishing+CW → Distribution → Stores. Each stage band is a row of week-boxes; "
        "the leftmost box is the upstream end (just entered) and the rightmost box is "
        "the downstream end (about to exit / arrive at the next stage). Inside each "
        "stage the rightmost box also sums in the buffer stock waiting at that stage.")

    H2(doc, "Week boxes")
    BULLETS(doc, [
        "Filled grey-blue (#4a6280) box with a number: that many units in transit at "
        "this week of the stage.",
        "Empty light-grey box: no units at that pipe position.",
        "Subtle left inset shadow on the rightmost box of each stage: that is the "
        "‘exit slot' — units there will arrive at the next stage on the following week.",
    ])

    H2(doc, "Cards")
    BULLETS(doc, [
        "Supplier card (left, dark navy): shows Backlog (orders placed but not yet "
        "shipped) and Cap (current weekly shipping capacity).",
        "Store cards (right, stacked A on top of B): Stock (current units), Dem "
        "(this week's demand share), Sold (this week's sales), and a red LOST badge "
        "if any units were missed.",
        "Order chip on the supplier band: the order quantity placed THIS week, if any.",
    ])

    H2(doc, "Info bar (top of diagram)")
    P(doc,
        "Compact strip showing Backlog, Pending (orders placed minus units arrived at "
        "stores), WIP (total in-system units), the active Order this week, current "
        "Forecast value, and the configured A/B demand split.")

    H2(doc, "WIP labels")
    P(doc,
        "Below each stage band, a WIP label sums all units currently in that stage's "
        "pipe + its buffer. Distribution shows two lines — WIP A and WIP B — because "
        "each store has its own dedicated pipe.")

    PAGE_BREAK(doc)

    # ─── Page 11: KPIs and the P&L ──────────────────────────────
    H1(doc, "9. KPIs and the P&L")

    H2(doc, "The seven KPI cards")
    P(doc,
        "Above the diagram, seven cards summarize cumulative results from W1 to the "
        "current week. They update as you navigate the timeline.")
    TABLE(doc,
          ["KPI", "Meaning", "Color logic"],
          [
              ["Service Level",  "Sales ÷ demand",                       "Red <60%, Amber <85%, Green ≥85%"],
              ["Cumul. Sales",   "Total units sold so far",              "Blue"],
              ["Missed Total",   "Units of demand not fulfilled",        "Red"],
              ["Missed A",       "Per-store missed sales (Store A)",     "Red"],
              ["Missed B",       "Per-store missed sales (Store B)",     "Purple"],
              ["Stockout Wks",   "Weeks with at least one missed unit",  "Green if 0, else red"],
              ["Useful Prod.",   "Sold ÷ (sold + remaining stock+pipe)", "Red <50%, Amber <80%, Green ≥80%"],
          ],
          widths_cm=[3.0, 7.5, 4.5])

    H2(doc, "P&L summary expander")
    P(doc,
        "At the bottom of the page, the P&L expander shows the full income statement "
        "computed from the entering-stage cost convention. Key rows:")
    BULLETS(doc, [
        "Revenue = total sales × selling price.",
        "Initial stock (pre-investment): valued at stage cost.",
        "Purchasing / Semi / Finishing: cumulative production costs at each stage.",
        "Total Variable Cost: initial stock + all production costs.",
        "Fixed cost: % of forecast revenue, simulation-period total.",
        "Net Margin: revenue − VC − fixed.",
        "Leftover Stock + WIP (asset, shown below margin): valorized leftover units.",
    ])

    H2(doc, "Production efficiency")
    P(doc,
        "Below the P&L table, three metrics show how productively the firm used its "
        "resources: Service Level, Sold (Useful) — units sold as a share of all units "
        "the system processed — and Remaining WIP+stock — units that ended up stranded "
        "as inventory.")

    H2(doc, "Reconciliation guarantee")
    P(doc,
        "The simulator enforces Revenue = VC + Fixed + Margin to within €0.01. If the "
        "Debug toggle is on, the Debug expander surfaces five conservation checks that "
        "must all pass.")

    PAGE_BREAK(doc)

    # ─── Page 12: Saving scenarios + tips ───────────────────────
    H1(doc, "10. Saving Scenarios and Working Tips")

    H2(doc, "Save and compare")
    P(doc,
        "The Save Scenario expander lets you persist the current configuration with a "
        "name (auto-generated by default). The first save is the baseline; subsequent "
        "saves show a Δ vs base column on margin, so you can quickly read the marginal "
        "impact of each change.")
    BULLETS(doc, [
        "Click a preset, save it as ‘baseline'.",
        "Change one parameter (e.g. doubling the order frequency).",
        "Re-save with a name reflecting the change.",
        "Read the Δ column to attribute the margin difference to that one variable.",
    ])

    H2(doc, "Working tips")
    BULLETS(doc, [
        "Always start from a preset. The 18 quick-scenario buttons set every parameter "
        "consistently — easier than configuring from scratch.",
        "Use the Debug toggle when something looks off. The reconciliation report "
        "tells you whether it is a bug or a misread.",
        "Hard-refresh (Ctrl+Shift+R) after a deploy to clear cached widget state.",
        "The cumulative KPIs at the top track where you are on the timeline. The P&L "
        "expander always shows end-of-simulation totals, not cumulative-to-current-week.",
    ])

    H2(doc, "Common pitfalls in interpretation")
    BULLETS(doc, [
        "100% service level is not a win in itself. Look at the margin and the leftover "
        "asset — sometimes 100% svc is achieved by carrying enormous stranded inventory.",
        "A negative margin on a low-demand scenario is the model working correctly. "
        "The pre-investment was disproportionate to realized demand — that is the "
        "lesson, not a glitch.",
        "Push at 100% service on Drop is misleading. The firm sold what it had on hand "
        "(initial store stock), but cumulative variable cost includes that initial "
        "stock value — net margin is therefore deeply negative.",
    ])

    H2(doc, "Where to find the file")
    P(doc,
        "The simulator is a single Python file (app.py) in the GitHub repository. The "
        "developer guide explains how to back it up, modify it, and re-deploy.",
        small=True)


def DEV_GUIDE_BODY(doc):
    H1(doc, "Supply Chain Agility Simulator — Developer & Deployment Guide")
    P(doc, "Version 2 · for users with no prior Python or Streamlit experience", italic=True)

    H2(doc, "Executive Summary")
    P(doc,
        "This guide walks you from zero (no Python, no Streamlit, no Git experience) "
        "through three operations: (1) backing up the existing app, (2) running it "
        "locally on your laptop to experiment with code changes, and (3) publishing it "
        "online via Streamlit Community Cloud. It assumes only that you have a Windows "
        "or Mac computer and an internet connection.")
    P(doc, "Total time, first-time end-to-end: about 60-90 minutes.")

    H3(doc, "Two routes")
    BULLETS(doc, [
        "ROUTE A — Cloud only (fastest): you only edit the file on GitHub and let "
        "Streamlit Cloud rebuild. No Python install needed locally. Good for small "
        "tweaks (text, colors, constants).",
        "ROUTE B — Local then publish (safer): you install Python, run the app on your "
        "machine, test changes, then push to GitHub when satisfied. Required for any "
        "non-trivial change.",
    ])

    PAGE_BREAK(doc)

    H1(doc, "1. Backing Up the Code")
    P(doc,
        "Before changing anything, make a copy. The application is one file: app.py "
        "in the GitHub repository. There are three independent backups you should set up.")

    H3(doc, "Backup 1 — Local copy")
    NUMBERED(doc, [
        "Open the GitHub repository in your browser: github.com/rgxsc/sc_simu_wip.",
        "Switch to the active branch: claude/rebuild-supply-chain-simulator-9LAVA.",
        "Click on app.py, then the ‘Raw' button at the top right of the file view.",
        "Right-click → Save As → save as app_backup_YYYYMMDD.py on your Desktop.",
        "Repeat for requirements.txt and slide_seasonal_4scenarios.html if you want a "
        "complete snapshot.",
    ])

    H3(doc, "Backup 2 — Git history (automatic)")
    P(doc,
        "Every time you push a change, Git records it. The repository's full history "
        "is your real backup. You can browse any prior state at: github.com/rgxsc/"
        "sc_simu_wip/commits/claude/rebuild-supply-chain-simulator-9LAVA. Click any "
        "commit to view the file as it was at that moment, or click ‘Browse files' to "
        "explore the entire repo at that commit.")

    H3(doc, "Backup 3 — A protected branch")
    NUMBERED(doc, [
        "On GitHub, click the branch dropdown.",
        "Type a new branch name like ‘backup-before-edits-2026-04' and press Enter "
        "→ ‘Create branch from claude/rebuild-supply-chain-simulator-9LAVA'.",
        "This frozen branch will never be touched by your future edits — a permanent "
        "rollback point.",
    ])

    CALLOUT(doc,
        "TIP: If you ever break things badly, the fastest recovery is to overwrite "
        "your active branch with the backup branch. The dev section ‘Rolling back' "
        "explains how.")

    PAGE_BREAK(doc)

    H1(doc, "2. Installing Python (Route B)")
    P(doc, "Skip this section if you are only doing Route A (cloud edits).")

    H3(doc, "Windows")
    NUMBERED(doc, [
        "Open https://www.python.org/downloads/ in your browser.",
        "Click the yellow ‘Download Python 3.12.x' button (any 3.11+ works).",
        "Run the installer. CRITICAL: tick ‘Add python.exe to PATH' on the first screen "
        "before clicking Install Now.",
        "After install, open the Start menu, type ‘cmd', press Enter to open Command Prompt.",
        "Type:  python --version  and press Enter. You should see ‘Python 3.12.x'. "
        "If not, restart the computer and try again.",
    ])

    H3(doc, "Mac")
    NUMBERED(doc, [
        "Open https://www.python.org/downloads/ in your browser.",
        "Click the macOS installer .pkg link, run it, and accept defaults.",
        "Open Terminal (Cmd+Space → ‘Terminal').",
        "Type:  python3 --version  and press Enter. You should see ‘Python 3.12.x'.",
    ])

    H3(doc, "Installing the simulator's dependencies")
    P(doc, "In the same terminal/command-prompt window, type:")
    CODE(doc, "pip install streamlit pandas numpy altair")
    P(doc,
        "Wait for download (~2 minutes on a normal connection). When it finishes, type:")
    CODE(doc, "streamlit --version")
    P(doc, "You should see something like ‘Streamlit, version 1.30.0' or higher.")

    PAGE_BREAK(doc)

    H1(doc, "3. Running Locally")

    H3(doc, "Get the code")
    NUMBERED(doc, [
        "Go to github.com/rgxsc/sc_simu_wip in your browser.",
        "Click the green ‘Code' button → ‘Download ZIP'.",
        "Unzip into a folder, e.g. C:\\sc_simu_wip (Windows) or ~/sc_simu_wip (Mac).",
    ])

    H3(doc, "Start the app")
    NUMBERED(doc, [
        "Open Command Prompt / Terminal.",
        "Navigate to the folder:  cd C:\\sc_simu_wip   (or  cd ~/sc_simu_wip on Mac).",
        "Type:  streamlit run app.py",
        "Your default browser opens automatically at http://localhost:8501 — the app is running.",
        "To stop the app, return to the terminal and press Ctrl+C.",
    ])

    CALLOUT(doc,
        "TIP: Keep the terminal window open while using the app. Closing it stops "
        "the app. Save your edits to app.py and the app will auto-reload (Streamlit "
        "watches the file).")

    H3(doc, "Edit the file")
    P(doc,
        "Open app.py in any text editor: Notepad, VS Code (free, recommended — see "
        "code.visualstudio.com), Sublime Text, anything. Save your changes; the running "
        "Streamlit app reloads within ~1 second.")

    PAGE_BREAK(doc)

    H1(doc, "4. Code Structure (where to find things)")

    P(doc,
        "app.py is a single ~1800-line file with clear section headers. Use Ctrl+F to "
        "jump to any of these markers:")

    TABLE(doc,
          ["Section header", "What lives there"],
          [
              ["CONSTANTS",                    "Valorization rates, base forecast, presets, defaults"],
              ["MATH HELPERS",                 "gamma_pdf, seasonal_curve, build_demand_curve, recommend_initial_stock"],
              ["SIMULATION ENGINE",            "run_simulation — the weekly loop"],
              ["KPI COMPUTATION",              "compute_kpis, cumulative_kpis, reconciliation_report"],
              ["DIAGRAM BUILDERS",             "week_box, store_card, supplier_card, make_sc_html"],
              ["PRESET APPLICATION",           "apply_preset (Permanent + Seasonal handling)"],
              ["SIDEBAR UI",                   "All sidebar widgets and the 18 quick-scenario buttons"],
              ["MAIN PAGE UI",                 "Header, KPI cards, diagram render, charts, P&L, debug, table, save"],
          ],
          widths_cm=[5.0, 10.0])

    H3(doc, "The most-edited constants")
    BULLETS(doc, [
        "VALOR_RAW_MAT, VALOR_SEMI, VALOR_FINISHED — change stage cost percentages.",
        "BASE_FORECAST — the planner's nominal demand signal (default 100).",
        "LT_PROFILES — Agile / Medium / Push lead times and order frequencies.",
        "STOCK_DIST_OPERATIONAL, STOCK_DIST_SEASONAL — % allocation per profile.",
        "SEASONAL_BASE_STOCK, SELL_THROUGH_SEASONAL — seasonal sizing rule.",
        "SEASONAL_PARAMS — gamma curve shape (peak position, k).",
    ])

    H3(doc, "Inside the weekly loop — multi-echelon ordering")
    P(doc,
        "run_simulation orders at four push points every review week, each with its "
        "own coverage horizon. Search these variables in the engine:")
    TABLE(doc,
          ["Variable", "Meaning"],
          [
              ["cov_sup / cov_semi / cov_fp / cov_ship", "Per-stage coverage windows (downstream LT + freq)."],
              ["existing_sup / _semi / _fp / _ship",     "Inventory counted from each push point downstream + that stage's backlog."],
              ["tgt_sup / _semi / _fp / _ship",          "Demand-to-cover target for each stage."],
              ["od_sup / od_semi / od_fp / od_ship",     "max(0, target − existing) — the order accrued to each stage's backlog."],
              ["pb / semi_backlog / fp_backlog / ship_backlog", "The four independent backlogs each step draws against."],
          ],
          widths_cm=[5.6, 9.4])
    P(doc,
        "To change a stage's horizon, edit its cov_* expression. To make a stage order "
        "more or less aggressively, edit its existing_* set (what it is allowed to "
        "count as already-covered). The per-store gap block (guarded by smart_distrib) "
        "raises a stage's order when one store would starve; remove it to revert to "
        "pooled-only ordering.")

    H3(doc, "Inside the weekly loop — seasonal factor discovery")
    P(doc,
        "When a planner_curve is passed in (seasonal_mode), the engine locks a single "
        "scaling factor at the first review week, then look-aheads over the curve:")
    BULLETS(doc, [
        "planner_curve_internal — the believed shape (avg ≈ 100/wk), index 0 unused.",
        "planner_factor — locked once: Σ actual_demand[1..w] / Σ curve[1..w], then "
        "snapped to a clean integer or 0.1 increment within rounding tolerance.",
        "_lookahead_sum(curve, w_from, w_to) — forward sum of the curve over a stage's "
        "coverage window, multiplied by planner_factor. This produces tgt_* in "
        "seasonal mode (the flat path uses ff × cov_x instead).",
    ])
    P(doc,
        "The factor never re-bases after the first review — that is intentional. To "
        "let the planner re-learn each review, move the planner_factor assignment out "
        "of the `if planner_factor is None` guard.")

    PAGE_BREAK(doc)

    H1(doc, "5. Common Modifications")

    H3(doc, "Change a number (no Python knowledge needed)")
    NUMBERED(doc, [
        "Open app.py, Ctrl+F to find the constant name.",
        "Replace the number on its line.",
        "Save. The running app auto-reloads.",
    ])
    P(doc,
        "Examples: change BASE_FORECAST = 100 to BASE_FORECAST = 150 to bump baseline "
        "demand. Change SELL_THROUGH_SEASONAL[\"Push\"] from 0.60 to 0.50 to make "
        "Push hedge harder.")

    H3(doc, "Change a default lead time")
    P(doc,
        "Find LT_PROFILES near the top of the file. Each profile is a dict with mat_lt, "
        "semi_lt, fp_lt, dist_lt, order_freq. Edit the numbers; save.")

    H3(doc, "Add a new preset button")
    P(doc,
        "Find the SIDEBAR UI section, sub-section ‘Quick scenarios'. Each preset is a "
        "single line:")
    CODE(doc, 'st.button("⚡", key="p_af", use_container_width=True,\n'
              '          on_click=apply_preset, args=("Agile", "Flat"))')
    P(doc,
        "Copy that line, change the key (must be unique) and the args tuple, and add "
        "it inside one of the column blocks. The apply_preset function already handles "
        "the four built-in demand_kind values (Flat / Growth / Drop / Seasonal).")

    H3(doc, "Change a color")
    P(doc,
        "Find the DIAGRAM BUILDERS section, near the top there is a Palette block "
        "with constants like C_BOX_FILL = '#4a6280'. Replace the hex value; save.")

    H3(doc, "Change a formula")
    P(doc,
        "Most of the engine logic lives in run_simulation. Read the docstring at the "
        "top first — it explains the weekly loop order. Make small changes one at a "
        "time and verify reconciliation by enabling the Debug toggle in the running app.")

    CALLOUT(doc,
        "WARNING: After any logic change, run with Debug ON. The reconciliation report "
        "must show all 5 checks PASS. If any fails, undo your change and try a smaller "
        "step.")

    PAGE_BREAK(doc)

    H1(doc, "6. Publishing Online (Streamlit Community Cloud)")

    H3(doc, "One-time setup")
    NUMBERED(doc, [
        "Open https://share.streamlit.io and click ‘Sign in with GitHub'.",
        "Authorize Streamlit Cloud to read your repositories.",
        "On the dashboard, click ‘New app'.",
        "Repository: rgxsc/sc_simu_wip.",
        "Branch: claude/rebuild-supply-chain-simulator-9LAVA  (or main, if you have "
        "merged).",
        "Main file path: app.py.",
        "App URL: choose any unused subdomain.",
        "Click ‘Deploy'. First build takes ~2-3 minutes.",
    ])
    P(doc,
        "Once built, you have a public URL like https://your-name.streamlit.app — "
        "share it with anyone. The app is free for one public app per account.")

    H3(doc, "Pushing your edits to the live site")
    P(doc, "If you edited locally and tested everything works:")
    NUMBERED(doc, [
        "On GitHub, navigate to app.py on your branch.",
        "Click the pencil icon (‘Edit this file').",
        "Paste your modified contents over the existing ones.",
        "Scroll to the bottom, write a short commit message, and click ‘Commit changes'.",
        "Streamlit Cloud detects the new commit and redeploys automatically (~30-60 seconds).",
        "Hard-refresh the running app: Ctrl+Shift+R (Windows) / Cmd+Shift+R (Mac).",
    ])

    H3(doc, "Updating directly in the browser (Route A)")
    P(doc,
        "If your change is just a number or text tweak, you can edit app.py directly "
        "on GitHub (the pencil icon → edit → commit). Streamlit Cloud will redeploy "
        "automatically. No local Python needed for this route.")

    H3(doc, "Rebooting the app")
    P(doc,
        "If a deploy seems stuck, go to share.streamlit.io, find your app on the "
        "dashboard, click the menu (three dots) → ‘Reboot'. Use ‘Clear cache' first "
        "if you suspect cached widget state is causing weird behavior.")

    PAGE_BREAK(doc)

    H1(doc, "7. Rolling Back and Troubleshooting")

    H3(doc, "Rolling back to a known-good commit")
    NUMBERED(doc, [
        "On GitHub, navigate to your branch, click ‘Commits'.",
        "Find the last commit you trust (e.g. the SHA you wrote down before changes).",
        "Click ‘<>' (Browse files) on that commit.",
        "Open app.py, click ‘Raw', save the contents.",
        "Edit your active branch's app.py and paste the saved-good contents over.",
        "Commit. Streamlit Cloud redeploys with the rolled-back code.",
    ])
    P(doc,
        "Alternative: if you created a backup branch (Section 1, Backup 3), you can "
        "ask GitHub support or use git on the command line to reset your active branch "
        "to the backup. The browser-only path above is simpler.")

    H3(doc, "Common errors")
    TABLE(doc,
          ["Symptom", "Likely cause", "Fix"],
          [
              ["App won't start locally — ‘streamlit not found'", "PATH not set", "Re-install Python with ‘Add to PATH' ticked"],
              ["Browser shows ‘ModuleNotFoundError'",          "Missing pip install", "Run pip install streamlit pandas numpy altair"],
              ["Streamlit Cloud build fails",                    "requirements.txt missing or wrong",   "Check the file is in repo root with the four packages"],
              ["Sidebar values do not change after preset",      "Browser cache",         "Hard-refresh (Ctrl+Shift+R)"],
              ["P&L residual ≠ 0 in Debug",                      "Logic change broke conservation", "Roll back to last good commit"],
              ["Diagram does not fit page width",                "Browser zoom or many-LT scenario",   "Set browser zoom to 100%; collapse the sidebar"],
          ],
          widths_cm=[5.0, 5.5, 5.0])

    H3(doc, "Where to look for logs")
    P(doc,
        "On Streamlit Cloud, click your app → ‘Manage app' (lower right of the running "
        "app) → ‘Logs' tab. Build errors and Python tracebacks appear here. Locally, "
        "Python errors print directly in the terminal where you ran ‘streamlit run app.py'.")

    H3(doc, "Where to ask for help")
    BULLETS(doc, [
        "Streamlit forum: discuss.streamlit.io — friendly, fast responses.",
        "Streamlit docs: docs.streamlit.io.",
        "Python basics: python.org/about/gettingstarted/.",
    ])


if __name__ == '__main__':
    build_user_guide('docs/USER_GUIDE.docx')
    build_dev_guide('docs/DEV_GUIDE.docx')
