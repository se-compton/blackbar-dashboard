"""Load a QuickBooks cost-account export and match it to Filevine cases.

Accepts a CSV or XLSX export such as "Transaction Detail by Account" or
"Transaction List by Customer". The header row is found automatically; section
header and total rows (no date) are skipped.

Each QB line is assigned to a Filevine project by, in order:
  1. a project ID in parentheses in the name or memo, e.g. "Crumley, Ashley (15032058)"
     (the format the Filevine-to-QuickBooks sync writes),
  2. any bare 7-8 digit number in the name or memo that is a known project ID,
  3. a unique client-name match ("Last, First" or "First Last").

Sign convention: positive amounts are costs posted to the cost account (debits),
negative amounts are reimbursements or write-offs (credits). Pass flip=True if
the export uses the opposite sign.
"""
import csv
import re
from datetime import date, datetime, timedelta

from openpyxl import load_workbook

HEADER_ALIASES = {
    "date": ("date", "transaction date", "txn date"),
    "type": ("transaction type", "type", "txn type"),
    "num": ("num", "no.", "no", "check no", "check no.", "check #", "ref no.", "ref no", "doc num"),
    "name": ("name", "customer", "customer:job", "customer full name", "payee", "vendor", "customer/project",
             "project", "customer/job"),
    "memo": ("memo/description", "memo", "description", "line description"),
    "account": ("account", "account full name", "split", "distribution account"),
    "amount": ("amount", "net amount"),
    "debit": ("debit",),
    "credit": ("credit",),
}
PID_PAREN = re.compile(r"\((\d{6,9})\)")
PID_BARE = re.compile(r"(?<!\d)(\d{7,8})(?!\d)")


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
    for row in rows[hi + 1:]:
        get = lambda k: row[cols[k]] if k in cols and cols[k] < len(row) else None
        d = _date(get("date"))
        if d is None:
            continue
        amt = _money(get("amount"))
        if amt is None:
            dr, cr = _money(get("debit")) or 0.0, _money(get("credit")) or 0.0
            if not dr and not cr:
                continue
            amt = dr - cr
        if flip:
            amt = -amt
        lines.append(dict(date=d, type=str(get("type") or ""), num=str(get("num") or "").strip(),
                          name=str(get("name") or ""), memo=str(get("memo") or ""),
                          account=str(get("account") or ""), amount=round(amt, 2)))
    return lines


def _name_keys(name):
    """Keys for matching a client name: 'last first' from either 'Last, First' or 'First Last'."""
    s = re.sub(r"\(.*?\)", " ", str(name or ""))
    s = re.split(r"\s+-\s+", s)[0]  # "Jane Doe - MVA - 1/1/2025" -> "Jane Doe"
    s = re.sub(r"[^a-zA-Z, ]", " ", s).strip().lower()
    if not s:
        return set()
    if "," in s:
        last, first = [p.strip() for p in s.split(",", 1)]
        firsts = first.split()
        return {f"{last} {firsts[0]}"} if firsts and last else set()
    parts = [p for p in s.split() if p not in ("jr", "sr", "ii", "iii", "iv", "minor")]
    return {f"{parts[-1]} {parts[0]}"} if len(parts) >= 2 else set()


def assign_cases(lines, cands):
    """Set line['pid'] and line['how'] for each QB line. cands: {pid: candidate dict}."""
    by_name = {}
    for pid, c in cands.items():
        for src in (c.get("clientName"), c.get("projectName")):
            for k in _name_keys(src):
                by_name.setdefault(k, set()).add(pid)
    for ln in lines:
        text = f"{ln['name']} {ln['memo']}"
        pid, how = None, None
        m = PID_PAREN.search(text)
        if m and int(m.group(1)) in cands:
            pid, how = int(m.group(1)), "Project ID in QB name/memo"
        if pid is None:
            for m in PID_BARE.finditer(text):
                if int(m.group(1)) in cands:
                    pid, how = int(m.group(1)), "Project ID in QB name/memo"
                    break
        if pid is None:
            hits = set()
            for k in _name_keys(ln["name"]) | _name_keys(ln["memo"].split(":")[0]):
                hits |= by_name.get(k, set())
            if len(hits) == 1:
                pid, how = hits.pop(), "Client name match"
            elif len(hits) > 1:
                how = f"Ambiguous name ({len(hits)} cases)"
        ln["pid"], ln["how"] = pid, how or "Unidentified"
    return lines


def match_transactions(fv_txns, qb_lines, days=10):
    """Pair Filevine transactions with QB lines on the same case (same amount; same check # or close date).

    Sets t['qb_match'] / t['qb_ref'] on FV transactions and ln['fv_matched'] on QB lines.
    """
    by_case = {}
    for ln in qb_lines:
        ln["fv_matched"] = False
        if ln.get("pid"):
            by_case.setdefault(ln["pid"], []).append(ln)
    for t in fv_txns:
        t["qb_match"], t["qb_ref"] = None, None
        if not t.get("counted"):
            continue
        pool = [ln for ln in by_case.get(t["pid"], []) if not ln["fv_matched"] and abs(abs(ln["amount"]) - abs(t["amount"])) < 0.005]
        if not pool:
            t["qb_match"] = "N"
            continue
        ref = str(t.get("ref") or "").strip()
        td = _date(t.get("date"))

        def score(ln):
            s = 0 if ref and ln["num"] == ref else 1
            gap = abs((ln["date"] - td).days) if td else 999
            return (s, gap)

        best = min(pool, key=score)
        s, gap = score(best)
        if s == 0 or gap <= days:
            best["fv_matched"] = True
            t["qb_match"] = "Y"
            t["qb_ref"] = f"{best['date'].isoformat()} {best['type']} {best['num']}".strip()
        else:
            t["qb_match"] = "N"
    return fv_txns, qb_lines
