"""Trust check clearing test: QuickBooks trust checks vs the Bank of America feed (Tiller).

A check written in QuickBooks only proves the check was cut. The bank feed shows which checks actually
cleared the trust account (1206), by check number and amount. A check to the firm that never cleared is
firm money still sitting in trust; a client or lienholder check that never cleared is a stale check
(eventually unclaimed property).

Also ties the firm's checks out of trust to the batch deposits into operating (1107) and cost (6455), month by
month. Trust and the firm accounts are both at Bank of America, so a firm check clears trust the day it is
deposited. Where the trust feed has a sync gap, the firm checks that can't be seen clearing are tied to the
operating and cost deposits made during the gap instead (those feeds have no gaps).

Writes <data_dir>/bank_status.json (per case: firm checks not cleared) for trust_firm_audit.py.

Usage: python3 scripts/bank_clearing.py <data_dir> <qb_full.xlsx> <tiller.json> <out.xlsx>
  tiller.json: [{date, desc, amt, acct, chk}] pulled from the Tiller Transactions sheet.
"""
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_cost_ledger import FONT, FV_URL, MONEY, add_table, style_header  # noqa: E402
from qb_match import load_qb  # noqa: E402
from trust_firm_audit import COST_RE, FEE_RE, FIRM, case_id, is_trust_payout_acct  # noqa: E402

TRUST_ACCT, OPERATING, COST = "1206", "1107", "6455"
BANK_START = date(2025, 5, 1)  # first day of the trust account feed
RECENT_DAYS = {True: 30, False: 90}  # firm / client checks written this close to the feed's end may not have cleared yet
# Days within which a check normally clears; a feed gap inside that window means "can't confirm". Measured on this
# data: firm checks clear in 0-8 days (99.5%, slowest 26), client and lienholder checks in up to ~120.
CLEAR_DAYS = {True: 30, False: 120}
TOL = 0.01


def feed_gaps(days, start, end, min_len=4):
    """Runs of business days with no transactions at all: the bank feed did not sync, not a quiet bank."""
    gaps, run, d = [], None, start
    while d <= end:
        if d.weekday() < 5 and d not in days:
            run = run or d
        elif d.weekday() < 5:
            if run and (d - run).days >= min_len:
                gaps.append((run, d - timedelta(days=1)))
            run = None
        d += timedelta(days=1)
    return gaps


def _money(s):
    s = str(s).replace("$", "").replace(",", "").strip()
    neg = s.startswith("(") or s.startswith("-")
    s = s.strip("()-")
    return -float(s) if neg else float(s or 0)


def load_bank(path):
    rows = []
    for r in json.load(open(path)):
        if not r.get("date"):
            continue
        if r["acct"] == OPERATING and r.get("name") not in (None, "BOA Operating"):
            continue  # Tiller links operating twice (BOA Operating and Full Analysis Bus Chk); keep one copy
        rows.append(dict(date=datetime.strptime(r["date"], "%m/%d/%Y").date(), desc=r["desc"], amount=_money(r["amt"]),
                         acct=r["acct"], chk=(r.get("chk") or "").lstrip("0")))
    return rows


def bucket(memo):
    m = memo.lower()
    return "fee" if FEE_RE.search(memo) and "subro" not in m else "cost" if COST_RE.search(memo) else \
        "postage" if "postage" in m else "subro" if "subro" in m else "other"


def main(data_dir, qb_path, bank_path, out_path):
    bank = load_bank(bank_path)
    end = max(r["date"] for r in bank if r["acct"] == TRUST_ACCT)
    cleared = defaultdict(list)  # check number -> [(date, amount)]
    for r in bank:
        if r["acct"] == TRUST_ACCT and r["chk"] and r["amount"] < 0:
            cleared[r["chk"]].append((r["date"], -r["amount"]))

    checks = {}  # QB check number -> one record (split lines summed)
    for ln in load_qb(qb_path):
        if not is_trust_payout_acct(ln["acct"]) or ln["type"] != "Check" or ln["amount"] >= 0:
            continue
        num = re.sub(r"^VV", "", str(ln["num"] or "").strip(), flags=re.I).lstrip("0")  # Deluxe e-checks
        if not num.isdigit():
            continue
        c = checks.setdefault(num, dict(num=num, date=ln["date"], name=ln["name"], memo=ln["memo"], amount=0.0,
                                        pid=case_id(ln["memo"], ln["name"]), firm=FIRM in ln["name"]))
        c["amount"] += -ln["amount"]
        c["date"] = min(c["date"], ln["date"])

    gaps = feed_gaps({r["date"] for r in bank if r["acct"] == TRUST_ACCT}, BANK_START, end)
    in_gap = lambda d0, firm: any(g0 <= d0 + timedelta(days=CLEAR_DAYS[firm]) and g1 >= d0 for g0, g1 in gaps)  # noqa: E731
    # Bank checks whose number is not on any QB trust check: try them against unmatched QB checks by amount
    # (a check number keyed wrong in QuickBooks, or a different check stock).
    spare = defaultdict(list)  # amount -> [(date, number)]
    for n, v in cleared.items():
        if n not in checks:
            spare[round(sum(a for _, a in v), 2)].append((min(d for d, _ in v), n))

    results = []
    for c in sorted(checks.values(), key=lambda c: c["date"]):
        hits = cleared.get(c["num"], [])
        c["alt"] = None
        if not hits:
            for d, n in sorted(spare.get(round(c["amount"], 2), [])):
                if 0 <= (d - c["date"]).days <= 120:
                    hits, c["alt"] = cleared[n], n
                    spare[round(c["amount"], 2)].remove((d, n))
                    break
        paid = sum(a for _, a in hits)
        c["kind"] = bucket(c["memo"]) if c["firm"] else "client/third party"
        c["cleared_on"] = min(d for d, _ in hits) if hits else None
        c["cleared_amt"] = paid
        if c["date"] < BANK_START and not hits:
            c["status"] = "Before bank data"
        elif not hits and (end - c["date"]).days <= RECENT_DAYS[c["firm"]]:
            c["status"] = "Not cleared (recent)"
        elif not hits and in_gap(c["date"], c["firm"]):
            c["status"] = "Can't confirm (bank feed gap)"
        elif not hits:
            c["status"] = "Not cleared"
        elif c["alt"]:
            c["status"] = "Cleared under a different check #"
        elif abs(paid - c["amount"]) > TOL:
            c["status"] = "Cleared for a different amount"
        else:
            c["status"] = "Cleared"
        results.append(c)
    orphans = [dict(num=n, date=d, amount=a) for a, v in spare.items() for d, n in v if d >= BANK_START]

    tie = deposit_tie_out(bank, results, gaps, in_gap)

    status = defaultdict(lambda: defaultdict(float))  # pid -> result -> firm check dollars
    for c in results:
        if c["firm"] and c["pid"]:
            status[str(c["pid"])][c["status"]] += c["amount"]
    with open(os.path.join(data_dir, "bank_status.json"), "w") as f:
        json.dump(dict(end=end.isoformat(), cases=status), f)

    write(out_path, end, results, orphans, tie, gaps)
    print("feed gaps", gaps)
    from collections import Counter
    cnt = Counter((c["firm"], c["status"]) for c in results if c["date"] >= BANK_START)
    amt = defaultdict(float)
    for c in results:
        if c["date"] >= BANK_START:
            amt[(c["firm"], c["status"])] += c["amount"]
    for k in sorted(cnt):
        print(k, cnt[k], round(amt[k], 2))
    print("bank checks not in QB", len(orphans), round(sum(o["amount"] for o in orphans), 2), "end", end)


def deposit_tie_out(bank, results, gaps, in_gap):
    """Rows of (period, firm checks to operating, operating deposits, firm checks to cost, cost deposits)."""
    target = lambda c: COST if c["kind"] in ("cost", "postage") else OPERATING  # noqa: E731
    gap_of = lambda d: next((g for g in gaps if g[0] <= d <= g[1]), None)  # noqa: E731
    per = defaultdict(lambda: defaultdict(float))
    for r in bank:
        if r["acct"] in (OPERATING, COST) and r["amount"] > 0 and "preencoded" in r["desc"].lower() and r["date"] >= BANK_START:
            g = gap_of(r["date"])
            per[("gap", g) if g else ("month", r["date"].replace(day=1))]["dep" + r["acct"]] += r["amount"]
    for c in results:
        if not c["firm"]:
            continue
        if c["status"] == "Cleared":
            per[("month", c["cleared_on"].replace(day=1))]["chk" + target(c)] += c["cleared_amt"]
        elif c["status"].startswith("Can't confirm"):
            g = next(g for g in gaps if g[0] <= c["date"] + timedelta(days=CLEAR_DAYS[True]) and g[1] >= c["date"])
            per[("gap", g)]["chk" + target(c)] += c["amount"]
    return [(k, per[k]) for k in sorted(per, key=lambda k: k[1] if k[0] == "month" else k[1][0])]


def write(out_path, end, results, orphans, tie, gaps):
    wb = Workbook()
    sm = wb.active
    sm.title = "Summary"
    sm["A1"] = "Trust Check Clearing: QuickBooks trust checks vs the bank"
    sm["A1"].font = Font(name=FONT, bold=True, size=14)
    sm["A2"] = (f"Trust checks written {BANK_START} through the QuickBooks cutoff, matched by check number to Bank of America "
                f"trust account {TRUST_ACCT} (Tiller feed through {end}).")
    hd = ["Payee", "Result", "Checks", "Amount", "What it means"]
    sm.append([])
    sm.append(hd)
    style_header(sm, 4, len(hd))
    n = len(results) + 1
    lines = [
        ("Firm", "Not cleared", "Check to the firm written in QuickBooks but never cleared trust: firm money still in trust, or a voided check still on the books."),
        ("Firm", "Not cleared (recent)", f"Written within {RECENT_DAYS[True]} days of the bank feed's end; may still clear."),
        ("Firm", "Can't confirm (bank feed gap)", f"No match, but the bank feed has a sync gap within {CLEAR_DAYS[True]} days of the check date (firm checks clear in 0-8 days). Likely cleared during the gap; confirm on the bank statement."),
        ("Firm", "Cleared under a different check #", "Same amount cleared under a check number QuickBooks does not have. Fix the check number in QuickBooks."),
        ("Firm", "Cleared for a different amount", "Bank paid a different amount than QuickBooks shows. Correct the entry."),
        ("Firm", "Cleared", "Firm check cleared trust as recorded."),
        ("Client/third party", "Not cleared", "Client, lienholder or provider check not cashed after 90+ days: stale check, follow up or reissue (unclaimed property after the dormancy period)."),
        ("Client/third party", "Not cleared (recent)", f"Written within {RECENT_DAYS[False]} days of the bank feed's end; client checks can take up to ~100 days to be cashed."),
        ("Client/third party", "Can't confirm (bank feed gap)", "Bank feed sync gap; confirm on the bank statement."),
        ("Client/third party", "Cleared under a different check #", "Same amount cleared under another check number."),
        ("Client/third party", "Cleared for a different amount", "Bank paid a different amount than QuickBooks shows."),
        ("Client/third party", "Cleared", "Cleared as recorded."),
    ]
    for i, (payee, res, note) in enumerate(lines, start=5):
        crit = f'\'All Trust Checks\'!$E$2:$E${n},"{payee}",\'All Trust Checks\'!$J$2:$J${n},"{res}"'
        sm.append([payee, res, f"=COUNTIFS({crit})", f"=SUMIFS('All Trust Checks'!$H$2:$H${n},{crit})", note])
        sm.cell(i, 4).number_format = MONEY
    r = 5 + len(lines)
    sm.cell(r, 1, "Bank cleared, not in QuickBooks")
    sm.cell(r, 3, f"=COUNTA('Bank Not in QB'!A2:A{len(orphans) + 1})")
    sm.cell(r, 4, f"=SUM('Bank Not in QB'!C2:C{len(orphans) + 1})").number_format = MONEY
    sm.cell(r, 5, "Check cleared the trust account but no QuickBooks trust check carries that number or amount.")
    sm.cell(r + 2, 1, "Bank feed sync gaps (no trust activity at all): " + ", ".join(f"{a:%b %d %Y} to {b:%b %d %Y}" for a, b in gaps)
            + ". Pull the bank statements for these windows to close out the 'Can't confirm' items.")
    for c, w in zip("ABCDE", [20, 30, 9, 16, 100]):
        sm.column_dimensions[c].width = w

    head = ["Check #", "QB Date", "Payee", "Memo", "Payee Type", "Firm Bucket", "Filevine Project ID", "QB Amount",
            "Bank Cleared Amount", "Result", "Cleared On", "Filevine Link", "Bank Check #"]
    order = {"Not cleared": 0, "Cleared for a different amount": 1, "Cleared under a different check #": 2,
             "Can't confirm (bank feed gap)": 3, "Not cleared (recent)": 4, "Cleared": 5, "Before bank data": 6}
    data = sorted((c for c in results if c["date"] >= BANK_START or c["cleared_on"]),
                  key=lambda c: (order[c["status"]], not c["firm"], -c["amount"]))
    for title, rows in (("Firm Checks Not Cleared", [c for c in data if c["firm"] and not c["status"].startswith("Cleared") and c["status"] != "Before bank data"]),
                        ("Stale Client Checks", [c for c in data if not c["firm"] and not c["status"].startswith("Cleared") and c["status"] != "Before bank data"]),
                        ("All Trust Checks", data)):
        sh = wb.create_sheet(title)
        sh.append(head)
        style_header(sh, 1, len(head))
        for c in rows:
            sh.append([int(c["num"]), c["date"], c["name"], c["memo"], "Firm" if c["firm"] else "Client/third party",
                       c["kind"] if c["firm"] else "", c["pid"], c["amount"], c["cleared_amt"] or None, c["status"],
                       c["cleared_on"], FV_URL.format(c["pid"]) if c["pid"] else None, int(c["alt"]) if c["alt"] else None])
        for i in range(2, len(rows) + 2):
            for col in (8, 9):
                sh.cell(i, col).number_format = MONEY
            for col in (2, 11):
                sh.cell(i, col).number_format = "yyyy-mm-dd"
        if rows:
            add_table(sh, title.replace(" ", ""), 1, len(rows) + 1, len(head))
        sh.freeze_panes = "B2"
        for col, w in zip("ABCDEFGHIJKLM", [10, 11, 30, 44, 16, 10, 12, 13, 13, 28, 11, 18, 11]):
            sh.column_dimensions[col].width = w

    sh = wb.create_sheet("Bank Not in QB")
    sh.append(["Check #", "Cleared On", "Amount"])
    style_header(sh, 1, 3)
    for o in sorted(orphans, key=lambda o: -o["amount"]):
        sh.append([int(o["num"]), o["date"], o["amount"]])
    for i in range(2, len(orphans) + 2):
        sh.cell(i, 2).number_format = "yyyy-mm-dd"
        sh.cell(i, 3).number_format = MONEY

    sh = wb.create_sheet("Firm Deposit Tie-Out")
    head = ["Period", "Basis", "Firm Checks to Operating", "Operating Deposits (1107)", "Operating Ratio",
            "Firm Case Exp + Postage Checks", "Cost Deposits (6455)", "Cost Ratio"]
    sh.append(head)
    style_header(sh, 1, len(head))
    for i, ((kind, key), v) in enumerate(tie, start=2):
        label = f"{key:%b %Y}" if kind == "month" else f"Feed gap {key[0]:%b %d %Y} to {key[1]:%b %d %Y}"
        basis = "Checks seen clearing trust (gap days excluded)" if kind == "month" else "Checks the trust feed could not see; deposits made during the gap"
        sh.append([label, basis, round(v["chk" + OPERATING], 2), round(v["dep" + OPERATING], 2), f'=IF(C{i}=0,"",D{i}/C{i})',
                   round(v["chk" + COST], 2), round(v["dep" + COST], 2), f'=IF(F{i}=0,"",G{i}/F{i})'])
        for col in (3, 4, 6, 7):
            sh.cell(i, col).number_format = MONEY
        for col in (5, 8):
            sh.cell(i, col).number_format = "0.00"
        if kind == "gap":
            for col in range(1, len(head) + 1):
                sh.cell(i, col).font = Font(name=FONT, size=10, bold=True)
    k = len(tie) + 3
    sh.cell(k, 1, "Operating takes in more than trust checks (other income lands there), so a ratio a little over 1.00 is normal; "
                  "compare each gap row with the months around it. A cost ratio well under 1.00 means Case Exp checks written "
                  "in that window may not have reached the cost account: pull the bank statements for that window.")
    for col, w in zip("ABCDEFGH", [34, 52, 18, 18, 10, 18, 16, 10]):
        sh.column_dimensions[col].width = w

    for ws in wb.worksheets:
        for row in ws.iter_rows(min_row=2):
            for c in row:
                if c.font is None or not c.font.bold:
                    c.font = Font(name=FONT, size=10)
    wb.calculation.fullCalcOnLoad = True
    wb.save(out_path)


if __name__ == "__main__":
    main(*sys.argv[1:5])
