"""Cross-check the firm's Disbursed List (what each closing statement says the firm gets) against the
trust checks actually written to the firm in QuickBooks.

For every disbursed case on the log, find the case in the QuickBooks trust account by client name
(trust check memos read "Last, First (FilevineID):Atty Fee") near the disbursement date, then compare
the logged Atty Fee, Case Exp and Postage with the trust checks to the firm.

Usage: python3 scripts/disbursement_log_check.py <qb_full.xlsx> <out.xlsx> <Disbursements.xlsx> [more logs...]
"""
import re
import sys
import unicodedata
from collections import defaultdict
from datetime import date, datetime

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from build_cost_ledger import FONT, FV_URL, MONEY, add_table, style_header  # noqa: E402
from qb_match import load_qb  # noqa: E402
from trust_firm_audit import FEE_RE, COST_RE, FIRM, TRUST_BANK, case_id  # noqa: E402

TOL = 1.00          # dollars of rounding allowed before a difference is reported
WINDOW = 60         # firm checks within this many days of the log's DSB date belong to that disbursement
SUFFIX = {"jr", "sr", "ii", "iii", "iv", "v"}


def _clean(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z ,'-]", " ", s)


def _words(s):
    return [t.replace("'", "") for t in re.split(r"[ ,.\-]+", _clean(s)) if t and t not in SUFFIX]


def key_from_log(name):
    """'First M. Darden-Rivers Jr.' -> ('rivers', 'first'): last word of the surname, first given name."""
    toks = _words(name)
    return (toks[-1], toks[0]) if len(toks) >= 2 else None


def key_from_qb(text):
    """'Darden - Rivers, Angela {123}' or '123/Last, First/Case Exp' -> ('rivers', 'angela')."""
    m = re.search(r"([A-Za-z'\- .]+),\s*([A-Za-z'\-]+)", text or "")
    if not m:
        return None
    last, first = _words(m.group(1)), _words(m.group(2))
    return (last[-1], first[0]) if last and first else None


def _d(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return None


def read_log(path):
    ws = load_workbook(path, read_only=True, data_only=True)["Disbursed List"]
    rows = list(ws.iter_rows(values_only=True))
    hi = next(i for i, r in enumerate(rows) if r and r[1] == "Client")
    col = {h: i for i, h in enumerate(rows[hi]) if h}
    out = []
    for r in rows[hi + 1:]:
        if not r[col["Client"]] or not isinstance(r[col["Settlement $"]], (int, float)):
            continue
        g = lambda h: r[col[h]] if h in col else None  # noqa: E731
        num = lambda v: float(v) if isinstance(v, (int, float)) else 0.0  # noqa: E731
        out.append(dict(source=path.split("/")[-1], client=g("Client"), settled=num(g("Settlement $")),
                        fee=num(g("Atty Fee")), postage=num(g("Postage")), cost=num(g("Case Exp")),
                        attorney=g("Attorney"), cm=g("Case Manager"), market=g("Market"),
                        dsb=_d(g("DSB Date")), set_date=_d(g("SET Date")), notes=g("Notes")))
    return out


def main(qb_path, out_path, logs):
    names = defaultdict(set)          # (last, first) -> case IDs seen on any trust check (payee or memo)
    activity = defaultdict(list)      # pid -> dates of any trust check
    firm = defaultdict(list)          # pid -> [(date, bucket, amount)] checks written to the firm
    for ln in load_qb(qb_path):
        if (ln["acct"] or "") != TRUST_BANK or ln["amount"] >= 0:
            continue
        pid = case_id(ln["memo"], ln["name"])
        if not pid:
            continue
        for text in (ln["name"], ln["memo"]):
            k = key_from_qb(text)
            if k:
                names[k].add(pid)
        activity[pid].append(ln["date"])
        if FIRM in ln["name"]:
            memo = ln["memo"]
            bucket = "fee" if FEE_RE.search(memo) else "cost" if COST_RE.search(memo) else \
                "postage" if "postage" in memo.lower() else "other"
            firm[pid].append((ln["date"], bucket, -ln["amount"]))

    results = []
    for path in logs:
        for r in read_log(path):
            k = key_from_log(r["client"])
            pids = names.get(k, set()) if k else set()
            pid, gap = None, None
            for p in pids:  # the case whose trust activity sits closest to the log's DSB date
                g = min(abs((d - r["dsb"]).days) for d in activity[p]) if r["dsb"] else 0
                if gap is None or g < gap:
                    pid, gap = p, g
            got = defaultdict(float)
            if pid is not None and r["dsb"]:
                for d, bucket, amt in firm[pid]:
                    if -WINDOW <= (d - r["dsb"]).days <= 3 * WINDOW:
                        got[bucket] += amt
            status = []
            if pid is None or gap > 3 * WINDOW:
                status.append("Client not found in trust near the DSB date")
            elif not got:
                status.append("No trust checks to firm for this case")
            else:
                if r["fee"] - got["fee"] > TOL:
                    status.append("Atty Fee short" if got["fee"] else "Atty Fee not taken")
                if r["cost"] - got["cost"] > TOL:
                    status.append("Case Exp short" if got["cost"] else "Case Exp not taken")
                if r["postage"] - got["postage"] > TOL:
                    status.append("Postage short" if got["postage"] else "Postage not taken")
                if got["fee"] - r["fee"] > TOL or got["cost"] - r["cost"] > TOL:
                    status.append("Firm took more than the log shows")
            if len(pids) > 1:
                status.append("Name on more than one case; confirm the match")
            results.append(dict(r, pid=pid or "", fee_qb=got["fee"], cost_qb=got["cost"], post_qb=got["postage"],
                                other_qb=got["other"], status="; ".join(status) or "Ties"))

    write(out_path, results)
    from collections import Counter
    c = Counter()
    for r in results:
        for x in r["status"].split("; "):
            c[x] += 1
    print(dict(rows=len(results), **c))


HEAD = ["Log", "Client", "Filevine Project ID", "DSB Date", "Settlement", "Log Atty Fee", "Trust Atty Fee to Firm",
        "Fee Difference", "Log Case Exp", "Trust Case Exp to Firm", "Case Exp Difference", "Log Postage",
        "Trust Postage to Firm", "Postage Difference", "Other Trust $ to Firm", "Attorney", "Case Manager", "Market",
        "Log Notes", "Result"]


def write(out_path, results):
    wb = Workbook()
    ws = wb.active
    ws.title = "Exceptions"
    other = wb.create_sheet("All Log Rows")
    order = lambda r: (r["status"] == "Ties", -(r["fee"] - r["fee_qb"]), r["client"])  # noqa: E731
    for sh, data in ((ws, [r for r in results if r["status"] != "Ties"]), (other, results)):
        sh.append(HEAD)
        style_header(sh, 1, len(HEAD))
        for i, r in enumerate(sorted(data, key=order), start=2):
            sh.append([r["source"], r["client"], r["pid"], r["dsb"], r["settled"], r["fee"], r["fee_qb"], f"=F{i}-G{i}",
                       r["cost"], r["cost_qb"], f"=I{i}-J{i}", r["postage"], r["post_qb"], f"=L{i}-M{i}", r["other_qb"],
                       r["attorney"], r["cm"], r["market"], r["notes"], r["status"]])
            for c in (5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15):
                sh.cell(i, c).number_format = MONEY
            sh.cell(i, 4).number_format = "yyyy-mm-dd"
        if data:
            add_table(sh, sh.title.replace(" ", ""), 1, len(data) + 1, len(HEAD))
        sh.freeze_panes = "C2"
        for c, w in zip("ABCDEFGHIJKLMNOPQRST", [22, 26, 14, 11] + [13] * 11 + [10, 12, 11, 24, 50]):
            sh.column_dimensions[c].width = w
        for row in sh.iter_rows(min_row=2):
            for c in row:
                c.font = Font(name=FONT, size=10)
    wb.calculation.fullCalcOnLoad = True
    wb.save(out_path)


if __name__ == "__main__":
    a = sys.argv[1:]
    main(a[0], a[1], a[2:])
