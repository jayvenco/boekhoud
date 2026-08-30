from io import BytesIO
from datetime import datetime
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import cm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.barcharts import VerticalBarChart, HorizontalBarChart
from reportlab.graphics.charts.legends import Legend
from backend.services.expense_filters import (
    is_afschrijving_expense as _is_afschrijving,
    is_afschrijving_installment as _is_afschrijving_installment,
    is_huisvesting_expense as _is_huisvesting,
)

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

_CELL_STYLE = ParagraphStyle('cell', fontName='Helvetica', fontSize=8, leading=10, textColor=TEXT)


def _wrap_cell(text, max_len=500):
    """Tekst voor een tabelcel die binnen de kolombreedte moet afbreken (bijv.
    een omschrijving) in plaats van over te lopen in de volgende kolom —
    reportlab breekt alleen af binnen Paragraph-objecten, niet bij platte
    strings in een Table."""
    text = (text or "")[:max_len]
    return Paragraph(escape(text), _CELL_STYLE)


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
    story.append(_P(title, fontName="Helvetica-Bold", fontSize=18, leading=22,
                    textColor=BRAND, spaceAfter=3))
    if company_name:
        story.append(_P(company_name, fontName="Helvetica", fontSize=10, leading=13,
                        textColor=TEXT_LIGHT, spaceAfter=2))
    story.append(_P(f"Gegenereerd op {datetime.now().strftime('%d-%m-%Y %H:%M')}",
                    fontName="Helvetica", fontSize=9, leading=12, textColor=TEXT_LIGHT, spaceAfter=3))
    if filters_desc:
        story.append(_P(f"Filters: {filters_desc}",
                        fontName="Helvetica-Oblique", fontSize=8.5, leading=11,
                        textColor=TEXT_LIGHT, spaceAfter=4))
    story.append(Spacer(1, 0.35 * cm))


def _monthly_chart(inc_by_month, exp_by_month, month_names) -> Drawing:
    """Gegroepeerde staafdiagram inkomsten vs. uitgaven per maand."""
    inc_data = [inc_by_month.get(m, 0) for m in range(1, 13)]
    exp_data = [exp_by_month.get(m, 0) for m in range(1, 13)]
    max_val = max(inc_data + exp_data + [1])

    d = Drawing(680, 190)
    chart = VerticalBarChart()
    chart.x = 35
    chart.y = 25
    chart.width = 560
    chart.height = 140
    chart.data = [inc_data, exp_data]
    chart.categoryAxis.categoryNames = [m[:3] for m in month_names]
    chart.categoryAxis.labels.fontSize = 7
    chart.categoryAxis.labels.fillColor = TEXT_LIGHT
    chart.valueAxis.labels.fontSize = 7
    chart.valueAxis.labels.fillColor = TEXT_LIGHT
    chart.valueAxis.valueMin = 0
    chart.valueAxis.valueMax = max_val * 1.15
    chart.groupSpacing = 6
    chart.barSpacing = 1
    chart.bars[0].fillColor = SUCCESS
    chart.bars[1].fillColor = DANGER
    chart.strokeColor = None
    d.add(chart)

    legend = Legend()
    legend.x = 610
    legend.y = 150
    legend.dx = 8
    legend.dy = 8
    legend.fontSize = 7.5
    legend.alignment = "left"
    legend.colorNamePairs = [(SUCCESS, "Inkomsten"), (DANGER, "Uitgaven")]
    d.add(legend)
    return d


def _category_chart(cat_totals, bar_color, total_width=680, name_len=22) -> Drawing:
    """Horizontale staafdiagram van bedrag per categorie, aflopend gesorteerd."""
    items = sorted(cat_totals, key=lambda x: x[1], reverse=True)[:8]
    if not items:
        return None
    items = list(reversed(items))
    values = [v for _, v in items]
    names = [n[:name_len] for n, _ in items]
    max_val = max(values + [1])

    label_w = min(100, total_width * 0.22)
    chart_w = total_width - label_w - 25
    height = 24 * len(items) + 30
    d = Drawing(total_width, height)
    chart = HorizontalBarChart()
    chart.x = label_w
    chart.y = 15
    chart.width = chart_w
    chart.height = height - 30
    chart.data = [values]
    chart.categoryAxis.categoryNames = names
    chart.categoryAxis.labels.fontSize = 8
    chart.categoryAxis.labels.fillColor = TEXT
    chart.valueAxis.labels.fontSize = 7
    chart.valueAxis.labels.fillColor = TEXT_LIGHT
    chart.valueAxis.valueMin = 0
    chart.valueAxis.valueMax = max_val * 1.15
    chart.bars[0].fillColor = bar_color
    chart.barSpacing = 4
    chart.strokeColor = None
    d.add(chart)
    return d


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

    cat_totals = {}
    for i in incomes:
        key = i.category.name if i.category else "Onbekend"
        cat_totals[key] = cat_totals.get(key, 0) + i.amount
    chart = _category_chart(list(cat_totals.items()), SUCCESS)
    if chart:
        story.append(_P("Inkomsten per categorie", fontName="Helvetica-Bold",
                        fontSize=10, textColor=TEXT, spaceAfter=4))
        story.append(chart)
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
            _wrap_cell(i.description),
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
                        company_name="", fiscal_year=None,
                        total_huisvestingskosten=0, total_afschrijvingen=0, total_km=0) -> BytesIO:
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
    balance   = total_inc - total_exp - total_huisvestingskosten - total_afschrijvingen

    summary = Table(
        [["Totale inkomsten", "Reguliere uitgaven", "Huisvestingskosten", "Afschrijvingen", "Winst / Verlies"],
         [f"€ {total_inc:,.2f}", f"€ {total_exp:,.2f}", f"€ {total_huisvestingskosten:,.2f}",
          f"€ {total_afschrijvingen:,.2f}", f"€ {balance:,.2f}"]],
        colWidths=[5.2*cm] * 5
    )
    s = _summary_style()
    s.add("TEXTCOLOR", (4, 1), (4, 1), SUCCESS if balance >= 0 else DANGER)
    summary.setStyle(s)
    story.append(summary)
    story.append(Spacer(1, 0.25*cm))
    story.append(_P(f"Zakelijke kilometers: {total_km:,.1f} km", fontName="Helvetica",
                    fontSize=9, textColor=TEXT_LIGHT))
    story.append(Spacer(1, 0.3*cm))

    story.append(_P("Inkomsten & uitgaven per maand", fontName="Helvetica-Bold",
                    fontSize=10, textColor=TEXT, spaceAfter=4))
    story.append(_monthly_chart(inc_by_month, exp_by_month, month_names))
    story.append(Spacer(1, 0.4*cm))

    headers = ["Maand", "Inkomsten (€)", "Reguliere uitgaven (€)", "Winst/Verlies (€)", "Cumulatief (€)"]
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
                 f"{total_inc - total_exp:,.2f}", ""])

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

    total = sum(e.amount for e in expenses if not _is_afschrijving(e) and not _is_huisvesting(e))
    dep   = sum(e.amount for e in expenses if _is_afschrijving_installment(e))
    huisvesting = sum(e.amount for e in expenses if _is_huisvesting(e) and not _is_afschrijving(e))

    summary = Table(
        [["Totale uitgaven", "Huisvestingskosten", "Afschrijvingen", "Aantal"],
         [f"€ {total:,.2f}", f"€ {huisvesting:,.2f}", f"€ {dep:,.2f}", str(len(expenses))]],
        colWidths=[5 * cm, 5 * cm, 5 * cm, 3 * cm]
    )
    s = _summary_style()
    s.add("TEXTCOLOR", (0, 1), (0, 1), DANGER)
    summary.setStyle(s)
    story.append(summary)
    story.append(Spacer(1, 0.4 * cm))

    cat_totals = {}
    for e in expenses:
        if _is_afschrijving(e) or _is_huisvesting(e):
            continue
        key = e.category.name if e.category else "Onbekend"
        cat_totals[key] = cat_totals.get(key, 0) + e.amount
    chart = _category_chart(list(cat_totals.items()), DANGER)
    if chart:
        story.append(_P("Uitgaven per categorie", fontName="Helvetica-Bold",
                        fontSize=10, textColor=TEXT, spaceAfter=4))
        story.append(chart)
        story.append(Spacer(1, 0.4 * cm))

    headers = ["Factuurnummer", "Datum", "Categorie", "Omschrijving", "Bedrag (€)", "Afschr."]
    rows = [headers]
    for e in expenses:
        rows.append([
            e.invoice_number or "",
            e.date.strftime("%d-%m-%Y"),
            e.category.name if e.category else "—",
            _wrap_cell(e.description),
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
                           received_via_labels=None,
                           total_huisvestingskosten=0, total_afschrijvingen=0, total_km=0) -> BytesIO:
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
    balance   = total_inc - total_exp - total_huisvestingskosten - total_afschrijvingen

    summary = Table(
        [["Totale inkomsten", "Reguliere uitgaven", "Huisvestingskosten", "Afschrijvingen", "Winst / Verlies"],
         [f"€ {total_inc:,.2f}", f"€ {total_exp:,.2f}", f"€ {total_huisvestingskosten:,.2f}",
          f"€ {total_afschrijvingen:,.2f}", f"€ {balance:,.2f}"]],
        colWidths=[5.2*cm] * 5
    )
    s = _summary_style()
    s.add("TEXTCOLOR", (4, 1), (4, 1), SUCCESS if balance >= 0 else DANGER)
    summary.setStyle(s)
    story.append(summary)
    story.append(Spacer(1, 0.25*cm))
    story.append(_P(f"Zakelijke kilometers: {total_km:,.1f} km", fontName="Helvetica",
                    fontSize=9, textColor=TEXT_LIGHT))
    story.append(Spacer(1, 0.3*cm))

    story.append(_P("Inkomsten & uitgaven per maand", fontName="Helvetica-Bold",
                    fontSize=10, textColor=TEXT, spaceAfter=4))
    story.append(_monthly_chart(inc_by_month, exp_by_month, month_names))
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

    inc_cat_totals = {}
    for i in incomes:
        key = i.category.name if i.category else "Onbekend"
        inc_cat_totals[key] = inc_cat_totals.get(key, 0) + i.amount
    chart = _category_chart(list(inc_cat_totals.items()), SUCCESS)
    if chart:
        story.append(_P("Inkomsten per categorie", fontName="Helvetica-Bold",
                        fontSize=10, textColor=TEXT, spaceAfter=4))
        story.append(chart)
        story.append(Spacer(1, 0.4*cm))

    LABELS = received_via_labels or {}
    headers = ["Factuurnummer", "Datum", "Categorie", "Omschrijving", "Bedrag (€)", "Status", "Ontvangen op"]
    rows = [headers]
    for i in incomes:
        via = (i.received_via_other if i.received_via == "overig" and i.received_via_other
               else LABELS.get(i.received_via, i.received_via or ""))
        rows.append([
            i.invoice_number or "", i.date.strftime("%d-%m-%Y"),
            i.category.name if i.category else "—", _wrap_cell(i.description),
            f"{i.amount:,.2f}", "Betaald" if i.status == "betaald" else "Openstaand", via,
        ])
    rows.append(["", "", "", "Totaal", f"{inc_total:,.2f}", "", ""])

    tbl = Table(rows, colWidths=[3.5*cm, 2.5*cm, 3.5*cm, 6*cm, 2.5*cm, 2.5*cm, 3.5*cm], repeatRows=1)
    tbl.setStyle(_tbl_style(has_footer=True))
    story.append(tbl)

    # Sectie 3: Uitgaven
    story.append(PageBreak())
    _header(story, f"Uitgaven {year}", company_name, filters_desc="")
    exp_total = sum(e.amount for e in expenses if not _is_afschrijving(e) and not _is_huisvesting(e))
    exp_dep   = sum(e.amount for e in expenses if _is_afschrijving_installment(e))
    exp_huisvesting = sum(e.amount for e in expenses if _is_huisvesting(e) and not _is_afschrijving(e))

    summary = Table(
        [["Totale uitgaven", "Huisvestingskosten", "Afschrijvingen", "Aantal"],
         [f"€ {exp_total:,.2f}", f"€ {exp_huisvesting:,.2f}", f"€ {exp_dep:,.2f}", str(len(expenses))]],
        colWidths=[5*cm, 5*cm, 5*cm, 3*cm]
    )
    s = _summary_style()
    s.add("TEXTCOLOR", (0, 1), (0, 1), DANGER)
    summary.setStyle(s)
    story.append(summary)
    story.append(Spacer(1, 0.4*cm))

    exp_cat_totals = {}
    for e in expenses:
        if _is_afschrijving(e) or _is_huisvesting(e):
            continue
        key = e.category.name if e.category else "Onbekend"
        exp_cat_totals[key] = exp_cat_totals.get(key, 0) + e.amount
    chart = _category_chart(list(exp_cat_totals.items()), DANGER)
    if chart:
        story.append(_P("Uitgaven per categorie", fontName="Helvetica-Bold",
                        fontSize=10, textColor=TEXT, spaceAfter=4))
        story.append(chart)
        story.append(Spacer(1, 0.4*cm))

    headers = ["Factuurnummer", "Datum", "Categorie", "Omschrijving", "Bedrag (€)", "Afschr."]
    rows = [headers]
    for e in expenses:
        rows.append([
            e.invoice_number or "", e.date.strftime("%d-%m-%Y"),
            e.category.name if e.category else "—", _wrap_cell(e.description),
            f"{e.amount:,.2f}", "Ja" if e.is_depreciable else "—",
        ])
    rows.append(["", "", "", "Totaal", f"{exp_total:,.2f}", ""])

    tbl = Table(rows, colWidths=[4*cm, 2.5*cm, 3.5*cm, 8.5*cm, 2.5*cm, 2.5*cm], repeatRows=1)
    tbl.setStyle(_tbl_style(has_footer=True))
    story.append(tbl)

    doc.build(story)
    buf.seek(0)
    return buf


def generate_rapportage_pdf(data: dict, company_name="") -> BytesIO:
    """PDF-versie van de Rapportages-pagina: categorieverdeling, uren,
    kilometers en afschrijvingsoverzicht voor het gekozen jaar."""
    year = data["year"]
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4),
                            leftMargin=1.5*cm, rightMargin=1.5*cm,
                            topMargin=1.5*cm, bottomMargin=1.5*cm)
    story = []
    _header(story, f"Rapportage {year}", company_name, filters_desc="")

    total_inc = data["total_income"]
    total_exp = data["total_expenses"]
    profit = data["profit"]
    profit_col = 2
    summary_rows = [["Totale inkomsten", "Totale uitgaven", "Netto resultaat"],
                     [f"€ {total_inc:,.2f}", f"€ {total_exp:,.2f}", f"€ {profit:,.2f}"]]
    if data.get("total_huisvestingskosten"):
        summary_rows[0].insert(2, "Huisvestingskosten")
        summary_rows[1].insert(2, f"€ {data['total_huisvestingskosten']:,.2f}")
        profit_col += 1
    if data.get("dep_this_year"):
        summary_rows[0].insert(profit_col, "Afschrijvingen dit jaar")
        summary_rows[1].insert(profit_col, f"€ {data['dep_this_year']:,.2f}")
        profit_col += 1
    summary = Table(summary_rows, colWidths=[6*cm] * len(summary_rows[0]))
    s = _summary_style()
    s.add("TEXTCOLOR", (profit_col, 1), (profit_col, 1), SUCCESS if profit >= 0 else DANGER)
    summary.setStyle(s)
    story.append(summary)
    story.append(Spacer(1, 0.5*cm))

    # Inkomsten & uitgaven per categorie, naast elkaar
    both = bool(data["income_by_cat"]) and bool(data["expense_by_cat"])
    chart_width = 220 if both else 480
    name_len = 14 if both else 22
    inc_chart = _category_chart(data["income_by_cat"], SUCCESS, total_width=chart_width, name_len=name_len)
    exp_chart = _category_chart(data["expense_by_cat"], DANGER, total_width=chart_width, name_len=name_len)
    if inc_chart or exp_chart:
        headers_row = []
        chart_row = []
        if inc_chart:
            headers_row.append(_P("Inkomsten per categorie", fontName="Helvetica-Bold", fontSize=10, textColor=TEXT))
            chart_row.append(inc_chart)
        if exp_chart:
            headers_row.append(_P("Uitgaven per categorie", fontName="Helvetica-Bold", fontSize=10, textColor=TEXT))
            chart_row.append(exp_chart)
        col_w = 16*cm if len(chart_row) == 1 else 8*cm
        t = Table([headers_row, chart_row], colWidths=[col_w] * len(chart_row))
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story.append(t)
        story.append(Spacer(1, 0.4*cm))
    elif not data["income_by_cat"] and not data["expense_by_cat"]:
        story.append(_P(f"Geen inkomsten of uitgaven in {year}.", fontName="Helvetica-Oblique",
                        fontSize=9, textColor=TEXT_LIGHT))
        story.append(Spacer(1, 0.4*cm))

    # Urenregistratie
    story.append(PageBreak())
    _header(story, "Urenregistratie", company_name, filters_desc="")
    total_hours = data["total_hours"]
    summary = Table(
        [["Totaal uren", "Urencriterium voortgang", "Categorieën"],
         [f"{total_hours:,.1f}", f"{data['urencriterium_pct']}% van {data['urencriterium']}", str(len(data["hours_by_cat"]))]],
        colWidths=[6*cm, 6*cm, 6*cm]
    )
    summary.setStyle(_summary_style())
    story.append(summary)
    story.append(Spacer(1, 0.4*cm))
    if data["hours_by_cat"]:
        headers = ["Categorie", "Uren", "%"]
        rows = [headers]
        for name, total in data["hours_by_cat"]:
            pct = (total / total_hours * 100) if total_hours else 0
            rows.append([name, f"{total:,.1f}", f"{pct:.0f}%"])
        tbl = Table(rows, colWidths=[8*cm, 4*cm, 4*cm], repeatRows=1)
        tbl.setStyle(_tbl_style())
        story.append(tbl)
    else:
        story.append(_P(f"Geen uren geregistreerd in {year}.", fontName="Helvetica-Oblique",
                        fontSize=9, textColor=TEXT_LIGHT))

    # Kilometerregistratie
    story.append(Spacer(1, 0.6*cm))
    story.append(_P("Gereden kilometers per maand", fontName="Helvetica-Bold",
                    fontSize=12, textColor=BRAND, spaceAfter=6))
    summary = Table(
        [["Totaal km", "Totaal bedrag"],
         [f"{data['total_km']:,.1f}", f"€ {data['total_km_amount']:,.2f}"]],
        colWidths=[8*cm, 8*cm]
    )
    summary.setStyle(_summary_style())
    story.append(summary)
    story.append(Spacer(1, 0.4*cm))
    if data["total_km"]:
        headers = ["Maand", "Km", "Bedrag (€)"]
        rows = [headers]
        for r in data["km_rows"]:
            rows.append([r["name"], f"{r['km']:,.1f}", f"{r['amount']:,.2f}"])
        tbl = Table(rows, colWidths=[8*cm, 4*cm, 4*cm], repeatRows=1)
        tbl.setStyle(_tbl_style())
        story.append(tbl)
    else:
        story.append(_P(f"Geen kilometers geregistreerd in {year}.", fontName="Helvetica-Oblique",
                        fontSize=9, textColor=TEXT_LIGHT))

    # Afschrijvingsoverzicht
    if data["depreciations"]:
        story.append(PageBreak())
        _header(story, "Afschrijvingsoverzicht", company_name, filters_desc="")
        for dep in data["depreciations"]:
            story.append(_P(f"{dep['description']} — {dep['invoice']}", fontName="Helvetica-Bold",
                            fontSize=11, textColor=TEXT, spaceAfter=6))
            summary = Table(
                [["Aanschaf", "Per jaar", "Restwaarde", "Boekwaarde", "Volledig afgeschreven"],
                 [f"€ {dep['purchase']:,.2f}", f"€ {dep['annual']:,.2f}", f"€ {dep['residual']:,.2f}",
                  f"€ {dep['book_value']:,.2f}", str(dep['end_year'])]],
                colWidths=[5*cm] * 5
            )
            summary.setStyle(_summary_style())
            story.append(summary)
            story.append(Spacer(1, 0.3*cm))

            headers = ["Jaar", "Afschrijving (€)", "Cumulatief (€)", "Boekwaarde (€)"]
            rows = [headers]
            for s_row in dep["schedule"]:
                rows.append([str(s_row["year"]), f"{s_row['amount']:,.2f}",
                            f"{s_row['cumulative']:,.2f}", f"{s_row['book_value']:,.2f}"])
            tbl = Table(rows, colWidths=[4*cm, 7*cm, 7*cm, 7*cm], repeatRows=1)
            tbl.setStyle(_tbl_style())
            story.append(tbl)
            story.append(Spacer(1, 0.6*cm))

    doc.build(story)
    buf.seek(0)
    return buf
