"""Parse a Paylocity Pre-Process Payroll Register PDF into per-check earnings.

Usage:
    python parse_register.py REGISTER.pdf [-o earnings.csv]

Writes one row per check per earning code (emp_id, name, check_no, code, hours, amount)
and verifies every code's total against the register's company totals page.
"""
import argparse
import csv
import re
import sys
from collections import defaultdict

from pypdf import PdfReader

# Earning code -> description as printed on the register.
EARNINGS = {
    "REG": "Reg", "OT": "OT", "PERS": "PERS", "PERSM": "PERSM", "FLOAT": "FLOAT",
    "HOL": "Holiday", "HOLM": "Holiday M", "PTO": "PTO", "PTOM": "PTO Memo",
    "REMOT": "REMOT", "BONUS": "Bonus", "ATTYF": "ATTYF", "SEVER": "Severence",
    "CLNG": "CLNG", "401ER": "401K", "ERMED": "ER Medical",
}
MONEY = r"([\d,]+\.\d\d)(?!\d)"
PATTERNS = {
    code: re.compile(rf"(?<![A-Z0-9]){code}\s+{re.escape(desc)}\s+(?:(\d+\.\d{{4}})\s+(?:[\d.,]+\s+)?)?{MONEY}")
    for code, desc in EARNINGS.items()
}


def money(s):
    return float(s.replace(",", ""))


def read_lines(path):
    lines = []
    for page in PdfReader(path).pages:
        lines.extend(page.extract_text(extraction_mode="layout").split("\n"))
    return lines


def scan(line):
    for code, pat in PATTERNS.items():
        for m in pat.finditer(line):
            yield code, float(m.group(1)) if m.group(1) else 0.0, money(m.group(2))


def parse(lines):
    rows, company, cur, in_totals = [], defaultdict(float), None, False
    checks = defaultdict(int)
    for i, line in enumerate(lines):
        if line.strip().startswith("Totals for"):
            in_totals, cur = True, None
            continue
        if in_totals:
            for code, _, amt in scan(line):
                company[code] += amt
            continue
        if "Code  Earning  Hours  Rate" in line or re.search(r"Code\s+Earning\s+Hours\s+Rate", line):
            name = re.split(r"\s{2,}", line.strip())[0]
            if name.endswith(","):
                name += " " + re.split(r"\s{2,}", lines[i + 1].strip())[0]
            cur = {"name": name, "emp_id": None}
            continue
        if cur is None:
            continue
        m = re.search(r"EmpId\s+(\d+)", line)
        if m and cur["emp_id"] is None:
            cur["emp_id"] = int(m.group(1))
            checks[cur["emp_id"]] += 1
            cur["check_no"] = checks[cur["emp_id"]]
        for code, hours, amt in scan(line):
            # EmpId can print a line or two below the first earning, so resolve it after the block
            rows.append((cur, {"code": code, "hours": hours, "amount": amt}))
    return [{**check, **line} for check, line in rows], company


def verify(rows, company):
    ok = True
    by_code = defaultdict(float)
    for r in rows:
        by_code[r["code"]] += r["amount"]
    for code, total in sorted(company.items()):
        got = round(by_code.get(code, 0), 2)
        flag = "OK " if abs(got - total) < 0.005 else "OFF"
        ok &= flag == "OK "
        print(f"{flag} {code:<6} register {total:>12,.2f}  parsed {got:>12,.2f}", file=sys.stderr)
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf")
    ap.add_argument("-o", "--out", default="earnings.csv")
    args = ap.parse_args()
    rows, company = parse(read_lines(args.pdf))
    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["emp_id", "name", "check_no", "code", "hours", "amount"])
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} earning lines -> {args.out}", file=sys.stderr)
    sys.exit(0 if verify(rows, company) else 1)


if __name__ == "__main__":
    main()
