"""Load a QuickBooks cost-account export and match it to Filevine cases.

Accepts a CSV or XLSX export such as "Transaction Detail by Account". The header
row is found automatically; section header and total rows (no date) are skipped.

The firm's Advanced Client Costs account does not carry the case on each line
(Name is the vendor), so lines are tied to cases mainly through the Filevine
transactions, which do carry the case:
  1. Filevine expense check  -> QB Check with the same check number and amount
  2. Filevine card expense   -> QB card Expense with the same amount within 7 days
  3. Filevine Due-to-Firm expense disbursal -> QB Deposit (credit) with the same
     amount within 21 days
A QB line can also be tied directly when its name or description holds a
Filevine project ID, e.g. "Gain Ford (10933334)" or "Falter, Lawrence {8330767}".

Sign convention: positive amounts are costs posted to the cost account (debits),
negative amounts are reimbursements or write-offs (credits). Pass flip=True if
the export uses the opposite sign.
"""
import bisect
import csv
import re
from datetime import date, datetime

from openpyxl import load_workbook

HEADER_ALIASES = {
    "date": ("date", "transaction date", "txn date"),
    "type": ("transaction type", "type", "txn type"),
    "num": ("num", "no.", "no", "check no", "check no.", "check #", "ref no.", "ref no", "doc num"),
    "name": ("name", "customer", "customer:job", "customer full name", "payee", "vendor", "customer/project",
             "project", "customer/job"),
    "memo": ("memo/description", "memo", "description", "line description"),
    "account": ("split", "account", "account full name", "distribution account"),
    "cls": ("class", "class full name"),
    "amount": ("amount", "net amount"),
    "debit": ("debit",),
    "credit": ("credit",),
}
PID_PAT = re.compile(r"[\(\{](\d{7,8})[\)\}]")
CARD_TYPES = ("expense", "credit card expense", "credit card credit")


def _norm(s):
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def _money(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("$", "").replace(",", "")
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        x = float(s)
    except ValueError:
        return None
    return -x if neg else x


def _date(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y", "%m-%d-%Y"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            pass
    return None


def _rows(path):
    if path.lower().endswith((".xlsx", ".xlsm")):
        ws = load_workbook(path, data_only=True, read_only=True).active
        return [list(r) for r in ws.iter_rows(values_only=True)]
    with open(path, newline="", encoding="utf-8-sig") as f:
        return [row for row in csv.reader(f)]


def _find_header(rows):
    for i, row in enumerate(rows[:40]):
        cells = [_norm(c) for c in row]
        cols = {}
        for key, aliases in HEADER_ALIASES.items():
            for j, c in enumerate(cells):
                if c in aliases and key not in cols:
                    cols[key] = j
        if "date" in cols and ("amount" in cols or "debit" in cols or "credit" in cols):
            return i, cols
    raise ValueError("Could not find a header row with Date and Amount/Debit/Credit columns in the QB export")


def load_qb(path, flip=False):
    rows = _rows(path)
    hi, cols = _find_header(rows)
    lines = []
    acct = None
    for row in rows[hi + 1:]:
        get = lambda k: row[cols[k]] if k in cols and cols[k] < len(row) else None
        d = _date(get("date"))
        if d is None:
            first = row[0] if row else None
            if first and not str(first).startswith(("Total", "TOTAL", "Accrual", "Cash Basis")):
                acct = str(first).strip()
            continue
        amt = _money(get("amount"))
        if amt is None:
            dr, cr = _money(get("debit")) or 0.0, _money(get("credit")) or 0.0
            amt = dr - cr
        if flip:
            amt = -amt
        lines.append(dict(date=d, type=str(get("type") or ""), num=str(get("num") or "").strip(),
                          name=str(get("name") or ""), memo=str(get("memo") or ""),
                          account=str(get("account") or ""), cls=str(get("cls") or ""),
                          amount=round(amt, 2), pid=None, how=None, fv_matched=False, acct=acct))
    return lines


COST_ACCOUNT = "Advanced Client Costs"
FV_ID = re.compile(r"(?<!\d)(\d{7,8})(?!\d)")


def _fv_id(text):
    """First plausible Filevine project ID (7-8 digits, 8M-16M range) in a memo."""
    for m in FV_ID.finditer(text or ""):
        v = int(m.group(1))
        if 8_000_000 <= v < 16_000_000:
            return v
    return None


def split_general_ledger(lines):
    """For a full general-ledger export, return (cost-account lines, all other lines).
    For a single-account export, everything is the cost account."""
    accts = {ln["acct"] for ln in lines}
    if len(accts) <= 1:
        return lines, []
    return ([ln for ln in lines if ln["acct"] == COST_ACCOUNT],
            [ln for ln in lines if ln["acct"] != COST_ACCOUNT])


def enrich_from_ledger(cost_lines, other_lines):
    """Tie cost-account lines to cases using the other side of each transaction in a full GL:
      - cost checks  <- the Hostilo Cost bank line with the same check # and amount (memo 'Name/ID/...')
      - card charges <- the card-account line with the same date and amount whose memo holds an ID
      - Case Exp deposits (credits) <- the trust check to the firm memo'd 'Last, First (ID):Case Exp'
        with the same amount within 14 days
    Also returns {pid: total} of trust 'Case Exp' checks paid to the firm, by case."""
    bank = {}
    card = {}
    trust = {}
    trust_by_case = {}
    for ln in other_lines:
        a = ln["acct"] or ""
        pid = _fv_id(f"{ln['name']} {ln['memo']}")
        if "Cost (6455)" in a and ln["type"] == "Check" and pid:
            bank.setdefault((ln["num"], round(abs(ln["amount"]) * 100)), []).append(pid)
        elif ("7818" in a or "COST SRC" in a or "7184" in a) and pid:
            card.setdefault((ln["date"], round(abs(ln["amount"]) * 100)), []).append(pid)
        elif a == "Hostilo Trust" and ln["type"] == "Check" and "hostilo" in ln["name"].lower() \
                and re.search(r"case\s*exp", ln["memo"], re.I) and pid:
            trust.setdefault(round(abs(ln["amount"]) * 100), []).append([ln["date"], pid, False])
            trust_by_case[pid] = trust_by_case.get(pid, 0.0) + abs(ln["amount"])
    for ln in cost_lines:
        if ln["pid"]:
            continue
        key = round(abs(ln["amount"]) * 100)
        if ln["type"] == "Check" and (ln["num"], key) in bank:
            ln["pid"], ln["how"] = bank[(ln["num"], key)][0], "Case ID on the cost-bank side of the check"
        elif ln["type"] != "Deposit" and (ln["date"], key) in card:
            ln["pid"], ln["how"] = card[(ln["date"], key)][0], "Case ID on the card-account side of the charge"
        elif ln["type"] == "Deposit" and ln["amount"] < 0 and key in trust:
            best, gap = None, None
            for cand in trust[key]:
                if cand[2]:
                    continue
                g = abs((cand[0] - ln["date"]).days)
                if g <= 14 and (gap is None or g < gap):
                    best, gap = cand, g
            if best:
                best[2] = True
                ln["pid"], ln["how"] = best[1], "Trust 'Case Exp' check to the firm (amount/date)"
    return cost_lines, trust_by_case


def assign_direct(lines, cands):
    """Tie QB lines that name a Filevine project ID in their name or description."""
    for ln in lines:
        for m in PID_PAT.finditer(f"{ln['name']} {ln['memo']}"):
            if int(m.group(1)) in cands:
                ln["pid"], ln["how"] = int(m.group(1)), "Project ID in QB name/description"
                break
    return lines


class _Pool:
    """QB lines indexed by (kind, cents) and sorted by date for nearest-date lookups."""

    def __init__(self, lines, kind_of):
        self.idx = {}
        for ln in lines:
            k = kind_of(ln)
            if k:
                self.idx.setdefault((k, round(abs(ln["amount"]) * 100)), []).append(ln)
        self.dates = {}
        for key, v in self.idx.items():
            v.sort(key=lambda x: x["date"])
            self.dates[key] = [x["date"].toordinal() for x in v]

    def take(self, kind, amount, when, days, num=None, pid=None):
        key = (kind, round(abs(amount) * 100))
        cands = self.idx.get(key, [])
        if num is not None:
            for ln in cands:
                if not ln["fv_matched"] and ln["num"] == num and not (ln["pid"] and pid and ln["pid"] != pid):
                    return ln
            return None
        if when is None or not cands:
            return None
        ds, w = self.dates[key], when.toordinal()
        lo, hi = bisect.bisect_left(ds, w - days), bisect.bisect_right(ds, w + days)
        best, best_gap = None, None
        for i in range(lo, hi):
            ln = cands[i]
            if ln["fv_matched"] or (ln["pid"] and pid and ln["pid"] != pid):
                continue
            gap = abs(ds[i] - w)
            if best_gap is None or gap < best_gap:
                best, best_gap = ln, gap
        return best


def _kind(ln):
    t = ln["type"].lower()
    if t == "check":
        return "check"
    if t in CARD_TYPES or "card" in ln["account"].lower():
        return "card"
    if t == "deposit" and ln["amount"] < 0:
        return "deposit"
    return None


def match_transactions(fv_txns, qb_lines):
    """Pair Filevine transactions with QB lines. Sets t['qb_match'], t['qb_ref'] on FV rows and
    pid/how/fv_matched on the QB lines that pair up."""
    pool = _Pool(qb_lines, _kind)

    def claim(t, ln, how):
        ln["fv_matched"] = True
        if ln["pid"] == t["pid"] and ln["how"]:
            ln["how"] = f"{ln['how']}; confirmed by Filevine"
        else:
            ln["pid"], ln["how"] = t["pid"], how
        t["qb_match"] = "Y"
        t["qb_ref"] = f"{ln['date'].isoformat()} {ln['type']} {ln['num']}".strip()

    # Check-number matches first (strongest), then date/amount matches.
    ordered = sorted(fv_txns, key=lambda t: 0 if (t.get("src") == "Expense Request" and str(t.get("ref") or "").strip()) else 1)
    for t in ordered:
        t["qb_match"], t["qb_ref"] = None, None
        if not t.get("counted") or not t.get("amount"):
            continue
        when = _date(t.get("date"))
        ref = str(t.get("ref") or "").strip()
        ln = None
        if t["src"] == "Expense Request":
            if ref:
                ln = pool.take("check", t["amount"], when, 0, num=ref, pid=t["pid"])
                if ln:
                    claim(t, ln, "Matched to FV expense (check #)")
                    continue
            ln = pool.take("card", t["amount"], when, 7, pid=t["pid"]) or pool.take("check", t["amount"], when, 7, pid=t["pid"])
            if ln:
                claim(t, ln, "Matched to FV expense (amount/date)")
                continue
        elif t["src"] == "Disbursal" and "Postage" not in str(t.get("method") or ""):
            ln = pool.take("deposit", t["amount"], when, 21, pid=t["pid"])
            if ln:
                claim(t, ln, "Matched to FV Due-to-Firm disbursal (amount, within 21 days)")
                continue
            t["qb_match"] = "N"  # may be picked up by the wider second pass below
            continue
        else:  # postage (logged or disbursed) is not posted to the QB cost account
            t["qb_match"] = None
            continue
        t["qb_match"] = "N"
    # Second pass: Filevine rarely records the disbursal check date, so the fallback date (when the
    # disbursal row was created) can run weeks ahead of the QB deposit. Widen to 90 days.
    for t in fv_txns:
        if t.get("qb_match") == "N" and t["src"] == "Disbursal":
            ln = pool.take("deposit", t["amount"], _date(t.get("date")), 90, pid=t["pid"])
            if ln:
                claim(t, ln, "Matched to FV Due-to-Firm disbursal (amount, 22-90 days)")
    return fv_txns, qb_lines
