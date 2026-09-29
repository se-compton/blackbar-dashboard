"""Reconcile register earnings against what should have been paid.

Usage:
    python reconcile.py earnings.csv --expected expected.csv [--code BONUS]
    python reconcile.py earnings.csv --fee-split "Payroll Export August 2026.csv"

expected.csv columns: emp_id,name,expected,basis   (one row per person, for --code)
Fee split export: the Payroll Export CSV from the Market Attorney Fee Split workbook
(Attorney, ..., Net Due); matched to ATTYF by attorney name.
"""
import argparse
import csv
from collections import defaultdict


def load_register(path, code):
    paid, names = defaultdict(float), {}
    for r in csv.DictReader(open(path)):
        names[int(r["emp_id"])] = r["name"]
        if r["code"] == code:
            paid[int(r["emp_id"])] += float(r["amount"])
    return paid, names


def report(lines):
    width = max(len(l[0]) for l in lines) if lines else 10
    print(f"{'Name':<{width}}  {'Expected':>11}  {'Paid':>11}  {'Diff':>10}  Basis")
    for name, exp, paid, basis in lines:
        diff = "" if exp is None else f"{paid - exp:>10,.2f}"
        e = "no support" if exp is None else f"{exp:,.2f}"
        status = "" if exp is not None and abs(paid - exp) < 0.005 else "  <<"
        print(f"{name:<{width}}  {e:>11}  {paid:>11,.2f}  {diff:>10}  {basis}{status}")
    tot_e = sum(l[1] or 0 for l in lines)
    tot_p = sum(l[2] for l in lines)
    print(f"{'TOTAL':<{width}}  {tot_e:>11,.2f}  {tot_p:>11,.2f}  {tot_p - tot_e:>10,.2f}")


def by_expected(reg, expected, code):
    paid, names = load_register(reg, code)
    lines, seen = [], set()
    for r in csv.DictReader(open(expected)):
        i = int(r["emp_id"])
        seen.add(i)
        lines.append((r.get("name") or names.get(i, str(i)), float(r["expected"]), round(paid.get(i, 0), 2), r.get("basis", "")))
    for i, amt in paid.items():
        if i not in seen and amt:
            lines.append((names[i], None, round(amt, 2), f"paid {code}, not in expected file"))
    report(lines)


def by_fee_split(reg, export):
    paid, names = load_register(reg, "ATTYF")
    by_name = {}
    for i, n in names.items():
        last, _, first = n.partition(", ")
        by_name[f"{first.split()[0]} {last}".lower()] = i
    lines, seen = [], set()
    for r in csv.DictReader(open(export)):
        if r["Attorney"].upper() == "TOTAL":
            continue
        i = by_name.get(r["Attorney"].lower())
        seen.add(i)
        lines.append((r["Attorney"], float(r["Net Due"]), round(paid.get(i, 0), 2), "fee split export"))
    for i, amt in paid.items():
        if i not in seen and amt:
            lines.append((names[i], None, round(amt, 2), "paid ATTYF, not in export"))
    report(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("earnings")
    ap.add_argument("--expected")
    ap.add_argument("--code", default="BONUS")
    ap.add_argument("--fee-split")
    args = ap.parse_args()
    if args.expected:
        by_expected(args.earnings, args.expected, args.code)
    if args.fee_split:
        by_fee_split(args.earnings, args.fee_split)


if __name__ == "__main__":
    main()
