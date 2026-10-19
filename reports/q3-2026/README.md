# Q3 2026 report build

`make_q3.py` builds the two leadership PDFs from figures embedded in the script (taken from the QBO Profit and Loss by Quarter export of October 8, 2026 and the October 8 deck):

- `out/Hostilo_Q3-2026_Leadership-Deck.pdf` (8 pages, litigation shown as one department line)
- `out/Hostilo_Q3-2026_Condensed-PL.pdf` (one page, Q1 to Q3 and YTD, subtotals tie to QBO)

Run:

```
pip install reportlab
python3 reports/q3-2026/make_q3.py
```

Conventions it follows are in `CLAUDE.md` and `memory/context/company.md`. Generated PDFs are ignored by git.
