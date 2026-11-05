"""The invoice as a PDF. Built from the details frozen when the invoice was issued, so reprinting an old invoice never changes it."""
import io
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

IST = ZoneInfo("Asia/Kolkata")


def inr(paise: int) -> str:
    """Indian digit grouping (12,34,567.00). The built-in PDF fonts have no rupee sign, so amounts are written with 'Rs.'"""
    rupees, ps = divmod(abs(int(paise)), 100)
    s = str(rupees)
    s = s if len(s) <= 3 else (lambda head, tail: (",".join([head[max(i - 2, 0):i] for i in range(len(head), 0, -2)][::-1]) + "," + tail))(s[:-3], s[-3:])
    return f"{'-' if paise < 0 else ''}Rs. {s}.{ps:02d}"


def build(inv: dict, lines: list) -> bytes:
    """inv: number, status, period_month, issued_at, due_at, paid_at, subtotal_paise, cgst_paise, sgst_paise, igst_paise, total_paise, tax_rate, seller, buyer"""
    base = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=base["Normal"], fontSize=8.5, leading=11, textColor=colors.HexColor("#444444"))
    body = ParagraphStyle("body", parent=base["Normal"], fontSize=9.5, leading=12.5)
    h = ParagraphStyle("h", parent=base["Heading1"], fontSize=18, leading=22, textColor=colors.HexColor("#0F766E"), spaceAfter=2)
    right = ParagraphStyle("right", parent=body, alignment=2)
    s, b = inv["seller"] or {}, inv["buyer"] or {}
    P = lambda text, st=body: Paragraph(escape(str(text or "")).replace("\n", "<br/>"), st)
    d = lambda x: x.astimezone(IST).strftime("%d %b %Y") if x else "-"
    story = [Paragraph("TAX INVOICE" if inv["status"] != "void" else "TAX INVOICE (VOID)", h), P(f"Invoice no. {inv['number']}", body),
             P(f"Date: {d(inv['issued_at'])}     Due: {d(inv['due_at'])}     Period: {inv['period_month']:%B %Y}", small), Spacer(1, 6 * mm)]
    story.append(Table([[[P("From", small), P(s.get("legal_name"), ParagraphStyle("b", parent=body, fontName="Helvetica-Bold")), P(s.get("address")), P(f"GSTIN: {s.get('gstin', '')}", small)],
                         [P("Bill to", small), P(b.get("name"), ParagraphStyle("b2", parent=body, fontName="Helvetica-Bold")), P(b.get("address")), P(f"GSTIN: {b.get('gstin') or 'not registered'}   State code: {b.get('state_code', '')}", small)]]],
                       colWidths=[90 * mm, 90 * mm], style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")])))
    story.append(Spacer(1, 6 * mm))
    rows = [[P("Description", small), P("SAC", small), P("Camera-days", small), P("Price / camera / month", small), P("Amount", small)]]
    for l in lines:
        rows.append([P(l["description"]), P(s.get("sac_code")), P(l["camera_days"], right), P(inr(l["unit_paise"]), right), P(inr(l["amount_paise"]), right)])
    rows.append(["", "", "", P("Subtotal", right), P(inr(inv["subtotal_paise"]), right)])
    rate = f"{inv['tax_rate']:g}"
    if inv["igst_paise"]:
        rows.append(["", "", "", P(f"IGST @ {rate}%", right), P(inr(inv["igst_paise"]), right)])
    else:
        half = f"{float(inv['tax_rate']) / 2:g}"
        rows.append(["", "", "", P(f"CGST @ {half}%", right), P(inr(inv["cgst_paise"]), right)])
        rows.append(["", "", "", P(f"SGST @ {half}%", right), P(inr(inv["sgst_paise"]), right)])
    rows.append(["", "", "", P("<b>Total</b>", right), Paragraph(f"<b>{inr(inv['total_paise'])}</b>", right)])
    t = Table(rows, colWidths=[78 * mm, 18 * mm, 22 * mm, 36 * mm, 28 * mm], repeatRows=1)
    t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#0F766E")), ("LINEBELOW", (0, 1), (-1, len(lines)), 0.25, colors.HexColor("#CCCCCC")),
                           ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEABOVE", (3, -1), (-1, -1), 0.6, colors.black)]))
    story += [t, Spacer(1, 8 * mm)]
    if inv["status"] == "paid":
        story.append(P(f"Paid on {d(inv['paid_at'])}.", body))
    elif s.get("bank_details"):
        story += [P("Payment details", small), P(s["bank_details"])]
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm, bottomMargin=15 * mm, title=f"Invoice {inv['number']}").build(story)
    return buf.getvalue()
