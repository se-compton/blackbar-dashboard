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
from openpyxl.styles import Alignment, Font

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
    cutoff = date(2000, 1, 1)
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
        cutoff = max(cutoff, ln["date"])
        if FIRM in ln["name"]:
            memo = ln["memo"]
            bucket = "subro" if "subro" in memo.lower() else \
                "afreimb" if re.search(r"(att?n?y\s*fee|af)\s*reimb", memo, re.I) else \
                "fee" if FEE_RE.search(memo) else "cost" if COST_RE.search(memo) else \
                "postage" if "postage" in memo.lower() else "other"
            firm[pid].append((ln["date"], bucket, -ln["amount"]))

    subro_logs = [p for p in logs if "subro" in p.lower()]
    logs = [p for p in logs if p not in subro_logs]
    subro = check_subro(subro_logs, firm)
    # Match each log row to a case, then compare at the case level: one case can appear on the log more
    # than once (BI and Med Pay paid separately, multiple disbursements), while the firm checks are per case.
    rows = []
    for path in logs:
        for r in read_log(path):
            k = key_from_log(r["client"])
            pids = names.get(k, set()) if k else set()
            pid, gap = None, None
            for p in pids:  # the case whose trust activity sits closest to the log's DSB date
                g = min(abs((d - r["dsb"]).days) for d in activity[p]) if r["dsb"] else 0
                if gap is None or g < gap:
                    pid, gap = p, g
            if pid is not None and (gap or 0) > 3 * WINDOW:
                pid = None
            rows.append(dict(r, pid=pid, ambiguous=len(pids) > 1))

    by_case = defaultdict(list)
    for r in rows:
        if r["pid"] is not None and r["dsb"]:
            by_case[r["pid"]].append(r)
    results = []
    for pid, grp in by_case.items():
        lo, hi = min(r["dsb"] for r in grp), max(r["dsb"] for r in grp)
        got = defaultdict(float)
        for d, bucket, amt in firm[pid]:
            if -WINDOW <= (d - lo).days and (d - hi).days <= 3 * WINDOW:
                got[bucket] += amt
        # A disbursement is often logged again when finalized (sometimes in the next year's log), but a case can
        # also have two real disbursements for the same amount (two policies). Compare with whichever reading
        # (every row, or repeats dropped) is closer to what trust actually paid the firm.
        uniq = list({(round(r["settled"], 2), round(r["fee"], 2)): r for r in grp}.values())
        want = {}
        for k in ("fee", "cost", "postage", "settled"):
            a, b = sum(r[k] for r in grp), sum(r[k] for r in uniq)
            want[k] = a if k == "settled" or abs(a - got[k]) <= abs(b - got[k]) else b
        status = []
        paid = any(v > 0 for v in got.values())
        if not paid and lo > cutoff:
            status.append("Disbursed after the QuickBooks data cutoff")
        elif not paid:
            status.append("No trust checks to firm for this case")
        else:
            unlabeled = got["other"] + max(0.0, got["cost"] - want["cost"])  # e.g. a fee check memo'd PI Settlement or Case Exp
            if want["fee"] - got["fee"] > TOL and want["fee"] - got["fee"] - unlabeled <= TOL:
                status.append("Atty Fee paid under another memo (recode)")
            elif want["fee"] - got["fee"] > TOL:
                status.append("Atty Fee short" if got["fee"] else "Atty Fee not taken")
            if want["cost"] - got["cost"] > TOL:
                status.append("Case Exp short" if got["cost"] else "Case Exp not taken")
            if want["postage"] - got["postage"] > TOL:
                status.append("Postage short" if got["postage"] else "Postage not taken")
            if got["fee"] - want["fee"] > TOL or got["cost"] - want["cost"] > TOL:
                status.append("Firm took more than the log shows")
        if status and any(r["ambiguous"] for r in grp):  # amounts that tie confirm the match
            status.append("Name on more than one case; confirm the match")
        results.append(dict(source=", ".join(sorted({r["source"][:4] for r in grp})), client=grp[0]["client"],
                            pid=pid, dsb=hi, settled=want["settled"], fee=want["fee"], cost=want["cost"],
                            postage=want["postage"], fee_qb=got["fee"], cost_qb=got["cost"], post_qb=got["postage"],
                            other_qb=got["other"], attorney=grp[0]["attorney"], cm=grp[0]["cm"], market=grp[0]["market"],
                            notes="; ".join(str(r["notes"]) for r in grp if r["notes"]) + (f" ({len(grp)} log rows)" if len(grp) > 1 else ""),
                            status="; ".join(status) or "Ties"))
    for r in rows:
        if r["pid"] is None:
            results.append(dict(r, source=r["source"][:4], pid="", fee_qb=0.0, cost_qb=0.0, post_qb=0.0, other_qb=0.0,
                                status="Disbursed after the QuickBooks data cutoff" if r["dsb"] and r["dsb"] > cutoff
                                else "Client not found in trust near the DSB date"))

    write(out_path, results, subro)
    from collections import Counter
    c = Counter()
    for r in results:
        for x in r["status"].split("; "):
            c[x] += 1
    print(dict(cases=len(results), **c))
    print("subro", dict(Counter(r["status"] for r in subro)), round(sum(r["logged"] - r["got"] for r in subro if r["logged"] > r["got"]), 2))


HEAD = ["Log", "Client", "Filevine Project ID", "DSB Date", "Settlement", "Log Atty Fee", "Trust Atty Fee to Firm",
        "Fee Difference", "Log Case Exp", "Trust Case Exp to Firm", "Case Exp Difference", "Log Postage",
        "Trust Postage to Firm", "Postage Difference", "Other Trust $ to Firm", "Attorney", "Case Manager", "Market",
        "Log Notes", "Result"]


def read_subro(path):
    """Monthly tabs: left block = subrogation reductions, right block = attorney-fee reimbursements.
    Both carry the Filevine case # and the 'Firm $' coming back to the firm."""
    out = []
    wb = load_workbook(path, read_only=True, data_only=True)
    for ws in wb.worksheets:
        if ws.title == "Summary":
            continue
        rows = list(ws.iter_rows(values_only=True))
        hi = next((i for i, r in enumerate(rows[:6]) if r and r[0] == "Client"), None)
        if hi is None:
            continue
        hdr = rows[hi]
        starts = [i for i, h in enumerate(hdr) if h == "Client"]
        for blk, st in enumerate(starts):
            col = {h: st + j for j, h in enumerate(hdr[st:st + 15]) if h}
            for r in rows[hi + 1:]:
                cid, amt = r[col["Case #"]] if col.get("Case #", 99) < len(r) else None, r[col["Firm $"]] if col.get("Firm $", 99) < len(r) else None
                try:
                    cid = int(float(cid))
                except (TypeError, ValueError):
                    continue
                if not 8_000_000 <= cid < 16_000_000:  # skip totals and row counters
                    continue
                if isinstance(amt, (int, float)) and amt > 0.005:
                    out.append(dict(source=path.split("/")[-1], month=ws.title, client=r[col["Client"]], pid=cid,
                                    kind="Subro reduction" if blk == 0 else "Atty fee reimbursement", firm=float(amt)))
    return out


def check_subro(paths, firm):
    """Per case: firm $ on the subro logs vs trust 'Subro' / 'Atty Fee Reimbursement' checks to the firm."""
    logged = defaultdict(lambda: dict(firm=0.0, rows=[]))
    for p in paths:
        for r in read_subro(p):
            logged[r["pid"]]["firm"] += r["firm"]
            logged[r["pid"]]["rows"].append(r)
    out = []
    for pid, v in logged.items():
        got = sum(a for _, b, a in firm.get(pid, []) if b in ("subro", "afreimb"))
        first = v["rows"][0]
        diff = v["firm"] - got
        status = "Ties" if abs(diff) <= TOL else ("Firm $ not taken from trust" if got == 0 else
                                                  "Firm took less than the subro log" if diff > 0 else "Firm took more than the subro log")
        out.append(dict(pid=pid, client=first["client"], months=", ".join(sorted({f"{r['source'][:4]} {r['month']}" for r in v["rows"]})),
                        kinds=", ".join(sorted({r["kind"] for r in v["rows"]})), logged=v["firm"], got=got, status=status))
    return out


def write(out_path, results, subro=()):
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
    sm = wb.create_sheet("Summary", 0)
    sm["A1"] = "Disbursement Log vs Trust: did the firm get what each closing statement says?"
    sm["A1"].font = Font(name=FONT, bold=True, size=14)
    sm.append([])
    sm.append(["Result", "Cases", "Log Amount", "Trust Checks to Firm", "Under-collected", "What to do"])
    style_header(sm, 3, 6)
    n = len(results) + 1
    checks = [
        ("Atty Fee not taken", "F", "G", "No fee check from trust for a case the log shows a fee on. Pull the trust ledger card."),
        ("Atty Fee paid under another memo", "F", "O", "Fee went to the firm but the trust check memo is not Atty Fee (e.g. PI Settlement). Recode."),
        ("Atty Fee short", "F", "G", "Fee check smaller than the log. Confirm against the closing statement (fee reduction?)."),
        ("Case Exp not taken", "I", "J", "Log shows case expenses but no Case Exp check from trust."),
        ("Case Exp short", "I", "J", "Case Exp check smaller than the log."),
        ("Postage not taken", "L", "M", "Log shows postage but no postage check from trust."),
        ("Postage short", "L", "M", "Postage check smaller than the log."),
        ("No trust checks to firm", "F", "G", "Client found in trust but no checks to the firm near the disbursement."),
        ("Client not found in trust", "E", "G", "Log name not found in the trust ledger near the DSB date (name spelling, or paid outside trust)."),
        ("after the QuickBooks data cutoff", "F", "G", "DSB date is after the QuickBooks export ends; recheck with a newer export."),
        ("Firm took more than the log", "F", "G", "Trust paid the firm more than the log shows. Usually a log entry error; confirm."),
    ]
    for i, (label, a, b, note) in enumerate(checks, start=4):
        crit = f'Exceptions!$T$2:$T${n},"*{label}*"'
        sm.append([label, f"=COUNTIFS({crit})", f"=SUMIFS(Exceptions!${a}$2:${a}${n},{crit})",
                   f"=SUMIFS(Exceptions!${b}$2:${b}${n},{crit})", f"=MAX(0,C{i}-D{i})", note])
        for c in (3, 4, 5):
            sm.cell(i, c).number_format = MONEY
    r0 = 4 + len(checks) + 1
    sm.cell(r0, 1, "Cases on the logs (matched to trust)")
    sm.cell(r0, 2, f'=COUNTIF(\'All Log Rows\'!$T$2:$T${n},"<>Client not found*")')
    sm.cell(r0 + 1, 1, "Cases that tie exactly")
    sm.cell(r0 + 1, 2, f'=COUNTIF(\'All Log Rows\'!$T$2:$T${n},"Ties")')
    notes = ["Method: each Disbursed List row is matched to its Filevine case by client name in the trust ledger, nearest the DSB date. "
             "Rows for the same case are combined (BI and Med Pay, multiple disbursements; a repeat entry logged again at finalization is dropped when that fits the trust checks better) and compared with the trust checks written to "
             "the firm from 60 days before the first DSB date to 180 days after the last: Atty Fee, Case Exp, Postage. "
             "Subrogation and attorney-fee reimbursements are checked separately on the Subro Firm $ tab by case number."]
    sm.cell(r0 + 3, 1, notes[0]).alignment = Alignment(wrap_text=True, vertical="top")
    sm.merge_cells(start_row=r0 + 3, start_column=1, end_row=r0 + 3, end_column=6)
    sm.row_dimensions[r0 + 3].height = 60
    for c, w in zip("ABCDEF", [34, 9, 16, 18, 16, 90]):
        sm.column_dimensions[c].width = w

    if subro:
        sh = wb.create_sheet("Subro Firm $")
        head = ["Filevine Project ID", "Client", "Log Month(s)", "Type", "Firm $ per Subro Log",
                "Trust Subro / Fee Reimb Checks to Firm", "Difference", "Result", "Filevine Link"]
        sh.append(head)
        style_header(sh, 1, len(head))
        data = sorted(subro, key=lambda r: (r["status"] == "Ties", -(r["logged"] - r["got"])))
        for i, r in enumerate(data, start=2):
            sh.append([r["pid"], r["client"], r["months"], r["kinds"], r["logged"], r["got"], f"=E{i}-F{i}", r["status"],
                       FV_URL.format(r["pid"])])
            for c in (5, 6, 7):
                sh.cell(i, c).number_format = MONEY
        add_table(sh, "SubroFirm", 1, len(data) + 1, len(head))
        sh.freeze_panes = "C2"
        for c, w in zip("ABCDEFGHI", [12, 26, 30, 30, 14, 16, 13, 34, 18]):
            sh.column_dimensions[c].width = w
    wb.calculation.fullCalcOnLoad = True
    wb.save(out_path)


if __name__ == "__main__":
    a = sys.argv[1:]
    main(a[0], a[1], a[2:])
