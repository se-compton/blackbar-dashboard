"""Build the Filevine case-expense ledger for QuickBooks cost-account reconciliation.

Inputs (produced by the Filevine pull):
  <data_dir>/candidates.json      closed-case list (projectId, name, client, phase, phaseDate)
  <data_dir>/results/*.jsonl      per-case disposition + expense detail

Optional: a QuickBooks cost-account export (CSV/XLSX). When given, the QB columns
are filled per case, each Filevine transaction is matched to a QB line, and a
QB Detail tab lists every QB line with its case status. Without it, the QB
columns are left blank for manual entry.

Usage: python3 scripts/build_cost_ledger.py <data_dir> <output.xlsx> [as_of YYYY-MM-DD] [--qb export.csv] [--qb-flip]
"""
import glob
import json
import os
import sys
from datetime import date
from collections import Counter, defaultdict

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qb_match import assign_direct, enrich_from_ledger, load_qb, match_transactions, split_general_ledger  # noqa: E402

SCOPE_START = "2024-01-01"
SUSPECT_AMOUNT = 100000  # no single case-cost check should approach this
FV_URL = "https://hostilolaw.filevineapp.com/#/project/{}"

FONT = "Arial"
HDR_FILL = PatternFill("solid", start_color="1F3864")
INPUT_FILL = PatternFill("solid", start_color="FFFF00")
BLUE = Font(name=FONT, size=10, color="0000FF")
MONEY = '$#,##0.00;($#,##0.00);"-"'
THIN = Side(style="thin", color="BFBFBF")


def num(v):
    try:
        return float(v) if v not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def d10(v):
    return (v or "")[:10] or None


def disposition(case, cs):
    """Return (group, detail, date) for a case."""
    settle_fields = ("settlementamount", "uMSettlementAmount", "2ndLiabilitySettlementAmoun",
                     "2ndUMSettlementAmount", "otherSettlementAmount")
    settled_amt = max(num(cs.get("totalSettlementAmount")), sum(num(cs.get(f)) for f in settle_fields))
    rt = (cs.get("resolutionType") or "").strip()
    phase = case["phase"]
    settle_date = d10(cs.get("settlementdate")) or d10(cs.get("uMSettlementDate"))
    rej_date = d10(cs.get("rejectedOrFiredDate"))

    if settled_amt > 0:
        stage = {
            "Disbursement": "Settled - disbursement in progress",
            "Pending Closure - QB Checks": "Settled - pending closure (QB checks)",
            "Pending Closure": "Settled - pending closure",
            "Stale Checks - ACCT": "Settled - stale checks",
            "5 Year Send Out (Accounting)": "Settled - 5 year send out",
        }.get(phase, "Settled - closed")
        return "Settled", stage, settle_date or d10(cs.get("disbursement")) or case["phaseDate"], settled_amt
    if rt in ("Rejected", "Referral-Rejected"):
        return "Rejected", rt, rej_date or d10(cs.get("rejection")) or case["phaseDate"], 0.0
    if rt == "Fired":
        return "Terminated", "Fired", rej_date or d10(cs.get("fIRED")) or case["phaseDate"], 0.0
    if rt.startswith("Lost"):
        return "Terminated", rt, rej_date or case["phaseDate"], 0.0
    if phase == "Rejection" or cs.get("rejection"):
        return "Rejected", "Rejected (phase only)", d10(cs.get("rejection")) or case["phaseDate"], 0.0
    if phase == "FIRED" or cs.get("fIRED"):
        return "Terminated", "Fired (phase only)", d10(cs.get("fIRED")) or case["phaseDate"], 0.0
    if phase == "Referred Out" or cs.get("referredOut"):
        return "Referred Out", "Referred Out", d10(cs.get("referredOut")) or case["phaseDate"], 0.0
    return "Closed - no resolution recorded", "Archived without resolution type", case["phaseDate"], 0.0


def style_header(ws, row, ncols):
    for c in range(1, ncols + 1):
        cell = ws.cell(row=row, column=c)
        cell.font = Font(name=FONT, size=10, bold=True, color="FFFFFF")
        cell.fill = HDR_FILL
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    ws.row_dimensions[row].height = 32


def add_table(ws, name, first_row, last_row, ncols):
    ref = f"A{first_row}:{get_column_letter(ncols)}{max(last_row, first_row + 1)}"
    t = Table(displayName=name, ref=ref)
    t.tableStyleInfo = TableStyleInfo(name="TableStyleLight9", showRowStripes=True)
    ws.add_table(t)


def main(data_dir, out_path, as_of, qb_path=None, qb_flip=False):
    cands = {c["projectId"]: c for c in json.load(open(f"{data_dir}/candidates.json"))}
    pulled = {}
    for f in sorted(glob.glob(f"{data_dir}/results/*.jsonl")):
        for line in open(f):
            if line.strip():
                r = json.loads(line)
                pulled[int(r["projectId"])] = r

    cases, txns, out_of_scope, missing, screened = [], [], Counter(), [], []
    suspect = []
    for pid, case in cands.items():
        r = pulled.get(pid)
        if r is None:
            missing.append(pid)
            continue
        if r.get("screen") == "no_cost_activity":
            screened.append(case)
            continue
        cs = r.get("cs") or {}
        group, detail, ddate, settled_amt = disposition(case, cs)
        if (ddate or "") < SCOPE_START:
            out_of_scope[group] += 1
            continue

        rows = []
        for e in r.get("exp") or []:
            void = (e.get("status") or "") == "Void"
            # An amount equal to the project ID (or absurdly large) is a typo in Filevine, not a real cost.
            if num(e.get("amount")) and (num(e.get("amount")) == pid or num(e.get("amount")) > SUSPECT_AMOUNT):
                suspect.append((pid, case["projectName"], e.get("checkNumber"), e.get("payee"), num(e.get("amount"))))
                void = True
            rows.append(dict(src="Expense Request", kind="Advance", date=d10(e.get("dateOfCheck")) or d10(e.get("date")) or d10(e.get("created")),
                             payee=e.get("payee"), memo=e.get("memo") or e.get("description"), ref=e.get("checkNumber"),
                             method=e.get("expenseType"), status=e.get("status"), amount=num(e.get("amount")),
                             toqb="Yes" if e.get("toQB") else ("No" if e.get("toQB") is False else None),
                             qbupd=e.get("qbUpdate"), counted=not void))
        for p in r.get("post") or []:
            void = (p.get("status") or "") == "Voided"
            amt = num(p.get("amountdue")) or num(p.get("transactionamount")) or num(p.get("amountpaid"))
            # A few cases log real case expenses in the postage section; keep those in the QB match.
            case_exp = (p.get("expenseType") or "") == "Case Expense"
            rows.append(dict(src="Case Expense (Postage section)" if case_exp else "Postage", kind="Advance",
                             date=d10(p.get("date")) or d10(p.get("checkdate")) or d10(p.get("created")),
                             payee=None if case_exp else "Postage", memo=p.get("memo"), ref=p.get("checknumber"),
                             method="Case Expense" if case_exp else "Postage",
                             status=p.get("status"), amount=amt, toqb=None, qbupd=None, counted=not void))
        for d in r.get("disb") or []:
            void = (d.get("status") or "") == "Voided"
            rows.append(dict(src="Disbursal", kind="Due-to-Firm disbursal", date=d10(d.get("checkdate")) or d10(d.get("created")),
                             payee="Michael G. Hostilo, LLC", memo=d.get("memo"), ref=d.get("checknumber"), method=d.get("type"),
                             status=d.get("status"), amount=num(d.get("amountpaid")) or num(d.get("amountdue")),
                             toqb=None, qbupd=None, counted=not void))

        adv = sum(x["amount"] for x in rows if x["kind"] == "Advance" and x["counted"])
        # Postage disbursals never post to the QB cost account, so only Due to Firm (Expenses) counts here.
        rec = sum(x["amount"] for x in rows if x["kind"] != "Advance" and x["counted"] and "Postage" not in str(x["method"]))
        fc = r.get("fc") or {}
        cases.append(dict(pid=pid, name=case["projectName"], client=case.get("clientName") or "", group=group, detail=detail,
                          ddate=ddate, phase=case["phase"], phase_date=case["phaseDate"], settled=settled_amt,
                          reason=cs.get("reasonForRejection") or cs.get("reasonForRejectionOrFire"),
                          who=cs.get("whoRequestedRejectionOrFir"), adv=adv, rec=rec,
                          fvqb=num(fc.get("quickBooksCaseCosts")) if fc.get("quickBooksCaseCosts") is not None else None,
                          fvqb_upd=fc.get("lastUpdateFromQuickBooks"), ntx=len(rows), errors="; ".join(r.get("errors") or [])))
        for x in rows:
            txns.append(dict(x, pid=pid, name=case["projectName"], group=group))

    cases.sort(key=lambda c: (c["group"], c["ddate"] or "", c["pid"]))
    txns.sort(key=lambda t: (t["group"], t["pid"], t["date"] or ""))

    qb_lines, qb_by_case, qb_status, trust_by_case = None, defaultdict(lambda: [0.0, 0.0]), {}, {}
    if qb_path:
        qb_lines, gl_other = split_general_ledger(load_qb(qb_path, flip=qb_flip))
        qb_lines = assign_direct(qb_lines, cands)
        trust_by_case = {}
        if gl_other:
            qb_lines, trust_by_case = enrich_from_ledger(qb_lines, gl_other)
        match_transactions(txns, qb_lines)
        for ln in qb_lines:
            if ln["pid"]:
                qb_by_case[ln["pid"]][0 if ln["amount"] > 0 else 1] += abs(ln["amount"])
        per = defaultdict(lambda: [0, 0])
        for t in txns:
            if t.get("qb_match") in ("Y", "N"):
                per[t["pid"]][0] += 1
                per[t["pid"]][1] += t["qb_match"] == "Y"
        for pid, (n, y) in per.items():
            qb_status[pid] = ("All FV items found in QB" if y == n else
                              "No FV items found in QB" if y == 0 else f"Partial: {y} of {n} FV items found in QB")

    wb = Workbook()
    # ---- Summary tab -------------------------------------------------------
    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = "Filevine Case Expense Ledger - Cost Account Reconciliation (First Pass: Filevine Only)"
    ws["A1"].font = Font(name=FONT, size=14, bold=True)
    ws["A2"] = f"Closed cases with a disposition date from {SCOPE_START} through {as_of}. Source: Filevine org 5676, Personal Injury project type."
    ws["A2"].font = Font(name=FONT, size=10, italic=True)
    hdr = ["Disposition", "Cases", "Cases w/ FV Cost Activity", "FV Expenses Logged (Requests + Postage)",
           "FV Due-to-Firm Expense Disbursals", "QB Costs Matched to Case", "QB Credits Matched to Case",
           "Net of Matched QB Lines", "Variance (FV Disbursed - QB Credits Matched)", "Cases w/ FV Items Not Found in QB"]
    ws.append([])
    ws.append(hdr)
    style_header(ws, 4, len(hdr))
    n_last = max(len(cases) + 1, 2)
    groups = ["Settled", "Rejected", "Terminated", "Referred Out", "Closed - no resolution recorded"]
    for i, g in enumerate(groups):
        r = 5 + i
        ws.cell(r, 1, g)
        rng = lambda col: f"Cases!${col}$2:${col}${n_last}"
        ws.cell(r, 2, f'=COUNTIFS({rng("D")},$A{r})')
        ws.cell(r, 3, f'=COUNTIFS({rng("D")},$A{r},{rng("P")},">0")')
        ws.cell(r, 4, f'=SUMIFS({rng("L")},{rng("D")},$A{r})')
        ws.cell(r, 5, f'=SUMIFS({rng("M")},{rng("D")},$A{r})')
        ws.cell(r, 6, f'=SUMIFS({rng("Q")},{rng("D")},$A{r})')
        ws.cell(r, 7, f'=SUMIFS({rng("R")},{rng("D")},$A{r})')
        ws.cell(r, 8, f'=F{r}-G{r}')
        ws.cell(r, 9, f'=IF(AND(F{r}=0,G{r}=0),"",E{r}-G{r})')
        ws.cell(r, 10, f'=COUNTIFS({rng("D")},$A{r},{rng("U")},"<>All FV items found in QB",{rng("U")},"<>")')
    tr = 5 + len(groups)
    ws.cell(tr, 1, "Total")
    for c in range(2, 11):
        L = get_column_letter(c)
        ws.cell(tr, c, f"=SUM({L}5:{L}{tr - 1})")
    for row in ws.iter_rows(min_row=5, max_row=tr, max_col=len(hdr)):
        for cell in row:
            cell.font = Font(name=FONT, size=10, bold=(cell.row == tr))
            cell.border = Border(top=THIN, bottom=THIN)
            if 4 <= cell.column <= 9:
                cell.number_format = MONEY
    notes = [
        "How to read this workbook",
        "Cases tab: one row per closed case. Columns Q-R hold the QB cost-account lines tied to the case; Match Status says whether each Filevine expense and Due-to-Firm disbursal was found in QB.",
        "  - The QB Advanced Client Costs account does not record the case on each line (Name is the vendor), so QB lines are tied to cases through the Filevine transactions: check number, or amount and date.",
        "  - Variance compares the Filevine Due-to-Firm expense disbursal with the QB deposits (credits) matched to the case. A positive variance means Filevine shows a reimbursement that was not found in QB.",
        "  - QB lines that could not be tied to any Filevine transaction are listed on the QB Detail tab as 'Not traceable to a Filevine case' and totaled on the QB Not Traceable tab.",
        "Ledger tab: one row per Filevine transaction touching the cost account, for line-by-line tie-out to QuickBooks.",
        "All Closed Cases tab: every closed case in scope, including archived cases with no Filevine cost activity, so any QuickBooks entry can be traced to a case.",
        "  - Expense Request = case cost paid from the cost account and logged in Filevine (checks and BOA 7818 card). Voided items are listed but excluded from totals.",
        "  - Postage = postage logged in the Postage Only section.",
        "  - Disbursal = 'Due to Firm (Expenses/Postage)' check. Only the Expenses disbursals are totaled; postage disbursals are listed on the Ledger but never post to the QB cost account. Settled cases: case costs reimbursed to the firm from settlement. Rejected/fired cases: the 'FRD/REJ Case Exp' close-out of the cost balance.",
        "Many case costs are entered directly in QuickBooks and never logged in Filevine, so the Due-to-Firm disbursal (not the logged expenses) is the figure expected to tie to the QB cost account for each case.",
        "Disposition logic: Settled if any settlement amount is recorded in Case Summary; otherwise Resolution Type (Rejected, Referral-Rejected, Fired, Lost); otherwise the case phase.",
        "Disposition date: settlement date, rejected/fired date, or the date the case entered its current phase (archive date) when neither is recorded.",
        "'FV QuickBooks Case Costs (synced)' is the figure Filevine last pulled from QuickBooks (Final Costs & Fees section); settled cases only.",
    ]
    for i, n in enumerate(notes):
        c = ws.cell(tr + 2 + i, 1, n)
        c.font = Font(name=FONT, size=10, bold=(i == 0))
    ws.column_dimensions["A"].width = 34
    for c in range(2, 11):
        ws.column_dimensions[get_column_letter(c)].width = 17

    # ---- Cases tab ---------------------------------------------------------
    wc = wb.create_sheet("Cases")
    chdr = ["Filevine Project ID", "Case Name", "Client", "Disposition", "Disposition Detail", "Disposition Date",
            "Current FV Phase", "Phase Date", "Settlement Amount", "Rejection / Fire Reason", "Requested By",
            "FV Expenses Logged", "FV Due-to-Firm Expense Disbursals", "Logged minus Disbursed", "FV QuickBooks Case Costs (synced)",
            "FV Transactions", "QB Costs Matched to Case", "QB Credits Matched to Case", "Net of Matched QB Lines",
            "Variance (FV Disbursed - QB Credits Matched)", "QB Match Status", "QB Notes", "Notes", "Filevine Link",
            "QB Trust 'Case Exp' Checks to Firm"]
    wc.append(chdr)
    style_header(wc, 1, len(chdr))
    for i, c in enumerate(cases):
        r = i + 2
        qd, qc = (qb_by_case[c["pid"]] if c["pid"] in qb_by_case else (None, None))
        wc.append([c["pid"], c["name"], c["client"], c["group"], c["detail"], c["ddate"], c["phase"], c["phase_date"],
                   c["settled"] or None, c["reason"], c["who"], c["adv"], c["rec"], f"=L{r}-M{r}", c["fvqb"], c["ntx"],
                   qd, qc, f'=IF(AND(Q{r}="",R{r}=""),"",N(Q{r})-N(R{r}))', f'=IF(AND(Q{r}="",R{r}=""),"",M{r}-N(R{r}))',
                   qb_status.get(c["pid"], "") if qb_lines is not None else
                   f'=IF(AND(Q{r}="",R{r}=""),IF(M{r}>0,"Not in QB",""),IF(ABS(S{r})>=0.01,"Open balance in QB",IF(ABS(T{r})<0.01,"Matched","Variance")))',
                   None, c["errors"] or None, FV_URL.format(c["pid"]),
                   round(trust_by_case[c["pid"]], 2) if qb_lines is not None and c["pid"] in trust_by_case else None])
    last = len(cases) + 1
    for row in wc.iter_rows(min_row=2, max_row=last, max_col=len(chdr)):
        for cell in row:
            cell.font = Font(name=FONT, size=10)
            if cell.column in (9, 12, 13, 14, 15, 17, 18, 19, 20, 25):
                cell.number_format = MONEY
            if cell.column in (17, 18, 22):
                cell.fill = INPUT_FILL
                cell.font = BLUE
    wc["Q1"].comment = Comment("QB cost-account debits tied to this case through its Filevine expenses (check # or amount/date) or a project ID in the QB description.", "Ledger")
    wc["R1"].comment = Comment("QB cost-account credits (Case Exp / FRD-REJ deposits) tied to this case through its Filevine Due-to-Firm disbursals.", "Ledger")
    wc["U1"].comment = Comment("Share of this case's Filevine expenses and Due-to-Firm disbursals that were found in QB. Postage is not posted line-by-line in QB and is not counted.", "Ledger")
    widths = [12, 38, 24, 16, 30, 12, 24, 11, 14, 26, 11, 14, 14, 14, 14, 10, 14, 14, 14, 14, 20, 24, 24, 42, 14]
    wc["Y1"].comment = Comment("Total of the trust-account checks to the firm memo'd '<client> (<ID>):Case Exp' for this case, read from the full QB general ledger. Should equal the Filevine Due-to-Firm expense disbursal.", "Ledger")
    for i, w in enumerate(widths):
        wc.column_dimensions[get_column_letter(i + 1)].width = w
    wc.freeze_panes = "C2"
    add_table(wc, "CasesTbl", 1, last, len(chdr))

    # ---- Ledger tab --------------------------------------------------------
    wl = wb.create_sheet("Ledger")
    lhdr = ["Filevine Project ID", "Case Name", "Disposition", "Source", "Entry Type", "Transaction Date", "Payee",
            "Memo / Description", "Check # / Ref", "Method / Type", "FV Status", "Amount", "Counts in Totals",
            "Sent to QB", "Last QB Update (FV)", "QB Match (Y/N)", "QB Ref / Notes"]
    wl.append(lhdr)
    style_header(wl, 1, len(lhdr))
    for t in txns:
        wl.append([t["pid"], t["name"], t["group"], t["src"], t["kind"], t["date"], t["payee"], t["memo"], t["ref"],
                   t["method"], t["status"], t["amount"], "Yes" if t["counted"] else "No (void)", t["toqb"], t["qbupd"],
                   t.get("qb_match"), t.get("qb_ref")])
    lastl = len(txns) + 1
    for row in wl.iter_rows(min_row=2, max_row=lastl, max_col=len(lhdr)):
        for cell in row:
            cell.font = Font(name=FONT, size=10)
            if cell.column == 12:
                cell.number_format = MONEY
            if cell.column in (16, 17):
                cell.fill = INPUT_FILL
                cell.font = BLUE
    widths = [12, 38, 14, 16, 18, 12, 28, 44, 12, 26, 18, 12, 12, 9, 22, 12, 24]
    for i, w in enumerate(widths):
        wl.column_dimensions[get_column_letter(i + 1)].width = w
    wl.freeze_panes = "C2"
    add_table(wl, "LedgerTbl", 1, lastl, len(lhdr))

    # ---- All Closed Cases tab (lookup list for QB entries on any case) ----
    wa = wb.create_sheet("All Closed Cases")
    ahdr = ["Filevine Project ID", "Case Name", "Client", "Current FV Phase", "Phase Date (archive / close)",
            "FV Cost Activity", "Disposition (if pulled)", "Filevine Link"]
    wa.append(ahdr)
    style_header(wa, 1, len(ahdr))
    by_pid = {c["pid"]: c for c in cases}
    screened_ids = {c["projectId"] for c in screened}
    all_rows = []
    for pid, case in cands.items():
        if pid in by_pid:
            act, disp = "Yes - see Cases tab", by_pid[pid]["group"]
        elif pid in screened_ids:
            act, disp = "None in Filevine", None
        elif pid in pulled:
            act, disp = "Out of date scope", None
        else:
            act, disp = "Not pulled", None
        all_rows.append([pid, case["projectName"], case.get("clientName") or "", case["phase"], case["phaseDate"],
                         act, disp, FV_URL.format(pid)])
    for row in sorted(all_rows, key=lambda x: (x[1] or "").lower()):
        wa.append(row)
    for row in wa.iter_rows(min_row=2, max_col=len(ahdr)):
        for cell in row:
            cell.font = Font(name=FONT, size=10)
    for i, w in enumerate([12, 40, 26, 24, 14, 20, 16, 42]):
        wa.column_dimensions[get_column_letter(i + 1)].width = w
    wa.freeze_panes = "C2"
    add_table(wa, "AllCasesTbl", 1, len(all_rows) + 1, len(ahdr))

    # ---- QB Detail tab (only when a QB export is supplied) -----------------
    if qb_lines is not None:
        def case_status(pid):
            if not pid:
                return "Not traceable to a Filevine case"
            if pid in by_pid:
                return f"In ledger ({by_pid[pid]['group']})"
            if pid in screened_ids:
                return "Closed - no FV cost activity"
            if pid in pulled:
                return "Closed - disposition before " + SCOPE_START
            return "Open or pre-2024 case (not in the closed-case list)"
        scope_d = date.fromisoformat(SCOPE_START)
        shown = [ln for ln in qb_lines if ln["date"] >= scope_d or ln["pid"]]
        wq = wb.create_sheet("QB Detail")
        qhdr = ["QB Date", "QB Type", "QB Num", "QB Name (vendor)", "QB Class", "QB Description", "QB Split", "Amount",
                "Filevine Project ID", "Case Name", "How Tied", "Case Status", "Matched to FV Transaction"]
        wq.append(qhdr)
        style_header(wq, 1, len(qhdr))
        for ln in sorted(shown, key=lambda x: x["date"]):
            pid = ln["pid"]
            wq.append([ln["date"].isoformat(), ln["type"], ln["num"], ln["name"], ln["cls"], ln["memo"], ln["account"],
                       ln["amount"], pid, cands[pid]["projectName"] if pid in cands else None, ln["how"], case_status(pid),
                       "Y" if ln["fv_matched"] else "N"])
        for row in wq.iter_rows(min_row=2, max_col=len(qhdr)):
            for cell in row:
                cell.font = Font(name=FONT, size=10)
                if cell.column == 8:
                    cell.number_format = MONEY
        for i, w in enumerate([11, 12, 9, 30, 18, 34, 28, 11, 12, 34, 30, 28, 11]):
            wq.column_dimensions[get_column_letter(i + 1)].width = w
        wq.freeze_panes = "A2"
        add_table(wq, "QBDetailTbl", 1, len(shown) + 1, len(qhdr))

        # ---- QB Not Traceable tab: untied QB lines in scope, by year / type / vendor ----
        wn = wb.create_sheet("QB Not Traceable")
        agg = defaultdict(lambda: [0, 0.0])
        for ln in qb_lines:
            if ln["date"] >= scope_d and not ln["pid"]:
                who = ln["name"] or (ln["memo"][:40] if ln["type"] == "Deposit" else "(no name)")
                k = (ln["date"].year, ln["type"], who)
                agg[k][0] += 1
                agg[k][1] += ln["amount"]
        nhdr = ["Year", "QB Type", "Vendor / Deposit Memo", "Lines", "Amount"]
        wn.append(nhdr)
        style_header(wn, 1, len(nhdr))
        for (y, t, who), (n, amt) in sorted(agg.items(), key=lambda kv: (kv[0][0], -abs(kv[1][1]))):
            wn.append([str(y), t, who, n, round(amt, 2)])
        for row in wn.iter_rows(min_row=2, max_col=len(nhdr)):
            for cell in row:
                cell.font = Font(name=FONT, size=10)
                if cell.column == 5:
                    cell.number_format = MONEY
        for i, w in enumerate([8, 18, 44, 8, 14]):
            wn.column_dimensions[get_column_letter(i + 1)].width = w
        wn.freeze_panes = "A2"
        add_table(wn, "QBNotTraceTbl", 1, len(agg) + 1, len(nhdr))

        # ---- QB control totals on the Summary tab ----
        r0 = ws.max_row + 2
        ws.cell(r0, 1, "QuickBooks control totals (Advanced Client Costs)").font = Font(name=FONT, size=10, bold=True)
        insc = [ln for ln in qb_lines if ln["date"] >= scope_d]
        ctl = [
            ("QB account balance, all dates (ties to the QB report total)", sum(ln["amount"] for ln in qb_lines)),
            (f"QB costs posted since {SCOPE_START}", sum(ln["amount"] for ln in insc if ln["amount"] > 0)),
            (f"QB credits (reimbursed / written off) since {SCOPE_START}", -sum(ln["amount"] for ln in insc if ln["amount"] < 0)),
            ("  of which costs tied to a Filevine case", sum(ln["amount"] for ln in insc if ln["amount"] > 0 and ln["pid"])),
            ("  of which credits tied to a Filevine case", -sum(ln["amount"] for ln in insc if ln["amount"] < 0 and ln["pid"])),
            ("  costs not traceable to a Filevine case", sum(ln["amount"] for ln in insc if ln["amount"] > 0 and not ln["pid"])),
            ("  credits not traceable to a Filevine case", -sum(ln["amount"] for ln in insc if ln["amount"] < 0 and not ln["pid"])),
        ]
        for i, (label, val) in enumerate(ctl):
            ws.cell(r0 + 1 + i, 1, label).font = Font(name=FONT, size=10)
            c = ws.cell(r0 + 1 + i, 4, round(val, 2))
            c.font = Font(name=FONT, size=10)
            c.number_format = MONEY
        ws.cell(r0 + 1 + len(ctl), 1, f"Source: {os.path.basename(qb_path)} (QuickBooks Transaction Detail by Account export).").font = Font(name=FONT, size=9, italic=True)

    # ---- Data Gaps tab -----------------------------------------------------
    wg = wb.create_sheet("Data Gaps")
    wg.append(["Item", "Count / Detail"])
    style_header(wg, 1, 2)
    wg.append(["Cases pulled from Filevine", len(pulled)])
    wg.append(["Cases in scope (disposition date on or after " + SCOPE_START + ")", len(cases)])
    for g, n in sorted(out_of_scope.items()):
        wg.append([f"Excluded: {g} with disposition date before {SCOPE_START}", n])
    wg.append(["Archived cases with no Filevine cost activity (listed on All Closed Cases tab)", len(screened)])
    wg.append(["Candidate cases not returned by the pull", len(missing)])
    wg.append(["Cases with Filevine read errors", sum(1 for c in cases if c["errors"])])
    wg.append(["Settled cases with no FV expense entries", sum(1 for c in cases if c["group"] == "Settled" and c["ntx"] == 0)])
    wg.append(["Cases with FV expenses logged but no Due-to-Firm expense disbursal",
               sum(1 for c in cases if c["adv"] > 0 and c["rec"] == 0)])
    if qb_lines is not None:
        wg.append(["QB lines loaded (all dates)", len(qb_lines)])
        wg.append(["QB lines matched to a Filevine transaction", sum(1 for ln in qb_lines if ln["fv_matched"])])
        wg.append(["QB lines tied by project ID in the description", sum(1 for ln in qb_lines if ln["pid"] and not ln["fv_matched"])])
        wg.append(["Filevine expenses / disbursals found in QB", sum(1 for t in txns if t.get("qb_match") == "Y")])
        wg.append(["Filevine expenses / disbursals NOT found in QB", sum(1 for t in txns if t.get("qb_match") == "N")])
        wg.append(["QB lines since " + SCOPE_START + " not traceable to a Filevine case (see QB Not Traceable tab)",
                   sum(1 for ln in qb_lines if ln["date"] >= date.fromisoformat(SCOPE_START) and not ln["pid"])])
    if suspect:
        wg.append([])
        wg.append(["Expense requests with an impossible amount (excluded from totals; correct in Filevine)", len(suspect)])
        for pid, name, chk, payee, amt in suspect:
            wg.append([f"  {pid} {name}: check {chk or 'n/a'} to {payee or 'n/a'}", f"{amt:,.2f} entered in Filevine"])
    if missing:
        wg.append([])
        wg.append(["Missing Filevine Project IDs", ", ".join(str(m) for m in missing[:500])])
    for row in wg.iter_rows(min_row=2):
        for cell in row:
            cell.font = Font(name=FONT, size=10)
    wg.column_dimensions["A"].width = 70
    wg.column_dimensions["B"].width = 40

    wb.calculation.fullCalcOnLoad = True
    wb.save(out_path)
    print(json.dumps(dict(cases=len(cases), txns=len(txns), pulled=len(pulled), missing=len(missing),
                          out_of_scope=dict(out_of_scope), by_group=dict(Counter(c["group"] for c in cases)))))


if __name__ == "__main__":
    args = sys.argv[1:]
    qb_flip = "--qb-flip" in args
    args = [a for a in args if a != "--qb-flip"]
    qb_path = None
    if "--qb" in args:
        i = args.index("--qb")
        qb_path = args[i + 1]
        del args[i:i + 2]
    main(args[0], args[1], args[2] if len(args) > 2 else "current", qb_path, qb_flip)
