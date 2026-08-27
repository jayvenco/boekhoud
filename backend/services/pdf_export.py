from io import BytesIO
from datetime import datetime
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import cm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak

BRAND      = colors.HexColor("#8B6F4E")
BRAND_LITE = colors.HexColor("#F5EDE3")
BG_ALT     = colors.HexColor("#FAF8F5")
TEXT       = colors.HexColor("#524840")
TEXT_LIGHT = colors.HexColor("#9E9187")
SUCCESS    = colors.HexColor("#4A7C59")
DANGER     = colors.HexColor("#B85450")
BORDER     = colors.HexColor("#E2D9CE")
WHITE      = colors.white

_P = lambda text, **kw: Paragraph(text, ParagraphStyle('x', **kw))


def _tbl_style(has_footer=False):
    cmds = [
        ("BACKGROUND",   (0, 0), (-1, 0),  BRAND),
        ("TEXTCOLOR",    (0, 0), (-1, 0),  WHITE),
        ("FONTNAME",     (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",     (0, 0), (-1, 0),  8.5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2 if has_footer else -1), [WHITE, BG_ALT]),
        ("FONTNAME",     (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE",     (0, 1), (-1, -1), 8),
        ("TEXTCOLOR",    (0, 1), (-1, -1), TEXT),
        ("GRID",         (0, 0), (-1, -1), 0.25, BORDER),
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING",   (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 5),
    ]
    if has_footer:
        cmds += [
            ("FONTNAME",   (0, -1), (-1, -1), "Helvetica-Bold"),
            ("BACKGROUND", (0, -1), (-1, -1), BRAND_LITE),
            ("LINEABOVE",  (0, -1), (-1, -1), 1, BRAND),
        ]
    return TableStyle(cmds)


def _summary_style():
    return TableStyle([
        ("BACKGROUND",   (0, 0), (-1, 0),  BG_ALT),
        ("FONTNAME",     (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",     (0, 0), (-1, 0),  7.5),
        ("TEXTCOLOR",    (0, 0), (-1, 0),  TEXT_LIGHT),
        ("FONTNAME",     (0, 1), (-1, 1),  "Helvetica-Bold"),
        ("FONTSIZE",     (0, 1), (-1, 1),  11),
        ("TEXTCOLOR",    (0, 1), (-1, 1),  BRAND),
        ("GRID",         (0, 0), (-1, -1), 0.25, BORDER),
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN",        (0, 0), (-1, -1), "CENTER"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING",   (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 5),
    ])


def _header(story, title, company_name, filters_desc):
    story.append(_P(title, fontName="Helvetica-Bold", fontSize=18,
                    textColor=BRAND, spaceAfter=3))
    if company_name:
        story.append(_P(company_name, fontName="Helvetica", fontSize=10,
                        textColor=TEXT_LIGHT, spaceAfter=2))
    story.append(_P(f"Gegenereerd op {datetime.now().strftime('%d-%m-%Y %H:%M')}",
                    fontName="Helvetica", fontSize=9, textColor=TEXT_LIGHT, spaceAfter=3))
    if filters_desc:
        story.append(_P(f"Filters: {filters_desc}",
                        fontName="Helvetica-Oblique", fontSize=8.5,
                        textColor=TEXT_LIGHT, spaceAfter=4))
    story.append(Spacer(1, 0.35 * cm))


def generate_incomes_pdf(incomes, company_name="", filters_desc="",
                         received_via_labels=None) -> BytesIO:
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4),
                            leftMargin=1.5 * cm, rightMargin=1.5 * cm,
                            topMargin=1.5 * cm, bottomMargin=1.5 * cm)
    story = []
    _header(story, "Inkomsten Overzicht", company_name, filters_desc)

    total  = sum(i.amount for i in incomes)
    paid   = sum(i.amount for i in incomes if i.status == "betaald")
    unpaid = total - paid

    summary = Table(
        [["Totaal inkomsten", "Ontvangen", "Openstaand", "Aantal"],
         [f"€ {total:,.2f}", f"€ {paid:,.2f}", f"€ {unpaid:,.2f}", str(len(incomes))]],
        colWidths=[5 * cm, 5 * cm, 5 * cm, 3 * cm]
    )
    summary.setStyle(_summary_style())
    story.append(summary)
    story.append(Spacer(1, 0.4 * cm))

    LABELS = received_via_labels or {}
    headers = ["Factuurnummer", "Datum", "Categorie", "Omschrijving",
               "Bedrag (€)", "Status", "Ontvangen op"]
    rows = [headers]
    for i in incomes:
        via = (i.received_via_other if i.received_via == "overig" and i.received_via_other
               else LABELS.get(i.received_via, i.received_via or ""))
        rows.append([
            i.invoice_number or "",
            i.date.strftime("%d-%m-%Y"),
            i.category.name if i.category else "—",
            (i.description or "")[:55],
            f"{i.amount:,.2f}",
            "Betaald" if i.status == "betaald" else "Openstaand",
            via,
        ])
    rows.append(["", "", "", "Totaal", f"{total:,.2f}", "", ""])

    tbl = Table(rows, colWidths=[3.5*cm, 2.5*cm, 3.5*cm, 6*cm, 2.5*cm, 2.5*cm, 3.5*cm],
                repeatRows=1)
    tbl.setStyle(_tbl_style(has_footer=True))
    story.append(tbl)

    doc.build(story)
    buf.seek(0)
    return buf


def generate_yearly_pdf(year, inc_by_month, exp_by_month,
                        company_name="", fiscal_year=None) -> BytesIO:
    month_names = ["Januari","Februari","Maart","April","Mei","Juni",
                   "Juli","Augustus","September","Oktober","November","December"]
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4),
                            leftMargin=1.5*cm, rightMargin=1.5*cm,
                            topMargin=1.5*cm, bottomMargin=1.5*cm)
    story = []
    _header(story, f"Jaaroverzicht {year}", company_name, filters_desc="")

    total_inc = sum(inc_by_month.values())
    total_exp = sum(exp_by_month.values())
    balance   = total_inc - total_exp

    summary = Table(
        [["Totale inkomsten", "Totale uitgaven", "Winst / Verlies"],
         [f"€ {total_inc:,.2f}", f"€ {total_exp:,.2f}", f"€ {balance:,.2f}"]],
        colWidths=[6*cm, 6*cm, 6*cm]
    )
    s = _summary_style()
    s.add("TEXTCOLOR", (2, 1), (2, 1), SUCCESS if balance >= 0 else DANGER)
    summary.setStyle(s)
    story.append(summary)
    story.append(Spacer(1, 0.4*cm))

    headers = ["Maand", "Inkomsten (€)", "Uitgaven (€)", "Winst/Verlies (€)", "Cumulatief (€)"]
    rows = [headers]
    cumulative = 0
    for m in range(1, 13):
        inc = inc_by_month.get(m, 0)
        exp = exp_by_month.get(m, 0)
        profit = inc - exp
        cumulative += profit
        rows.append([
            month_names[m - 1],
            f"{inc:,.2f}",
            f"{exp:,.2f}",
            f"{profit:,.2f}",
            f"{cumulative:,.2f}",
        ])
    rows.append(["Totaal", f"{total_inc:,.2f}", f"{total_exp:,.2f}",
                 f"{balance:,.2f}", ""])

    tbl = Table(rows, colWidths=[4*cm, 4.5*cm, 4.5*cm, 4.5*cm, 4.5*cm], repeatRows=1)
    tbl.setStyle(_tbl_style(has_footer=True))
    story.append(tbl)

    if fiscal_year and fiscal_year.status == "afgesloten":
        story.append(Spacer(1, 0.5*cm))
        story.append(_P(
            f"Boekjaar {year} is definitief afgesloten op "
            f"{fiscal_year.closed_at.strftime('%d-%m-%Y') if fiscal_year.closed_at else '—'}.",
            fontName="Helvetica-Oblique", fontSize=8.5, textColor=TEXT_LIGHT
        ))

    doc.build(story)
    buf.seek(0)
    return buf


def generate_expenses_pdf(expenses, company_name="", filters_desc="") -> BytesIO:
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4),
                            leftMargin=1.5 * cm, rightMargin=1.5 * cm,
                            topMargin=1.5 * cm, bottomMargin=1.5 * cm)
    story = []
    _header(story, "Uitgaven Overzicht", company_name, filters_desc)

    total = sum(e.amount for e in expenses if not e.is_depreciable)
    dep   = sum(e.amount for e in expenses if e.is_depreciable)

    summary = Table(
        [["Totale uitgaven", "Afschrijvingen", "Aantal"],
         [f"€ {total:,.2f}", f"€ {dep:,.2f}", str(len(expenses))]],
        colWidths=[5 * cm, 5 * cm, 3 * cm]
    )
    s = _summary_style()
    s.add("TEXTCOLOR", (0, 1), (0, 1), DANGER)
    summary.setStyle(s)
    story.append(summary)
    story.append(Spacer(1, 0.4 * cm))

    headers = ["Factuurnummer", "Datum", "Categorie", "Omschrijving", "Bedrag (€)", "Afschr."]
    rows = [headers]
    for e in expenses:
        rows.append([
            e.invoice_number or "",
            e.date.strftime("%d-%m-%Y"),
            e.category.name if e.category else "—",
            (e.description or "")[:65],
            f"{e.amount:,.2f}",
            "Ja" if e.is_depreciable else "—",
        ])
    rows.append(["", "", "", "Totaal", f"{total:,.2f}", ""])

    tbl = Table(rows, colWidths=[4*cm, 2.5*cm, 3.5*cm, 8.5*cm, 2.5*cm, 2.5*cm],
                repeatRows=1)
    tbl.setStyle(_tbl_style(has_footer=True))
    story.append(tbl)

    doc.build(story)
    buf.seek(0)
    return buf


def generate_full_year_pdf(year, incomes, expenses, inc_by_month, exp_by_month,
                           company_name="", fiscal_year=None,
                           received_via_labels=None) -> BytesIO:
    """Gecombineerde PDF-jaarexport: jaarrapport + volledige inkomsten- en uitgavenlijst."""
    month_names = ["Januari","Februari","Maart","April","Mei","Juni",
                   "Juli","Augustus","September","Oktober","November","December"]
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4),
                            leftMargin=1.5*cm, rightMargin=1.5*cm,
                            topMargin=1.5*cm, bottomMargin=1.5*cm)
    story = []

    # Sectie 1: Jaarrapport
    _header(story, f"Jaarrapport {year}", company_name, filters_desc="")
    total_inc = sum(inc_by_month.values())
    total_exp = sum(exp_by_month.values())
    balance   = total_inc - total_exp

    summary = Table(
        [["Totale inkomsten", "Totale uitgaven", "Winst / Verlies"],
         [f"€ {total_inc:,.2f}", f"€ {total_exp:,.2f}", f"€ {balance:,.2f}"]],
        colWidths=[6*cm, 6*cm, 6*cm]
    )
    s = _summary_style()
    s.add("TEXTCOLOR", (2, 1), (2, 1), SUCCESS if balance >= 0 else DANGER)
    summary.setStyle(s)
    story.append(summary)
    story.append(Spacer(1, 0.4*cm))

    headers = ["Maand", "Inkomsten (€)", "Uitgaven (€)", "Winst/Verlies (€)", "Cumulatief (€)"]
    rows = [headers]
    cumulative = 0
    for m in range(1, 13):
        inc = inc_by_month.get(m, 0)
        exp = exp_by_month.get(m, 0)
        profit = inc - exp
        cumulative += profit
        rows.append([month_names[m - 1], f"{inc:,.2f}", f"{exp:,.2f}",
                     f"{profit:,.2f}", f"{cumulative:,.2f}"])
    rows.append(["Totaal", f"{total_inc:,.2f}", f"{total_exp:,.2f}", f"{balance:,.2f}", ""])

    tbl = Table(rows, colWidths=[4*cm, 4.5*cm, 4.5*cm, 4.5*cm, 4.5*cm], repeatRows=1)
    tbl.setStyle(_tbl_style(has_footer=True))
    story.append(tbl)

    if fiscal_year and fiscal_year.status == "afgesloten":
        story.append(Spacer(1, 0.5*cm))
        story.append(_P(
            f"Boekjaar {year} is definitief afgesloten op "
            f"{fiscal_year.closed_at.strftime('%d-%m-%Y') if fiscal_year.closed_at else '—'}.",
            fontName="Helvetica-Oblique", fontSize=8.5, textColor=TEXT_LIGHT
        ))

    # Sectie 2: Inkomsten
    story.append(PageBreak())
    _header(story, f"Inkomsten {year}", company_name, filters_desc="")
    inc_total = sum(i.amount for i in incomes)
    inc_paid  = sum(i.amount for i in incomes if i.status == "betaald")
    inc_unpaid = inc_total - inc_paid

    summary = Table(
        [["Totaal inkomsten", "Ontvangen", "Openstaand", "Aantal"],
         [f"€ {inc_total:,.2f}", f"€ {inc_paid:,.2f}", f"€ {inc_unpaid:,.2f}", str(len(incomes))]],
        colWidths=[5*cm, 5*cm, 5*cm, 3*cm]
    )
    summary.setStyle(_summary_style())
    story.append(summary)
    story.append(Spacer(1, 0.4*cm))

    LABELS = received_via_labels or {}
    headers = ["Factuurnummer", "Datum", "Categorie", "Omschrijving", "Bedrag (€)", "Status", "Ontvangen op"]
    rows = [headers]
    for i in incomes:
        via = (i.received_via_other if i.received_via == "overig" and i.received_via_other
               else LABELS.get(i.received_via, i.received_via or ""))
        rows.append([
            i.invoice_number or "", i.date.strftime("%d-%m-%Y"),
            i.category.name if i.category else "—", (i.description or "")[:55],
            f"{i.amount:,.2f}", "Betaald" if i.status == "betaald" else "Openstaand", via,
        ])
    rows.append(["", "", "", "Totaal", f"{inc_total:,.2f}", "", ""])

    tbl = Table(rows, colWidths=[3.5*cm, 2.5*cm, 3.5*cm, 6*cm, 2.5*cm, 2.5*cm, 3.5*cm], repeatRows=1)
    tbl.setStyle(_tbl_style(has_footer=True))
    story.append(tbl)

    # Sectie 3: Uitgaven
    story.append(PageBreak())
    _header(story, f"Uitgaven {year}", company_name, filters_desc="")
    exp_total = sum(e.amount for e in expenses if not e.is_depreciable)
    exp_dep   = sum(e.amount for e in expenses if e.is_depreciable)

    summary = Table(
        [["Totale uitgaven", "Afschrijvingen", "Aantal"],
         [f"€ {exp_total:,.2f}", f"€ {exp_dep:,.2f}", str(len(expenses))]],
        colWidths=[5*cm, 5*cm, 3*cm]
    )
    s = _summary_style()
    s.add("TEXTCOLOR", (0, 1), (0, 1), DANGER)
    summary.setStyle(s)
    story.append(summary)
    story.append(Spacer(1, 0.4*cm))

    headers = ["Factuurnummer", "Datum", "Categorie", "Omschrijving", "Bedrag (€)", "Afschr."]
    rows = [headers]
    for e in expenses:
        rows.append([
            e.invoice_number or "", e.date.strftime("%d-%m-%Y"),
            e.category.name if e.category else "—", (e.description or "")[:65],
            f"{e.amount:,.2f}", "Ja" if e.is_depreciable else "—",
        ])
    rows.append(["", "", "", "Totaal", f"{exp_total:,.2f}", ""])

    tbl = Table(rows, colWidths=[4*cm, 2.5*cm, 3.5*cm, 8.5*cm, 2.5*cm, 2.5*cm], repeatRows=1)
    tbl.setStyle(_tbl_style(has_footer=True))
    story.append(tbl)

    doc.build(story)
    buf.seek(0)
    return buf
