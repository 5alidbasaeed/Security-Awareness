"""
Executive summary PDF (ReportLab, pure Python). Output is deterministic
(`invariant=1`: no embedded timestamp), so regenerating the same period from
the same event log yields byte-identical files — and the same SHA-256.

Limitation: ReportLab does not shape right-to-left scripts (Arabic, Hebrew).
Non-Latin names still round-trip losslessly in the CSV exports.
"""

import io
import os
from xml.sax.saxutils import escape

from reportlab.graphics.charts.linecharts import HorizontalLineChart
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.widgets.markers import makeMarker
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.risk_scoring import analytics

from .data import ReportData

FONT_DIR = "/usr/share/fonts/truetype/dejavu"
INK = colors.HexColor("#1a1a1a")
MUTED = colors.HexColor("#6b7280")
HAIRLINE = colors.HexColor("#e5e7eb")
ACCENT = colors.HexColor("#2563eb")
BAND = colors.HexColor("#f9fafb")


def _fonts() -> tuple[str, str]:
    """DejaVu (broad Latin/Cyrillic/Greek coverage) when installed, else built-in Helvetica."""
    regular, bold = os.path.join(FONT_DIR, "DejaVuSans.ttf"), os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf")
    if os.path.exists(regular) and os.path.exists(bold):
        for name, path in (("DejaVuSans", regular), ("DejaVuSans-Bold", bold)):
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, path))
        return "DejaVuSans", "DejaVuSans-Bold"
    return "Helvetica", "Helvetica-Bold"


def _fmt(value, suffix="") -> str:
    return "—" if value is None else f"{value:g}{suffix}"


def _table(header, rows, widths, font, bold, numeric_from=1):
    data = [header] + rows
    table = Table(data, colWidths=widths, repeatRows=1)
    style = [
        ("FONTNAME", (0, 0), (-1, 0), bold), ("FONTNAME", (0, 1), (-1, -1), font),
        ("FONTSIZE", (0, 0), (-1, -1), 8), ("TEXTCOLOR", (0, 0), (-1, 0), MUTED), ("TEXTCOLOR", (0, 1), (-1, -1), INK),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, HAIRLINE), ("ALIGN", (numeric_from, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    for index in range(2, len(data), 2):
        style.append(("BACKGROUND", (0, index), (-1, index), BAND))
    table.setStyle(TableStyle(style))
    return table


def _trend_chart(points, width, font):
    drawing = Drawing(width, 150)
    values = [p["average_score"] for p in points]
    if not any(v is not None for v in values):
        return None
    chart = HorizontalLineChart()
    chart.x, chart.y, chart.width, chart.height = 30, 22, width - 45, 115
    chart.data = [values]
    chart.categoryAxis.categoryNames = [p["week_ending"][5:] for p in points]
    chart.categoryAxis.labels.fontSize = 6
    chart.categoryAxis.labels.fontName = font
    chart.categoryAxis.strokeColor = HAIRLINE
    chart.categoryAxis.visibleTicks = False
    chart.categoryAxis.labels.angle = 0
    chart.valueAxis.valueMin, chart.valueAxis.valueMax, chart.valueAxis.valueStep = 0, 100, 25
    chart.valueAxis.labels.fontSize = 7
    chart.valueAxis.labels.fontName = font
    chart.valueAxis.visibleAxis = False
    chart.valueAxis.visibleTicks = False
    chart.valueAxis.gridStrokeColor = HAIRLINE
    chart.valueAxis.visibleGrid = True
    chart.lines[0].strokeColor, chart.lines[0].strokeWidth = ACCENT, 1.6
    chart.lines[0].symbol = makeMarker("Circle")  # a lone data point must still show
    chart.lines[0].symbol.fillColor = ACCENT
    chart.lines[0].symbol.strokeColor = ACCENT
    chart.lines[0].symbol.size = 4
    drawing.add(chart)
    return drawing


def render_executive_summary(data: ReportData) -> bytes:
    font, bold = _fonts()
    h1 = ParagraphStyle("h1", fontName=bold, fontSize=20, leading=24, textColor=INK, spaceAfter=2)
    h2 = ParagraphStyle("h2", fontName=bold, fontSize=12, leading=16, textColor=INK, spaceBefore=14, spaceAfter=6)
    body = ParagraphStyle("body", fontName=font, fontSize=9, leading=13, textColor=INK)
    muted = ParagraphStyle("muted", fontName=font, fontSize=8, leading=11, textColor=MUTED)
    big = ParagraphStyle("big", fontName=bold, fontSize=18, leading=22, textColor=INK)

    headline = data.headline
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
        title="Security awareness — executive summary", author="Security Awareness Platform", invariant=1,
    )
    width = A4[0] - 36 * mm

    story = [
        Paragraph("Security awareness — executive summary", h1),
        Paragraph(
            f"{data.period_start:%d %b %Y} – {data.period_end:%d %b %Y} · {escape(data.scope.label)} · "
            f"figures as of {data.as_of:%d %b %Y}", muted,
        ),
        Spacer(1, 10),
    ]

    tiles = [
        ("Average risk score", _fmt(headline["average_score_end"]), f"was {_fmt(headline['average_score_start'])} at period start" if headline["average_score_start"] is not None else "no employees scored at period start"),
        ("High-risk employees", str(headline["high_risk_employees"]), f"score {analytics.HIGH_RISK_THRESHOLD} or above"),
        ("Campaign failure rate", _fmt(headline["failure_rate_percent"], "%"), f"{headline['campaigns_run']} campaign(s), {headline['employees_targeted']} targeted"),
        ("Training completion", _fmt(headline["training"]["completion_rate_percent"], "%"), f"{headline['training']['overdue']} overdue"),
    ]
    tile_table = Table(
        [[Paragraph(label, muted) for label, _, _ in tiles], [Paragraph(value, big) for _, value, _ in tiles], [Paragraph(note, muted) for _, _, note in tiles]],
        colWidths=[width / 4] * 4,
    )
    tile_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("BOX", (0, 0), (-1, -1), 0.6, HAIRLINE), ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 4)]))
    story += [tile_table, Spacer(1, 4)]

    if headline["direction"]:
        story.append(Paragraph(f"Over the period the average score moved {headline['score_change']:+g} points — <b>{headline['direction']}</b> (lower is better).", body))

    chart = _trend_chart(data.trend, width, font)
    story.append(Paragraph("Risk trend", h2))
    story.append(chart if chart is not None else Paragraph("No score history in this period.", muted))

    story.append(Paragraph("Campaigns in this period", h2))
    if data.campaign_rows:
        rows = [[Paragraph(escape(c["campaign"]), body), c["launched"], str(c["targeted"]), _fmt(c["failure_rate_percent"], "%"), _fmt(c["submit_rate_percent"], "%"), _fmt(c["report_rate_percent"], "%")] for c in data.campaign_rows]
        story.append(_table(["Campaign", "Launched", "Targeted", "Failed", "Data", "Reported"], rows, [width * 0.36, width * 0.15, width * 0.13, width * 0.12, width * 0.12, width * 0.12], font, bold, numeric_from=2))
    else:
        story.append(Paragraph("No campaigns were launched in this period.", muted))

    story.append(Paragraph("Departments", h2))
    if data.department_rows:
        rows = [[Paragraph(escape(d["department"]), body), _fmt(d["average_score"]), str(d["high_risk_employees"]), _fmt(d["training_completion_percent"], "%"), str(d["training_overdue"])] for d in data.department_rows]
        story.append(_table(["Department", "Avg risk", "High risk", "Training done", "Overdue"], rows, [width * 0.36, width * 0.16, width * 0.16, width * 0.18, width * 0.14], font, bold))
    else:
        story.append(Paragraph("No employees in scope.", muted))

    story.append(KeepTogether([
        Paragraph("Method", h2),
        Paragraph(
            f"Scores use algorithm <b>{data.algorithm_version}</b>: per campaign only the worst outcome counts (submitting data 50, clicking 25), "
            "reporting the email earns credit, each repeat failure adds a penalty, and results fade with a 180-day half-life. "
            f"Risk levels: medium from {analytics.MEDIUM_RISK_THRESHOLD}, high from {analytics.HIGH_RISK_THRESHOLD}. "
            "Email-open events and training completion are excluded from the score; training completion is reported separately. "
            "All figures are recomputed from the immutable event log as it stood on the report date, so this report can be regenerated exactly.",
            muted,
        ),
    ]))

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont(font, 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(18 * mm, 10 * mm, "Confidential — internal security awareness reporting")
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {document.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
