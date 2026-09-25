"""Build the Filevine case-expense ledger for QuickBooks cost-account reconciliation.

Inputs (produced by the Filevine pull):
  <data_dir>/candidates.json      closed-case list (projectId, name, client, phase, phaseDate)
  <data_dir>/results/*.jsonl      per-case disposition + expense detail

Output: an .xlsx workbook with a case summary, a transaction-level ledger, and
blank QuickBooks columns to fill in during reconciliation.

Usage: python3 scripts/build_cost_ledger.py <data_dir> <output.xlsx> [as_of YYYY-MM-DD]
"""
import glob
import json
import sys
from collections import Counter

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

SCOPE_START = "2024-01-01"
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


def main(data_dir, out_path, as_of):
    cands = {c["projectId"]: c for c in json.load(open(f"{data_dir}/candidates.json"))}
    pulled = {}
    for f in sorted(glob.glob(f"{data_dir}/results/*.jsonl")):
        for line in open(f):
            if line.strip():
                r = json.loads(line)
                pulled[int(r["projectId"])] = r

    cases, txns, out_of_scope, missing = [], [], Counter(), []
    for pid, case in cands.items():
        r = pulled.get(pid)
        if r is None:
            missing.append(pid)
            continue
        cs = r.get("cs") or {}
        group, detail, ddate, settled_amt = disposition(case, cs)
        if (ddate or "") < SCOPE_START:
            out_of_scope[group] += 1
            continue

        rows = []
        for e in r.get("exp") or []:
            void = (e.get("status") or "") == "Void"
            rows.append(dict(src="Expense Request", kind="Advance", date=d10(e.get("dateOfCheck")) or d10(e.get("date")) or d10(e.get("created")),
                             payee=e.get("payee"), memo=e.get("memo") or e.get("description"), ref=e.get("checkNumber"),
                             method=e.get("expenseType"), status=e.get("status"), amount=num(e.get("amount")),
                             toqb="Yes" if e.get("toQB") else ("No" if e.get("toQB") is False else None),
                             qbupd=e.get("qbUpdate"), counted=not void))
        for p in r.get("post") or []:
            void = (p.get("status") or "") == "Voided"
            amt = num(p.get("amountdue")) or num(p.get("transactionamount")) or num(p.get("amountpaid"))
            rows.append(dict(src="Postage", kind="Advance", date=d10(p.get("date")) or d10(p.get("checkdate")) or d10(p.get("created")),
                             payee="Postage", memo=p.get("memo"), ref=p.get("checknumber"), method="Postage",
                             status=p.get("status"), amount=amt, toqb=None, qbupd=None, counted=not void))
        for d in r.get("disb") or []:
            void = (d.get("status") or "") == "Voided"
            rows.append(dict(src="Disbursal", kind="Due-to-Firm disbursal", date=d10(d.get("checkdate")) or d10(d.get("created")),
                             payee="Michael G. Hostilo, LLC", memo=d.get("memo"), ref=d.get("checknumber"), method=d.get("type"),
                             status=d.get("status"), amount=num(d.get("amountpaid")) or num(d.get("amountdue")),
                             toqb=None, qbupd=None, counted=not void))

        adv = sum(x["amount"] for x in rows if x["kind"] == "Advance" and x["counted"])
        rec = sum(x["amount"] for x in rows if x["kind"] != "Advance" and x["counted"])
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

    wb = Workbook()
    # ---- Summary tab -------------------------------------------------------
    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = "Filevine Case Expense Ledger - Cost Account Reconciliation (First Pass: Filevine Only)"
    ws["A1"].font = Font(name=FONT, size=14, bold=True)
    ws["A2"] = f"Closed cases with a disposition date from {SCOPE_START} through {as_of}. Source: Filevine org 5676, Personal Injury project type."
    ws["A2"].font = Font(name=FONT, size=10, italic=True)
    hdr = ["Disposition", "Cases", "Cases w/ FV Cost Activity", "FV Expenses Logged (Requests + Postage)",
           "FV Due-to-Firm Expense Disbursals", "Logged Not Yet Disbursed", "QB Cost Account Total", "Variance (Disbursed - QB)"]
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
        ws.cell(r, 6, f'=D{r}-E{r}')
        ws.cell(r, 7, f'=SUMIFS({rng("Q")},{rng("D")},$A{r})')
        ws.cell(r, 8, f'=E{r}-G{r}')
    tr = 5 + len(groups)
    ws.cell(tr, 1, "Total")
    for c in range(2, 9):
        L = get_column_letter(c)
        ws.cell(tr, c, f"=SUM({L}5:{L}{tr - 1})")
    for row in ws.iter_rows(min_row=5, max_row=tr, max_col=len(hdr)):
        for cell in row:
            cell.font = Font(name=FONT, size=10, bold=(cell.row == tr))
            cell.border = Border(top=THIN, bottom=THIN)
            if cell.column >= 4:
                cell.number_format = MONEY
    notes = [
        "How to read this workbook",
        "Cases tab: one row per closed case. Yellow columns Q-S are for the QuickBooks figures; Variance fills in automatically.",
        "Ledger tab: one row per Filevine transaction touching the cost account, for line-by-line tie-out to QuickBooks.",
        "  - Expense Request = case cost paid from the cost account and logged in Filevine (checks and BOA 7818 card). Voided items are listed but excluded from totals.",
        "  - Postage = postage logged in the Postage Only section.",
        "  - Disbursal = 'Due to Firm (Expenses/Postage)' check. Settled cases: case costs reimbursed to the firm from settlement. Rejected/fired cases: the 'FRD/REJ Case Exp' close-out of the cost balance.",
        "Many case costs are entered directly in QuickBooks and never logged in Filevine, so the Due-to-Firm disbursal (not the logged expenses) is the figure expected to tie to the QB cost account for each case.",
        "Disposition logic: Settled if any settlement amount is recorded in Case Summary; otherwise Resolution Type (Rejected, Referral-Rejected, Fired, Lost); otherwise the case phase.",
        "Disposition date: settlement date, rejected/fired date, or the date the case entered its current phase (archive date) when neither is recorded.",
        "'FV QuickBooks Case Costs (synced)' is the figure Filevine last pulled from QuickBooks (Final Costs & Fees section); settled cases only.",
    ]
    for i, n in enumerate(notes):
        c = ws.cell(tr + 2 + i, 1, n)
        c.font = Font(name=FONT, size=10, bold=(i == 0))
    ws.column_dimensions["A"].width = 34
    for c in range(2, 9):
        ws.column_dimensions[get_column_letter(c)].width = 19

    # ---- Cases tab ---------------------------------------------------------
    wc = wb.create_sheet("Cases")
    chdr = ["Filevine Project ID", "Case Name", "Client", "Disposition", "Disposition Detail", "Disposition Date",
            "Current FV Phase", "Phase Date", "Settlement Amount", "Rejection / Fire Reason", "Requested By",
            "FV Expenses Logged", "FV Due-to-Firm Expense Disbursals", "Logged Not Yet Disbursed", "FV QuickBooks Case Costs (synced)",
            "FV Transactions", "QB Cost Account Total", "QB Match Status", "QB Notes", "Variance (Disbursed - QB)", "Notes", "Filevine Link"]
    wc.append(chdr)
    style_header(wc, 1, len(chdr))
    for i, c in enumerate(cases):
        r = i + 2
        wc.append([c["pid"], c["name"], c["client"], c["group"], c["detail"], c["ddate"], c["phase"], c["phase_date"],
                   c["settled"] or None, c["reason"], c["who"], c["adv"], c["rec"], f"=L{r}-M{r}", c["fvqb"], c["ntx"],
                   None, None, None, f'=IF(Q{r}="","",M{r}-Q{r})', c["errors"] or None, FV_URL.format(c["pid"])])
    last = len(cases) + 1
    for row in wc.iter_rows(min_row=2, max_row=last, max_col=len(chdr)):
        for cell in row:
            cell.font = Font(name=FONT, size=10)
            if cell.column in (9, 12, 13, 14, 15, 17, 20):
                cell.number_format = MONEY
            if cell.column in (17, 18, 19):
                cell.fill = INPUT_FILL
                cell.font = BLUE
    wc["Q1"].comment = Comment("Enter the QuickBooks cost-account total for this case (from the QB data you will provide).", "Ledger")
    wc["R1"].comment = Comment("Suggested values: Matched / Variance / Not in QB / QB only", "Ledger")
    widths = [12, 38, 24, 16, 30, 12, 24, 11, 14, 26, 11, 14, 14, 14, 14, 10, 14, 14, 24, 14, 24, 42]
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
                   t["method"], t["status"], t["amount"], "Yes" if t["counted"] else "No (void)", t["toqb"], t["qbupd"], None, None])
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

    # ---- Data Gaps tab -----------------------------------------------------
    wg = wb.create_sheet("Data Gaps")
    wg.append(["Item", "Count / Detail"])
    style_header(wg, 1, 2)
    wg.append(["Cases pulled from Filevine", len(pulled)])
    wg.append(["Cases in scope (disposition date on or after " + SCOPE_START + ")", len(cases)])
    for g, n in sorted(out_of_scope.items()):
        wg.append([f"Excluded: {g} with disposition date before {SCOPE_START}", n])
    wg.append(["Candidate cases not returned by the pull", len(missing)])
    wg.append(["Cases with Filevine read errors", sum(1 for c in cases if c["errors"])])
    wg.append(["Settled cases with no FV expense entries", sum(1 for c in cases if c["group"] == "Settled" and c["ntx"] == 0)])
    wg.append(["Cases with FV expenses logged but no Due-to-Firm expense disbursal",
               sum(1 for c in cases if c["adv"] > 0 and c["rec"] == 0)])
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
    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "current")
