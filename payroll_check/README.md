# Payroll Register Check

Checks a Paylocity Pre-Process Payroll Register against the monthly bonus workbook and the
market attorney fee split export before payroll is submitted.

No employee data lives in this repo. Registers, exports and expected files stay on OneDrive
and in `data/` locally (gitignored).

## Run it

```bash
pip install pypdf
python parse_register.py data/Pre_Process_Payroll_Register.pdf -o data/earnings.csv
python reconcile.py data/earnings.csv --expected data/expected.csv          # BONUS
python reconcile.py data/earnings.csv --fee-split "data/Payroll Export August 2026.csv"  # ATTYF
```

`parse_register.py` exits non-zero if any earning code fails to tie to the register's company
totals page. Rows marked `<<` in the reconcile output need a look.

`expected.csv` is `emp_id,name,expected,basis`, normally the Paylocity Export tab of the
Master Bonus workbook (Employee ID, Amount) plus a name column.

## Where the inputs live (OneDrive, scompton)

| Input | Location |
| --- | --- |
| Bonus workbook + Paylocity import CSV | `2026 Monthly Bonus Workbooks/<Month>/` |
| KPI ratings (1-5) + CM extra incentive | `KPI Scores Master List.xlsx` (Kaleigh's OneDrive), one tab per month |
| Fee split payroll export | `2026 Market Fee Spreadsheets/<Month>/Payroll Export <Month>.csv` |

## Bonus rules (from the Master Bonus workbook)

- Monthly KPI = annual salary / 12 x tier % + extra incentive, prorated for hires inside 90 days.
- Standard: 5 = 15%, 4 = 13%, 3 = 10%, 2 = 5%, 1 = 0.
- Manager (R. Mason, Villanueva, Rossi): 5 = 20%, 4 = 15%.
- Senior Manager (Semco, Woodham): 5 = 25%, 4 = 20%.
- Tonya Lee: fixed 20% / 12 every month.
- Investigators (Rivers, McGahee, Z. Willis, Lovett, J. Mason): flat $500.
- Excluded: Mike Hostilo (owner), Sean Compton (declined). Hodges is quarterly only.
- LIT pool and quarterly management pay only in Jan, Apr, Jul, Oct.
- Market attorney fee split: 10% flat pre-lit, net of draw offset, paid under ATTYF.

## Gotchas

- Use the current Paylocity pay rate, not the roster tab. Several people got raises mid-year
  and the register pays on the new rate.
- A few people are rated off the KPI sheet (Bowers, Johns, Kaplan by their managers; Tonya's
  team). Get those ratings in writing before the run or they show up as "no support".
- The register prints EmpId a line or two below the first earning line; the parser handles it.
- The company totals page has no rate column. Don't read its hours as dollars.
