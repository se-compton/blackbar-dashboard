from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer,
                                Table, TableStyle, PageBreak, KeepTogether)
from reportlab.lib.enums import TA_LEFT, TA_RIGHT

NAVY = colors.HexColor("#1D2D63")
GRAY = colors.HexColor("#5F6673")
LINE = colors.HexColor("#C8CCD6")
HEAD = colors.HexColor("#EEF0F5")
TILE = colors.HexColor("#F5F6F9")

import os
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out") + os.sep
os.makedirs(OUT, exist_ok=True)

st_title = ParagraphStyle("t", fontName="Helvetica-Bold", fontSize=18, leading=22, textColor=NAVY, spaceAfter=4)
st_sub = ParagraphStyle("s", fontName="Helvetica", fontSize=9, leading=12, textColor=GRAY, spaceAfter=10)
st_body = ParagraphStyle("b", fontName="Helvetica", fontSize=9, leading=12.5, textColor=colors.black)
st_note = ParagraphStyle("n", fontName="Helvetica-Oblique", fontSize=8.5, leading=11.5, textColor=GRAY)
st_h2 = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=10.5, leading=13, textColor=NAVY, spaceBefore=6, spaceAfter=4)
st_kv = ParagraphStyle("kv", fontName="Helvetica-Bold", fontSize=17, leading=20, textColor=NAVY)
st_kl = ParagraphStyle("kl", fontName="Helvetica", fontSize=7, leading=9, textColor=GRAY)
st_cell = ParagraphStyle("c", fontName="Helvetica", fontSize=8.5, leading=10.5)
st_cellb = ParagraphStyle("cb", fontName="Helvetica-Bold", fontSize=8.5, leading=10.5)

def money(x, dec=0):
    neg = x < 0
    s = f"{abs(x):,.{dec}f}"
    return f"({s})" if neg else s

def kpis(items, width):
    n = len(items)
    cells = [[Paragraph(v, st_kv), ] for v, l in items]
    data = [[Paragraph(v, st_kv) for v, l in items], [Paragraph(l.upper(), st_kl) for v, l in items]]
    t = Table(data, colWidths=[width / n] * n, rowHeights=[24, 14])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), TILE),
        ("BOX", (0, 0), (-1, -1), 0.5, LINE),
        ("LINEBEFORE", (1, 0), (-1, -1), 0.5, LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, 0), 5),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 6), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return t

def table(data, widths, right_from=1, bold_rows=(), topline_rows=(), font=8.5, header=True):
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    style = [
        ("FONT", (0, 0), (-1, -1), "Helvetica", font),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.black),
        ("ALIGN", (right_from, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 3.2), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.2),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]
    if header:
        style += [("BACKGROUND", (0, 0), (-1, 0), HEAD), ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", font),
                  ("TEXTCOLOR", (0, 0), (-1, 0), NAVY), ("LINEBELOW", (0, 0), (-1, 0), 0.8, NAVY)]
    for r in bold_rows:
        style.append(("FONT", (0, r), (-1, r), "Helvetica-Bold", font))
    for r in topline_rows:
        style.append(("LINEABOVE", (0, r), (-1, r), 0.8, NAVY))
    t.setStyle(TableStyle(style))
    return t

# ---------------- Deck ----------------
PAGE = landscape(letter)
W, H = PAGE
M = 0.6 * inch
CW = W - 2 * M

def deck_page(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica-Bold", 8); canvas.setFillColor(NAVY)
    canvas.drawString(M, H - 0.42 * inch, "M I K E   H O S T I L O   L A W   F I R M")
    canvas.setFont("Helvetica", 8); canvas.setFillColor(GRAY)
    canvas.drawRightString(W - M, H - 0.42 * inch, "Quarter Ended September 30, 2026")
    canvas.setStrokeColor(LINE); canvas.setLineWidth(0.5)
    canvas.line(M, H - 0.5 * inch, W - M, H - 0.5 * inch)
    canvas.line(M, 0.5 * inch, W - M, 0.5 * inch)
    canvas.setFont("Helvetica", 7.5)
    canvas.drawString(M, 0.34 * inch, "Mike Hostilo Law Firm  |  Q3 2026 Leadership Deck  |  Deposit-date basis, accrual")
    if doc.page > 1:
        canvas.drawRightString(W - M, 0.34 * inch, str(doc.page - 1))
    canvas.restoreState()

doc = BaseDocTemplate(OUT + "Hostilo_Q3-2026_Leadership-Deck.pdf", pagesize=PAGE,
                      leftMargin=M, rightMargin=M, topMargin=0.7 * inch, bottomMargin=0.65 * inch,
                      title="Mike Hostilo Law Firm, Q3 2026 Leadership Deck", author="Sean Compton")
frame = Frame(M, 0.65 * inch, CW, H - 0.7 * inch - 0.65 * inch, id="f", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
doc.addPageTemplates([PageTemplate(id="deck", frames=[frame], onPage=deck_page)])
S = []

def page_head(title, kp):
    S.append(Paragraph(title, st_title))
    S.append(Paragraph("Mike Hostilo Law Firm  |  Quarter Ended September 30, 2026", st_sub))
    S.append(kpis(kp, CW)); S.append(Spacer(1, 12))

# Cover
S.append(Spacer(1, 40))
S.append(Paragraph("THIRD QUARTER 2026", ParagraphStyle("c1", fontName="Helvetica", fontSize=11, leading=14, textColor=GRAY)))
S.append(Paragraph("Quarterly Review", ParagraphStyle("c2", fontName="Helvetica-Bold", fontSize=30, leading=36, textColor=NAVY, spaceAfter=18)))
S.append(kpis([("$7,070,318", "Q3 fee income"), ("769", "Disbursements"), ("$9,194", "Avg fee per case"),
               ("$2,191,388", "Q3 net income"), ("29.8%", "Q3 margin")], CW))
S.append(Spacer(1, 22))
S.append(Paragraph("CONTENTS", ParagraphStyle("c3", fontName="Helvetica-Bold", fontSize=8, textColor=GRAY, spaceAfter=4)))
contents = ["1  Q3 2026 results", "2  Q2 to Q3: where the margin went", "3  Fee income by department",
            "4  Market operations and intake", "5  Q3 2026 attorney production",
            "6  Q3 2026 fee realization by location", "7  Year to date and pacing"]
S.append(Table([[Paragraph(c, st_body)] for c in contents], colWidths=[CW], rowHeights=[14] * len(contents),
               style=TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)])))
S.append(Spacer(1, 18))
S.append(Paragraph("Basis: deposit date. Q1 and Q2 at the July 24 figures of record. Q3 per the QuickBooks export of October 8, 2026, fully accrued; the management bonus is a placeholder pending determination. Litigation is reported as a department.", st_note))
S.append(PageBreak())

# 1 Results
page_head("Q3 2026 results", [("$2,191,388", "Q3 net income"), ("29.8%", "Q3 margin"), ("$7,070,318", "Fee income"), ("$9,194", "Avg fee per case")])
d = [["", "Q1 2026", "Q2 2026", "Q3 2026", "YTD 2026"],
     ["Revenue", "$6,691,932", "$6,275,461", "$7,354,896", "$20,322,290"],
     ["Costs, net of other income", "$5,283,059", "$5,704,211", "$5,163,508", "$16,150,778"],
     ["Net income", "$1,408,873", "$571,250", "$2,191,388", "$4,171,511"],
     ["Margin", "21.1%", "9.1%", "29.8%", "20.5%"],
     ["Disbursements", "771", "854", "769", "2,394"],
     ["Disbursement fee income", "$6,158,722", "$5,787,478", "$7,070,318", "$19,016,518"],
     ["Avg fee per case", "$7,988", "$6,777", "$9,194", "$7,943"]]
S.append(table(d, [CW * 0.36] + [CW * 0.16] * 4, bold_rows=(3,), topline_rows=(3,)))
S.append(Spacer(1, 10))
S.append(Paragraph("Q3 was the best revenue and margin of the year on the fewest disbursements. Average fee carried it, and advertising came down with all direct-buy media loaded.", st_note))
S.append(PageBreak())

# 2 Bridge
page_head("Q2 to Q3: where the margin went", [("$571,250", "Q2 net income"), ("$2,191,388", "Q3 net income"), ("$1,620,138", "Change"), ("9.1% to 29.8%", "Margin")])
d = [["Driver", "Favorable / (unfavorable)", "Running net income"],
     ["Q2 2026 net income, as reported", "", "$571,250"],
     ["Q2 post-close entries, not restated", "$58,568", "$629,818"],
     ["Revenue", "$1,079,434", "$1,709,252"],
     ["Advertising", "$774,927", "$2,484,179"],
     ["Attorney fee split", "($1,770)", "$2,482,409"],
     ["Management bonus", "($161,893)", "$2,320,515"],
     ["Staff bonus", "$16,729", "$2,337,245"],
     ["All other costs", "($155,163)", "$2,182,082"],
     ["Other income", "$9,306", "$2,191,388"],
     ["Q3 2026 net income", "", "$2,191,388"]]
S.append(table(d, [CW * 0.5, CW * 0.25, CW * 0.25], bold_rows=(1, 10), topline_rows=(10,)))
S.append(Spacer(1, 10))
S.append(Paragraph("The quarter turned on advertising, down $774,927 with all direct-buy media loaded, and on revenue, up $1,079,434.", st_note))
S.append(PageBreak())

# 3 Department
page_head("Fee income by department", [("$6,029,768", "Pre-lit Q3 fee income"), ("698", "Pre-lit Q3 cases"), ("$1,040,550", "Litigation Q3 fee income"), ("$14,656", "Lit avg fee per case")])
d = [["", "Pre-litigation", "Litigation", "Firm"],
     ["Cases, Q2", "790", "64", "854"],
     ["Cases, Q3", "698", "71", "769"],
     ["Fee income, Q2", "$4,799,260", "$988,218", "$5,787,478"],
     ["Fee income, Q3", "$6,029,768", "$1,040,550", "$7,070,318"],
     ["Change, Q2 to Q3", "$1,230,508", "$52,332", "$1,282,840"],
     ["Avg fee per case, Q2", "$6,075", "$15,441", "$6,777"],
     ["Avg fee per case, Q3", "$8,639", "$14,656", "$9,194"],
     ["Share of firm fees, Q3", "85.3%", "14.7%", "100.0%"],
     ["Fee split incentive, Q3", "$451,040", "$154,441", "$605,481"]]
S.append(table(d, [CW * 0.4] + [CW * 0.2] * 3, bold_rows=(5,), topline_rows=(5,)))
S.append(Spacer(1, 10))
S.append(Paragraph("Both departments grew fee income over Q2. Pre-lit added $1,230,508 on average fee, $6,075 to $8,639; litigation added $52,332 on 7 more cases.", st_note))
S.append(PageBreak())

# 4 Market ops
page_head("Market operations and intake", [("639", "Gross signups"), ("504", "Net signups"), ("21.1%", "Rejection rate"), ("$3,807", "Advertising per net signup")])
d = [["Market", "Gross signups", "Rejections", "Rejection rate", "Net signups", "Q2 net", "Disbursements", "Fee income", "Share of fees"],
     ["Savannah", "279", "52", "18.6%", "227", "292", "303", "$2,423,125", "34.3%"],
     ["Augusta", "152", "22", "14.5%", "130", "209", "232", "$2,306,404", "32.6%"],
     ["Macon", "163", "55", "33.7%", "108", "143", "168", "$1,788,345", "25.3%"],
     ["Columbus", "45", "6", "13.3%", "39", "43", "66", "$552,445", "7.8%"],
     ["Total", "639", "135", "21.1%", "504", "687", "769", "$7,070,318", "100.0%"]]
S.append(table(d, [CW * 0.16] + [CW * 0.105] * 8, bold_rows=(5,), topline_rows=(5,)))
S.append(Spacer(1, 12))
S.append(Paragraph("Fee income by month", st_h2))
d = [["", "July", "August", "September", "Q3"],
     ["Savannah", "$779,609", "$789,807", "$570,212", "$2,139,628"],
     ["Macon", "$405,915", "$238,053", "$967,792", "$1,611,760"],
     ["Augusta", "$413,981", "$353,952", "$532,574", "$1,300,507"],
     ["Aiken", "$437,884", "$157,721", "$410,291", "$1,005,897"],
     ["Columbus", "$132,794", "$77,400", "$92,494", "$302,688"],
     ["Other markets (4)", "$301,343", "$284,647", "$123,849", "$709,839"],
     ["All markets", "$2,471,525", "$1,901,580", "$2,697,212", "$7,070,318"]]
S.append(table(d, [CW * 0.28] + [CW * 0.18] * 4, bold_rows=(7,), topline_rows=(7,), font=8))
S.append(PageBreak())

# 5 Attorney production (litigation as a unit)
page_head("Q3 2026 attorney production", [("$6,029,768", "Pre-lit fee income"), ("698", "Pre-lit cases"), ("$8,639", "Pre-lit avg fee per case"), ("85.3%", "Pre-lit share of firm fees")])
S.append(Paragraph("Pre-lit attorneys", st_h2))
d = [["Attorney", "Cases", "Disbursement fee income", "Avg fee per case", "Share of pre-lit fees", "Fee split, net of draws"],
     ["Ryan Butz", "176", "$1,564,012", "$8,886", "25.9%", "$124,159"],
     ["Patrick Fraser", "118", "$1,278,038", "$10,831", "21.2%", "$95,762"],
     ["Kevin Judicki", "174", "$1,091,165", "$6,271", "18.1%", "$77,064"],
     ["Derek Poarch", "114", "$1,084,515", "$9,513", "18.0%", "$76,590"],
     ["Taylor Cocke", "82", "$666,678", "$8,130", "11.1%", "$44,775"],
     ["Brandi Allday", "31", "$326,678", "$10,538", "5.4%", "$32,691"],
     ["Departed (3)", "3", "$18,681", "$6,227", "0.3%", "$0"],
     ["Total", "698", "$6,029,768", "$8,639", "100.0%", "$451,040"]]
S.append(table(d, [CW * 0.25] + [CW * 0.15] * 5, bold_rows=(8,), topline_rows=(8,)))
S.append(Spacer(1, 12))
S.append(Paragraph("Litigation, as a department", st_h2))
d = [["Department", "Cases", "Disbursement fee income", "Avg fee per case", "Share of firm fees", "Litigation pool"],
     ["Litigation", "71", "$1,040,550", "$14,656", "14.7%", "$154,441"]]
S.append(table(d, [CW * 0.25] + [CW * 0.15] * 5, bold_rows=()))
S.append(Spacer(1, 10))
S.append(Paragraph("Litigation is reported as a unit. The pool is the Director of Litigation's allocation on the quarter's litigated cases at the team rate over the quarterly threshold.", st_note))
S.append(PageBreak())

# 6 Realization
page_head("Q3 2026 fee realization by location", [("$7,070,318", "Attorney fees"), ("769", "Cases"), ("$9,194", "Avg fee per case"), ("32.7%", "Realization")])
d = [["Market", "Settlements", "Atty fees", "Postage", "Case exp", "# of DSB", "Avg fee / case", "Fee %"],
     ["Savannah", "$6,515,087.05", "$2,139,628.16", "$8,998.27", "$60,416.92", "249", "$8,592.88", "32.8%"],
     ["Macon", "$4,951,059.85", "$1,611,759.88", "$5,402.86", "$32,598.41", "147", "$10,964.35", "32.6%"],
     ["Augusta", "$3,900,803.89", "$1,300,507.13", "$4,706.66", "$30,760.80", "164", "$7,929.92", "33.3%"],
     ["Aiken", "$3,042,916.32", "$1,005,896.56", "$2,275.60", "$10,217.82", "68", "$14,792.60", "33.1%"],
     ["Columbus", "$976,818.33", "$302,687.58", "$1,715.32", "$7,500.16", "47", "$6,440.16", "31.0%"],
     ["Alabama", "$772,384.00", "$249,757.08", "$562.80", "$4,225.13", "19", "$13,145.11", "32.3%"],
     ["Beaufort", "$751,965.22", "$237,091.84", "$1,086.60", "$7,622.57", "44", "$5,388.45", "31.5%"],
     ["Atlanta", "$551,602.46", "$176,584.97", "$774.73", "$4,037.64", "21", "$8,408.81", "32.0%"],
     ["Charleston", "$141,171.48", "$46,404.74", "$224.02", "$1,998.66", "10", "$4,640.47", "32.9%"],
     ["Pre-lit subtotal", "$18,391,699.23", "$6,029,768.40", "$22,763.54", "$129,310.48", "698", "$8,638.64", "32.8%"],
     ["Litigation", "$3,212,109.37", "$1,040,549.54", "$2,983.32", "$30,067.63", "71", "$14,655.63", "32.4%"],
     ["Total", "$21,603,808.60", "$7,070,317.94", "$25,746.86", "$159,378.11", "769", "$9,194.17", "32.7%"]]
S.append(table(d, [CW * 0.16, CW * 0.15, CW * 0.14, CW * 0.11, CW * 0.11, CW * 0.09, CW * 0.13, CW * 0.11],
               bold_rows=(10, 12), topline_rows=(10, 12), font=8))
S.append(PageBreak())

# 7 YTD and pacing
page_head("Year to date and pacing", [("$19,016,518", "YTD fee income"), ("2,394", "YTD cases"), ("$4,171,511", "YTD net income"), ("$8,483,482", "Q4 needed for $27.5M")])
d = [["", "Q1 2026", "Q2 2026", "Q3 2026", "YTD 2026"],
     ["Cases", "771", "854", "769", "2,394"],
     ["Fee income", "$6,158,722", "$5,787,478", "$7,070,318", "$19,016,518"],
     ["Avg fee per case", "$7,988", "$6,777", "$9,194", "$7,943"],
     ["Pre-lit fee income", "$4,467,839", "$4,799,260", "$6,029,768", "$15,296,868"],
     ["Litigation fee income", "$1,690,882", "$988,218", "$1,040,550", "$3,719,650"]]
S.append(table(d, [CW * 0.36] + [CW * 0.16] * 4))
S.append(Spacer(1, 12))
S.append(Paragraph("Q4 scenarios", st_h2))
d = [["Q4 scenario", "Q4 fee income", "Full year", "Against $27.5M"],
     ["Q3 pace held", "$7,070,318", "$26,086,836", "($1,413,164)"],
     ["H1 quarterly average", "$5,973,100", "$24,989,618", "($2,510,382)"],
     ["Plan", "$8,483,482", "$27,500,000", "$0"]]
S.append(table(d, [CW * 0.4] + [CW * 0.2] * 3, bold_rows=(3,), topline_rows=(3,)))
S.append(Spacer(1, 10))
S.append(Paragraph("Q4 needs $8,483,482 to reach the $27.5M plan. Holding the Q3 pace lands the year at $26,086,836. Another $360,596 sits on 59 tracker rows awaiting deposit dates.", st_note))
doc.build(S)

# ---------------- Condensed P&L ----------------
q = {  # Q1, Q2, Q3 from QBO P&L by Quarter, Jan-Sep 2026, accrual, exported 2026-10-08
 "disb": (6299088.59, 5803498.74, 7083508.52),
 "ref": (378059.46, 461412.51, 266065.88),
 "reimb": (14784.41, 10550.15, 16.15 + 5305.18),
 "adv": (2156011.89, 2693508.29, 1918581.28),
 "sal": (1183489.50, 1151783.48, 1387710.68),
 "split": (676951.08, 603710.84, 605481.02),
 "mgmt": (93324.00, 57245.55, 219138.81),
 "staffb": (180984.64, 131579.97, 114850.93),
 "ptax": (159647.56, 124753.00, 104835.75),
 "rent": (122198.64, 95874.96, 79120.20),
 "otherdir": (7474.31 + 18912.82 + 754.26 + 0 + 0 + 20962.81 + 13067.03,
              7278.57 + 12021.78 + 14338.09 + 37890.00 + 19552.04 + 27486.13 + 13571.24,
              9037.26 + 12788.32 + 7959.53 + 0 + 158.77 + 47206.14 + 14688.21),
 "benefits": (10745.79 + 3320.01 + 0 + 191487.62 + 17558.30 + 1908.37 + 49799.07 + 502.43,
              11820.04 + 0 + 160.00 + 195745.74 + 11889.45 + 721.31 + 52297.07 + 0,
              12605.14 + 1812.07 + 0 + 172751.00 + 15259.48 + 453.99 + 43526.44 + 0),
 "comp": (76434.68, 132439.83, 134659.36),
 "case": (110983.26, 123539.65, 92604.67),
 "contract": (73820.79, 61742.21, 67694.72),
 "ins": (56556.72, 45046.58, 57033.89),
 "prof": (35652.00, 1040.00, 23715.00),
 "otherind": (33.88 + 892.00 + 0 + 0 + 1735.01 + 5693.67 + 0 + 11181.30 + 2632.00,
              0 + 6339.83 + 2006.37 + 1206.28 + 2757.00 + 8449.14 + 49.00 + 5925.81 + 0,
              0 + 7372.20 + 5069.33 + 1294.89 + 3434.55 + 8946.70 + 44.99 + 7547.23 + 91.00),
 "unclass": (0.0, 0.0, 3450.18 + 15.57),
 "othinc": (10589.60, 8125.41, 17431.72),
}
def s3(k): return q[k]
def add(*ks): return tuple(sum(q[k][i] for k in ks) for i in range(3))
inc = add("disb", "ref", "reimb")
compt = add("sal", "split", "mgmt", "staffb")
direct = add("adv", "sal", "split", "mgmt", "staffb", "ptax", "rent", "otherdir")
indirect = add("benefits", "comp", "case", "contract", "ins", "prof", "otherind")
expenses = tuple(direct[i] + indirect[i] + q["unclass"][i] for i in range(3))
noi = tuple(inc[i] - expenses[i] for i in range(3))
ni = tuple(noi[i] + q["othinc"][i] for i in range(3))
# ties to the QuickBooks export
for got, exp in [(inc, (6691932.46, 6275461.40, 7354895.73)), (direct, (4633778.54, 4990593.94, 4521556.90)),
                 (indirect, (650936.90, 663175.31, 655916.65)), (expenses, (5284715.44, 5653769.25, 5180939.30)),
                 (noi, (1407217.02, 621692.15, 2173956.43)), (ni, (1417806.62, 629817.56, 2191388.15)),
                 (compt, (2134749.22, 1944319.84, 2327181.44))]:
    for g, e in zip(got, exp):
        assert abs(g - e) < 0.02, (g, e)
print("P&L ties to QBO totals")

def row(label, vals, bold=False, pct=True):
    ytd = sum(vals)
    cells = [label] + [("$" + money(v)) if v >= 0 else "($" + money(-v) + ")" for v in vals] + [("$" + money(ytd)) if ytd >= 0 else "($" + money(-ytd) + ")"]
    cells.append(f"{ytd / sum(inc) * 100:.1f}%" if pct else "")
    return cells, bold
rows = []
def sec(name): rows.append(([name, "", "", "", "", ""], "sec"))
sec("Income")
rows.append(row("Disbursement income", s3("disb")))
rows.append(row("Referral fee income", s3("ref")))
rows.append(row("Subrogation and other reimbursements", s3("reimb")))
rows.append(row("Total income", inc, bold=True))
sec("Direct costs")
rows.append(row("Advertising and promotion", s3("adv")))
rows.append(row("Salaries and wages", s3("sal")))
rows.append(row("Attorney fee splits and litigation pool (1)", s3("split")))
rows.append(row("Management bonuses (2)", s3("mgmt")))
rows.append(row("Staff bonuses", s3("staffb")))
rows.append(row("Total compensation", compt, bold=True))
rows.append(row("Payroll taxes", s3("ptax")))
rows.append(row("Rent", s3("rent")))
rows.append(row("Other direct (auto, outside services, repairs, property tax, uncollectable case expense, utilities)", s3("otherdir")))
rows.append(row("Total direct costs", direct, bold=True))
sec("Indirect costs")
rows.append(row("Employee benefits and related (health, disability, life, retirement, payroll processing, recruiting)", s3("benefits")))
rows.append(row("Computer, internet and software", s3("comp")))
rows.append(row("Case support (phones, supplies, postage, printing, research, travel)", s3("case")))
rows.append(row("Contract labor", s3("contract")))
rows.append(row("Insurance", s3("ins")))
rows.append(row("Professional fees", s3("prof")))
rows.append(row("Other indirect (licenses, dues, bank and interest, meals, charitable, small capital purchases)", s3("otherind")))
rows.append(row("Total indirect costs", indirect, bold=True))
rows.append(row("Unclassified (Ask My Accountant)", s3("unclass")))
rows.append(row("Total expenses", expenses, bold=True))
rows.append(row("Net operating income", noi, bold=True))
rows.append(row("Other income, net (rent, card rewards, refunds, less penalties)", s3("othinc")))
rows.append(row("Net income", ni, bold=True))
margins = ["Net margin"] + [f"{ni[i] / inc[i] * 100:.1f}%" for i in range(3)] + [f"{sum(ni) / sum(inc) * 100:.1f}%", ""]

PP = letter; PW, PH = PP; PM = 0.6 * inch; PCW = PW - 2 * PM
lab = ParagraphStyle("lab", fontName="Helvetica", fontSize=8, leading=9.5)
labb = ParagraphStyle("labb", fontName="Helvetica-Bold", fontSize=8, leading=9.5)
labs = ParagraphStyle("labs", fontName="Helvetica-Bold", fontSize=8, leading=9.5, textColor=NAVY)
data = [["", "Q1 2026", "Q2 2026", "Q3 2026", "YTD 2026", "YTD % rev"]]
style = [("FONT", (0, 0), (-1, -1), "Helvetica", 8), ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
         ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("BACKGROUND", (0, 0), (-1, 0), HEAD),
         ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8), ("TEXTCOLOR", (0, 0), (-1, 0), NAVY),
         ("LINEBELOW", (0, 0), (-1, 0), 0.8, NAVY), ("TOPPADDING", (0, 0), (-1, -1), 2.3),
         ("BOTTOMPADDING", (0, 0), (-1, -1), 2.3), ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5)]
for cells, kind in rows:
    r = len(data)
    if kind == "sec":
        data.append([Paragraph(cells[0], labs)] + [""] * 5)
        style += [("LINEBELOW", (0, r), (-1, r), 0.4, LINE), ("TOPPADDING", (0, r), (-1, r), 5)]
    else:
        data.append([Paragraph(cells[0], labb if kind else lab)] + cells[1:])
        if kind:
            style += [("FONT", (1, r), (-1, r), "Helvetica-Bold", 8), ("LINEABOVE", (0, r), (-1, r), 0.6, NAVY)]
        else:
            style += [("LINEBELOW", (0, r), (-1, r), 0.3, LINE)]
r = len(data); data.append([Paragraph(margins[0], labb)] + margins[1:])
style += [("FONT", (1, r), (-1, r), "Helvetica-Bold", 8), ("LINEABOVE", (0, r), (-1, r), 0.6, NAVY), ("LINEBELOW", (0, r), (-1, r), 1.2, NAVY)]
t = Table(data, colWidths=[PCW * 0.40] + [PCW * 0.125] * 4 + [PCW * 0.10], repeatRows=1)
t.setStyle(TableStyle(style))

def pl_page(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(LINE); canvas.setLineWidth(0.5); canvas.line(PM, 0.55 * inch, PW - PM, 0.55 * inch)
    canvas.setFont("Helvetica", 7.5); canvas.setFillColor(GRAY)
    canvas.drawString(PM, 0.38 * inch, "Mike Hostilo Law Firm  |  Condensed Profit and Loss  |  Accrual basis")
    canvas.drawRightString(PW - PM, 0.38 * inch, "Source: QuickBooks P&L by quarter, exported October 8, 2026")
    canvas.setFont("Helvetica-Bold", 8); canvas.setFillColor(NAVY)
    canvas.drawString(PM, PH - 0.42 * inch, "M I K E   H O S T I L O   L A W   F I R M")
    canvas.setStrokeColor(LINE); canvas.line(PM, PH - 0.5 * inch, PW - PM, PH - 0.5 * inch)
    canvas.restoreState()
pdoc = BaseDocTemplate(OUT + "Hostilo_Q3-2026_Condensed-PL.pdf", pagesize=PP, leftMargin=PM, rightMargin=PM,
                       topMargin=0.7 * inch, bottomMargin=0.7 * inch,
                       title="Mike Hostilo Law Firm, Condensed Profit and Loss, Q1 to Q3 2026", author="Sean Compton")
pf = Frame(PM, 0.7 * inch, PCW, PH - 0.7 * inch - 0.7 * inch, id="p", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
pdoc.addPageTemplates([PageTemplate(id="pl", frames=[pf], onPage=pl_page)])
P = [Paragraph("Condensed Profit and Loss", ParagraphStyle("p1", fontName="Helvetica-Bold", fontSize=17, leading=20, textColor=NAVY, spaceAfter=2)),
     Paragraph("January through September 2026, by quarter  |  Accrual basis  |  As the books stand on October 8, 2026", st_sub),
     t, Spacer(1, 8),
     Paragraph("(1) Includes the Q3 litigation pool of $154,441 and September market attorney splits of $162,074, accrued at 9/30 and paid with the October cycle.", st_note),
     Paragraph("(2) The Q3 management bonus of $219,139 is a placeholder at five percent of net income for each of the two principals, pending determination.", st_note),
     Paragraph("Q1 and Q2 are as the books stand today. Q1 was reported at $1,408,873 and Q2 at $571,250; Q2 now carries $629,818 after the July true-up of the June estimates and is not restated. Year-to-date net income of $4,239,012 ties to the balance sheet.", st_note)]
pdoc.build(P)
print("done")
