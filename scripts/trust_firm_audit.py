"""Trust-to-firm audit: settled cases that look fully disbursed from trust while the firm
was never paid its fee or reimbursed its case costs.

Sources
  Filevine pull (<data_dir>/candidates.json, <data_dir>/results/*.jsonl): settlement amount,
      disposition, case costs logged, Due-to-Firm expense disbursals, write-offs (FRD/REJ).
  QuickBooks full general ledger export: the trust bank account (every trust check carries the
      case ID, e.g. "Last, First (12345678):Atty Fee"), the Advanced Client Costs account (costs
      advanced per case and the trust 'Case Exp' reimbursements that landed), and Disbursement
      Income (attorney fees deposited to operating).

Method, per settled case
  1. Roll up every trust check tagged with the case ID: client, third party (liens, providers),
     and checks to the firm split into Atty Fee, Case Exp, Postage and other.
  2. A case is treated as fully disbursed when the client has been paid from trust.
  3. Expected firm money: a fee (any Atty Fee check) and, when costs were advanced, a cost
     reimbursement (or a recorded write-off).
  4. Exceptions: no fee, fee under the review threshold, costs advanced but never reimbursed,
     cost reimbursement short of costs advanced, settlement not fully run through trust, and
     cost reimbursements written from trust that never landed in the cost account.
  5. Firm-wide: trust Atty Fee checks vs Disbursement Income deposits by month, with a running
     balance of fee checks written to the firm but not yet deposited.

Usage: python3 scripts/trust_firm_audit.py <data_dir> <qb_full.xlsx> <output.xlsx> [as_of]
"""
import glob
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_cost_ledger import FONT, FV_URL, MONEY, SCOPE_START, add_table, disposition, num, style_header  # noqa: E402
from qb_match import assign_direct, enrich_from_ledger, load_qb, split_general_ledger  # noqa: E402

TRUST_BANK = "Hostilo Trust"
COST_ACCOUNT = "Advanced Client Costs"
FEE_INCOME = "Disbursement Income"
FIRM = "Hostilo, LLC"
LOW_FEE_PCT = 0.20      # fee under 20% of gross settlement is flagged for review
SHORT_TOL = 25.00       # cost reimbursement short by more than this is flagged
RECENT_DAYS = 21        # client paid this close to the QB data cutoff: firm checks may simply be pending
CASE_ID = re.compile(r"(?<!\d)(\d{7,8})(?!\d)")  # "(12345678)", "{12345678}" or older "12345678/Last, First/..."
FEE_RE = re.compile(r"att?n?y\b|attorney|af\s*reimb", re.I)
COST_RE = re.compile(r"case\s*exp|:\s*cas(e)?\s*$", re.I)
CLIENT_RE = re.compile(r"PI Settlement|Remaining PI|Medpay|Partial (Payment|Disbursement)|PD Proceeds", re.I)


def case_id(*texts):
    for t in texts:
        for m in CASE_ID.finditer(t or ""):
            v = int(m.group(1))
            if 8_000_000 <= v < 16_000_000:
                return v
    return None


def load_fv(data_dir):
    cands = {c["projectId"]: c for c in json.load(open(f"{data_dir}/candidates.json"))}
    pulled = {}
    for f in sorted(glob.glob(f"{data_dir}/results/*.jsonl")):
        for line in open(f):
            if not line.strip():
                continue
            r = json.loads(line)
            pid = int(r["projectId"])
            if "add_disb" in r and pid in pulled and not pulled[pid].get("screen"):
                pulled[pid].setdefault("disb", []).extend(r["add_disb"])
                continue
            pulled[pid] = r
    return cands, pulled


def main(data_dir, qb_path, out_path, as_of):
    cands, pulled = load_fv(data_dir)
    lines = load_qb(qb_path)
    start = date.fromisoformat(SCOPE_START)

    trust = defaultdict(lambda: defaultdict(float))     # pid -> bucket -> amount (positive = paid out)
    trust_lines = defaultdict(list)
    cost_adv = defaultdict(float)                       # QB costs advanced per case (cost account debits)
    cost_landed = defaultdict(float)                    # trust Case Exp credits landed in cost account
    fee_out, fee_in = defaultdict(float), defaultdict(float)
    client_date, cutoff = {}, date(2000, 1, 1)
    for ln in lines:
        acct, memo, name, amt = ln["acct"] or "", ln["memo"], ln["name"], ln["amount"]
        if acct == TRUST_BANK and ln["type"] in ("Check", "Expense") and amt < 0:
            paid = -amt
            to_firm = FIRM in name
            if to_firm and FEE_RE.search(memo) and ln["date"] >= start:
                fee_out[ln["date"].strftime("%Y-%m")] += paid
            pid = case_id(memo, name)
            if pid is None:
                continue
            if to_firm:
                bucket = "fee" if FEE_RE.search(memo) else "cost" if COST_RE.search(memo) else \
                    "postage" if "postage" in memo.lower() else "firm_other"
            elif f"{{{pid}}}" in name or CLIENT_RE.search(memo):
                bucket = "client"
            else:
                bucket = "third_party"
            trust[pid][bucket] += paid
            trust[pid]["n"] += 1
            if bucket == "client":
                client_date[pid] = max(client_date.get(pid, ln["date"]), ln["date"])
            cutoff = max(cutoff, ln["date"])
            trust_lines[pid].append((ln["date"].isoformat(), ln["num"], name, memo, bucket, paid))
        elif acct == FEE_INCOME and ln["date"] >= start:
            fee_in[ln["date"].strftime("%Y-%m")] += amt

    # Cost account lines carry the vendor, not the case; tie them to cases through the other side of
    # each transaction (cost-bank check memo, card memo, or the trust 'Case Exp' check for deposits).
    cost_lines, other = split_general_ledger(lines)
    cost_lines, _ = enrich_from_ledger(assign_direct(cost_lines, cands), other)
    for ln in cost_lines:
        if not ln["pid"]:
            continue
        if ln["amount"] > 0:
            cost_adv[ln["pid"]] += ln["amount"]
        elif "Trust 'Case Exp'" in (ln["how"] or ""):
            cost_landed[ln["pid"]] += -ln["amount"]

    rows, flagged = [], []
    for pid, case in cands.items():
        r = pulled.get(pid) or {}
        cs = r.get("cs") or {}
        group, detail, ddate, settled = disposition(case, cs)
        if group != "Settled" or (ddate or "") < SCOPE_START:
            continue
        t = trust.get(pid, {})
        fv_logged = sum(num(e.get("amount")) for e in r.get("exp") or [] if (e.get("status") or "") != "Void") + \
            sum(num(p.get("amountdue")) or num(p.get("transactionamount")) for p in r.get("post") or [] if (p.get("status") or "") != "Voided")
        fv_disb = sum(num(d.get("amountpaid")) or num(d.get("amountdue")) for d in r.get("disb") or []
                      if "Postage" not in str(d.get("type")) and (d.get("status") or "") != "Voided")
        writeoff = any(re.search(r"FRD|REJ|write", str(d.get("memo")), re.I) for d in r.get("disb") or [])
        fee, cost = t.get("fee", 0.0), t.get("cost", 0.0)
        client, third = t.get("client", 0.0), t.get("third_party", 0.0)
        out_total = sum(v for k, v in t.items() if k != "n")
        paid_on = client_date.get(pid)
        recent = paid_on is not None and (cutoff - paid_on).days <= RECENT_DAYS
        costs_expected = max(cost_adv.get(pid, 0.0), fv_disb)
        disbursed = client > 0
        flags = []
        if not t:
            flags.append("No trust checks tagged to this case")
        if recent and disbursed and (fee == 0 or (costs_expected > 0 and cost == 0)):
            flags.append(f"Client paid within {RECENT_DAYS} days of the data cutoff (firm checks may still be pending)")
        # Firm money that is not labeled Atty Fee: other firm checks, plus any Case Exp beyond costs advanced.
        firm_unlabeled = t.get("firm_other", 0.0) + max(0.0, cost - costs_expected)
        if disbursed and settled > 0 and fee == 0 and firm_unlabeled >= LOW_FEE_PCT * settled:
            flags.append("Fee likely paid to firm under another memo (not labeled Atty Fee)")
        elif disbursed and settled > 0 and fee == 0:
            flags.append("Client paid, no attorney fee check to firm")
        elif disbursed and settled > 0 and 0 < fee < LOW_FEE_PCT * settled:
            flags.append(f"Fee under {LOW_FEE_PCT:.0%} of settlement")
        if disbursed and costs_expected > 0 and cost == 0 and not writeoff:
            flags.append("Costs advanced, no Case Exp reimbursement from trust")
        elif disbursed and cost > 0 and costs_expected - cost > SHORT_TOL:
            flags.append("Cost reimbursement short of costs advanced")
        if disbursed and settled > 0 and settled - out_total > 100:
            flags.append("Settlement not fully run through trust")
        if cost - cost_landed.get(pid, 0.0) > SHORT_TOL:
            flags.append("Case Exp check from trust not found in cost account")
        row = dict(pid=pid, name=case["projectName"], ddate=ddate, paid_on=paid_on.isoformat() if paid_on else None, settled=settled, client=client, third=third,
                   fee=fee, fee_pct=(fee / settled) if settled else None, cost=cost, postage=t.get("postage", 0.0),
                   firm_other=t.get("firm_other", 0.0), out_total=out_total, cost_adv=cost_adv.get(pid, 0.0),
                   fv_logged=fv_logged, fv_disb=fv_disb, landed=cost_landed.get(pid, 0.0), writeoff=writeoff,
                   flags="; ".join(flags))
        rows.append(row)
        if flags:
            flagged.append(row)

    write_workbook(out_path, as_of, rows, flagged, trust_lines, fee_out, fee_in)
    counts = defaultdict(int)
    for r in flagged:
        for f in r["flags"].split("; "):
            counts[f] += 1
    print(json.dumps(dict(settled_cases=len(rows), flagged=len(flagged), by_flag=counts), indent=1))


HEAD = ["Filevine Project ID", "Case Name", "Settlement Date", "FV Gross Settlement", "Paid to Client (trust)",
        "Paid to Third Parties (trust)", "Atty Fee to Firm (trust)", "Fee % of Settlement", "Case Exp to Firm (trust)",
        "Postage to Firm (trust)", "Other to Firm (trust)", "Total Paid Out of Trust", "Settlement minus Paid Out",
        "QB Costs Advanced (cost acct)", "FV Costs Logged", "FV Due-to-Firm Expense Disbursal",
        "Case Exp Landed in Cost Acct", "FV Write-off (FRD/REJ)", "Exceptions", "Filevine Link", "Last Client Payment (trust)"]


def case_row(ws, i, r):
    ws.append([r["pid"], r["name"], r["ddate"], r["settled"], r["client"], r["third"], r["fee"], None, r["cost"],
               r["postage"], r["firm_other"], r["out_total"], None, r["cost_adv"], r["fv_logged"], r["fv_disb"],
               r["landed"], "Yes" if r["writeoff"] else "", r["flags"], FV_URL.format(r["pid"]), r["paid_on"]])
    ws.cell(i, 8, f'=IF(D{i}=0,"",G{i}/D{i})').number_format = "0.0%"
    ws.cell(i, 13, f"=D{i}-L{i}")
    for c in (4, 5, 6, 7, 9, 10, 11, 12, 13, 14, 15, 16, 17):
        ws.cell(i, c).number_format = MONEY


def write_workbook(out_path, as_of, rows, flagged, trust_lines, fee_out, fee_in):
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    bold = Font(name=FONT, bold=True, size=12)
    ws["A1"] = "Trust-to-Firm Audit: settled cases disbursed without the firm being paid"
    ws["A1"].font = Font(name=FONT, bold=True, size=14)
    ws["A2"] = f"Settled cases with a settlement date from {SCOPE_START} through {as_of}. Sources: Filevine case data and the QuickBooks general ledger."
    ws.append([])
    ws.append(["Exception", "Cases", "Dollars at Issue", "What it means"])
    style_header(ws, 4, 4)
    n = len(flagged) + 1
    defs = [
        ("Client paid, no attorney fee check to firm", "D", "Client was paid from trust but no Atty Fee check to the firm is tagged to the case. Dollars = gross settlement."),
        ("Fee likely paid to firm under another memo", "K", "No check is labeled Atty Fee, but the firm received a fee-sized check memo'd PI Settlement, Case Exp or blank. Fix the coding; the money likely moved."),
        ("Fee under", "G", f"Fee check exists but is under {LOW_FEE_PCT:.0%} of the gross settlement. Could be a fee reduction; confirm against the closing statement."),
        ("Costs advanced, no Case Exp reimbursement from trust", "N", "QB shows costs advanced (or Filevine shows a Due-to-Firm expense disbursal) but no Case Exp check came out of trust and no write-off is recorded."),
        ("Cost reimbursement short of costs advanced", "N", "A Case Exp check was written, but for less than the costs advanced."),
        ("Settlement not fully run through trust", "M", "Filevine gross settlement exceeds everything paid out of trust for the case. Funds may still sit in trust, or part was paid outside trust."),
        ("Case Exp check from trust not found in cost account", "I", "Trust wrote a Case Exp check to the firm, but no matching credit landed in Advanced Client Costs."),
        ("No trust checks tagged to this case", "D", "Filevine shows a settlement but no trust check carries this case ID."),
        ("data cutoff", "D", f"Client paid within {RECENT_DAYS} days of the QuickBooks data cutoff; a missing fee or cost check may simply not be written yet. Recheck before acting."),
    ]
    for i, (label, col, desc) in enumerate(defs, start=5):
        ws.append([label, f'=COUNTIF(Exceptions!$S$2:$S${n},"*{label}*")',
                   f'=SUMIFS(Exceptions!${col}$2:${col}${n},Exceptions!$S$2:$S${n},"*{label}*")', desc])
        ws.cell(i, 3).number_format = MONEY
    r0 = 5 + len(defs) + 1
    ws.cell(r0, 1, "Settled cases reviewed").font = bold
    ws.cell(r0, 2, len(rows))
    ws.cell(r0 + 1, 1, "Cases with at least one exception").font = bold
    ws.cell(r0 + 1, 2, len(flagged))
    notes = [
        "Method",
        "1. Every trust bank check in QuickBooks is tagged to a case by the Filevine ID in its memo or payee, e.g. 'Last, First (12345678):Atty Fee'.",
        "2. Checks are bucketed: client (payee is the client or memo PI Settlement / Remaining PI Funds), third party (liens, providers, subrogation), and firm (Atty Fee, Case Exp, Postage, other).",
        "3. A case counts as disbursed once the client has been paid from trust. The firm should then have taken its fee and, where costs were advanced, a Case Exp reimbursement or a recorded write-off.",
        "4. Costs advanced come from the QB Advanced Client Costs account (lines tagged with the case ID) and the Filevine Due-to-Firm expense disbursal, whichever is larger.",
        "5. The Fee Tie-Out tab checks that attorney fee checks written from trust were deposited to operating (Disbursement Income), month by month with a running balance.",
        "Limits: trust deposits are not tagged by case in QuickBooks, so the gross settlement comes from Filevine. Fee agreements are not in the data, so the fee test flags low fees for review rather than computing the contract fee.",
    ]
    for j, t in enumerate(notes):
        c = ws.cell(r0 + 3 + j, 1, t)
        c.font = bold if j == 0 else Font(name=FONT, size=10)
    ws.column_dimensions["A"].width = 58
    ws.column_dimensions["B"].width = 10
    ws.column_dimensions["C"].width = 18
    ws.column_dimensions["D"].width = 110

    for title, data in (("Exceptions", sorted(flagged, key=lambda r: (-r["settled"], r["pid"]))),
                        ("All Settled Cases", sorted(rows, key=lambda r: r["pid"]))):
        sh = wb.create_sheet(title)
        sh.append(HEAD)
        style_header(sh, 1, len(HEAD))
        for i, r in enumerate(data, start=2):
            case_row(sh, i, r)
        if data:
            add_table(sh, title.replace(" ", ""), 1, len(data) + 1, len(HEAD))
        sh.freeze_panes = "C2"
        for c, w in zip("ABCDEFGHIJKLMNOPQRSTU", [12, 34, 12] + [14] * 15 + [60, 18, 12]):
            sh.column_dimensions[c].width = w

    st = wb.create_sheet("Fee Tie-Out")
    st.append(["Month", "Atty Fee Checks Written from Trust", "Disbursement Income Deposited", "Difference", "Running Undeposited"])
    style_header(st, 1, 5)
    months = sorted(set(fee_out) | set(fee_in))
    for i, m in enumerate(months, start=2):
        st.append([m, round(fee_out[m], 2), round(fee_in[m], 2), f"=B{i}-C{i}", f"=D{i}" if i == 2 else f"=E{i-1}+D{i}"])
        for c in (2, 3, 4, 5):
            st.cell(i, c).number_format = MONEY
    for c, w in zip("ABCDE", [10, 30, 30, 16, 20]):
        st.column_dimensions[c].width = w

    sd = wb.create_sheet("Trust Detail (Exceptions)")
    sd.append(["Filevine Project ID", "Check Date", "Check #", "Payee", "Memo", "Bucket", "Amount"])
    style_header(sd, 1, 7)
    i = 1
    for r in sorted(flagged, key=lambda r: r["pid"]):
        for d, num_, name, memo, bucket, amt in sorted(trust_lines.get(r["pid"], [])):
            sd.append([r["pid"], d, num_, name, memo, bucket, amt])
            i += 1
            sd.cell(i, 7).number_format = MONEY
    for c, w in zip("ABCDEFG", [12, 11, 10, 34, 50, 12, 14]):
        sd.column_dimensions[c].width = w

    for sh in wb.worksheets:
        for row in sh.iter_rows(min_row=2):
            for c in row:
                if c.font is None or not c.font.bold:
                    c.font = Font(name=FONT, size=10)
                c.alignment = Alignment(vertical="top")
    wb.calculation.fullCalcOnLoad = True
    wb.save(out_path)


if __name__ == "__main__":
    a = sys.argv[1:]
    main(a[0], a[1], a[2], a[3] if len(a) > 3 else date.today().isoformat())
