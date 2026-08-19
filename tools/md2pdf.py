#!/usr/bin/env python3
"""Render the RFTag SOP from Markdown to a print-ready PDF handout."""

import re
import sys

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (BaseDocTemplate, Frame, KeepTogether,
                                PageBreak, PageTemplate, Paragraph,
                                Preformatted, Spacer, Table, TableStyle)

# ---------------------------------------------------------------- palette

INK = colors.HexColor("#1a1a1a")
MUTED = colors.HexColor("#5c5c5c")
RULE = colors.HexColor("#c8c8c8")
ACCENT = colors.HexColor("#1f4e79")
CODE_BG = colors.HexColor("#f4f4f2")
CODE_RULE = colors.HexColor("#dcdcd8")
CALLOUT_BG = colors.HexColor("#fdf6e3")
CALLOUT_RULE = colors.HexColor("#d9a441")
HEAD_BG = colors.HexColor("#eaeef2")

PAGE_W, PAGE_H = letter
MARGIN = 0.72 * inch
FRAME_W = PAGE_W - 2 * MARGIN

CODE_SIZE = 7.1
CODE_LEAD = 8.7
# Courier advance width is 0.6 em; leave a little slack inside the padded box.
CODE_COLS = int((FRAME_W - 16) / (CODE_SIZE * 0.6))

# ---------------------------------------------------------------- styles

body = ParagraphStyle("body", fontName="Helvetica", fontSize=9.3, leading=13.4,
                      textColor=INK, alignment=TA_LEFT, spaceAfter=7)
h1 = ParagraphStyle("h1", parent=body, fontName="Helvetica-Bold", fontSize=19,
                    leading=23, textColor=ACCENT, spaceBefore=0, spaceAfter=3)
h2 = ParagraphStyle("h2", parent=body, fontName="Helvetica-Bold", fontSize=13.5,
                    leading=17, textColor=ACCENT, spaceBefore=17, spaceAfter=7,
                    keepWithNext=1)
h3 = ParagraphStyle("h3", parent=body, fontName="Helvetica-Bold", fontSize=10.6,
                    leading=14, textColor=INK, spaceBefore=12, spaceAfter=5,
                    keepWithNext=1)
# A line like "Find and stop the other holder:" introduces the block beneath it
# and should not be left stranded at the foot of a page.
leadin = ParagraphStyle("leadin", parent=body, keepWithNext=1)
code = ParagraphStyle("code", fontName="Courier", fontSize=CODE_SIZE,
                      leading=CODE_LEAD, textColor=INK)
cell = ParagraphStyle("cell", parent=body, fontSize=8.3, leading=11.2,
                      spaceAfter=0)
cellh = ParagraphStyle("cellh", parent=cell, fontName="Helvetica-Bold")
callout = ParagraphStyle("callout", parent=body, fontSize=9.0, leading=13,
                         spaceAfter=0)
listp = ParagraphStyle("listp", parent=body, spaceAfter=4)

# ---------------------------------------------------------------- inline

def esc(text):
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def inline(text, heading=False):
    """Markdown inline formatting -> reportlab markup.

    Inside a heading, code spans keep the heading's weight and size -- styling
    them like body code makes the heading stop reading as a heading.
    """
    out = esc(text)
    # Inline code first, so ** inside backticks is not mistaken for bold.
    holds = []

    def hold(m):
        holds.append(m.group(1))
        return f"\x00{len(holds) - 1}\x00"

    out = re.sub(r"`([^`]+)`", hold, out)
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)
    out = re.sub(r"(?<![A-Za-z0-9])_(.+?)_(?![A-Za-z0-9])", r"<i>\1</i>", out)
    # Markdown links -> just the label; a printed page cannot be clicked.
    out = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", out)

    def unhold(m):
        frag = holds[int(m.group(1))]
        if heading:
            return f'<font face="Courier-Bold">{frag}</font>'
        return (f'<font face="Courier" size="8.3" '
                f'color="#8a2f2f">{frag}</font>')

    return re.sub(r"\x00(\d+)\x00", unhold, out)


def wrap_code(line):
    """Soft-wrap an over-long terminal line, marking the continuation."""
    if len(line) <= CODE_COLS:
        return [line]
    parts, rest = [], line
    while len(rest) > CODE_COLS:
        cut = rest.rfind(" ", 0, CODE_COLS)
        if cut < CODE_COLS * 0.55:      # no sensible break: hard-cut
            cut = CODE_COLS
        parts.append(rest[:cut])
        rest = "    " + rest[cut:].lstrip()
    parts.append(rest)
    return parts


# ---------------------------------------------------------------- parsing

def parse(md):
    blocks, lines, i = [], md.split("\n"), 0
    while i < len(lines):
        ln = lines[i]

        if ln.startswith("```"):
            lang = ln[3:].strip()
            i += 1
            buf = []
            while i < len(lines) and not lines[i].startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            blocks.append(("code", lang, buf))
            continue

        if ln.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|$", lines[i + 1]):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                if not re.match(r"^\|[\s:|-]+\|$", lines[i]):
                    cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                    rows.append(cells)
                i += 1
            blocks.append(("table", rows))
            continue

        if ln.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].startswith(">"):
                buf.append(lines[i].lstrip(">").strip())
                i += 1
            blocks.append(("quote", " ".join(b for b in buf if b)))
            continue

        m = re.match(r"^(#{1,3})\s+(.*)", ln)
        if m:
            blocks.append((f"h{len(m.group(1))}", m.group(2)))
            i += 1
            continue

        if re.match(r"^---+\s*$", ln):
            blocks.append(("hr",))
            i += 1
            continue

        if re.match(r"^\s*[-*]\s+", ln) or re.match(r"^\s*\d+\.\s+", ln):
            items, ordered = [], bool(re.match(r"^\s*\d+\.\s+", ln))
            while i < len(lines) and (re.match(r"^\s*[-*]\s+", lines[i])
                                      or re.match(r"^\s*\d+\.\s+", lines[i])
                                      or (lines[i].startswith("   ") and lines[i].strip()
                                          and items)):
                if re.match(r"^\s*[-*]\s+", lines[i]) or re.match(r"^\s*\d+\.\s+", lines[i]):
                    items.append(re.sub(r"^\s*(?:[-*]|\d+\.)\s+", "", lines[i]))
                else:
                    items[-1] += " " + lines[i].strip()
                i += 1
            blocks.append(("ol" if ordered else "ul", items))
            continue

        if ln.strip():
            buf = []
            while i < len(lines) and lines[i].strip() and not lines[i].startswith(("#", "|", ">", "```")) \
                    and not re.match(r"^\s*[-*]\s+", lines[i]) and not re.match(r"^---+\s*$", lines[i]):
                buf.append(lines[i].strip())
                i += 1
            blocks.append(("p", " ".join(buf)))
            continue

        i += 1
    return blocks


# ---------------------------------------------------------------- render

def code_flowable(lines):
    wrapped = []
    for ln in lines:
        wrapped.extend(wrap_code(ln.rstrip()))
    inner = Preformatted("\n".join(wrapped) or " ", code)
    t = Table([[inner]], colWidths=[FRAME_W])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CODE_BG),
        ("BOX", (0, 0), (-1, -1), 0.5, CODE_RULE),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return t


def table_flowable(rows):
    header, data = rows[0], rows[1:]
    ncols = len(header)
    # Weight columns by the content they carry, then clamp so no column starves.
    weights = []
    for c in range(ncols):
        longest = max([len(r[c]) for r in rows if c < len(r)] or [1])
        weights.append(max(longest, 6))
    total = sum(weights)
    widths = [max(FRAME_W * w / total, FRAME_W * 0.11) for w in weights]
    scale = FRAME_W / sum(widths)
    widths = [w * scale for w in widths]

    grid = [[Paragraph(inline(c), cellh) for c in header]]
    for r in data:
        r = r + [""] * (ncols - len(r))
        grid.append([Paragraph(inline(c), cell) for c in r[:ncols]])

    t = Table(grid, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), HEAD_BG),
        ("LINEBELOW", (0, 0), (-1, 0), 0.7, RULE),
        ("GRID", (0, 0), (-1, -1), 0.35, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def quote_flowable(text):
    t = Table([[Paragraph(inline(text), callout)]], colWidths=[FRAME_W])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CALLOUT_BG),
        ("LINEBEFORE", (0, 0), (0, -1), 2.4, CALLOUT_RULE),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def build_story(blocks):
    story = []
    for b in blocks:
        kind = b[0]
        if kind == "h1":
            story.append(Paragraph(inline(b[1], heading=True), h1))
        elif kind == "h2":
            story.append(Paragraph(inline(b[1], heading=True), h2))
        elif kind == "h3":
            story.append(Paragraph(inline(b[1], heading=True), h3))
        elif kind == "p":
            style = leadin if b[1].rstrip().endswith(":") else body
            story.append(Paragraph(inline(b[1]), style))
        elif kind == "code":
            story.append(code_flowable(b[2]))
            story.append(Spacer(1, 8))
        elif kind == "table":
            story.append(table_flowable(b[1]))
            story.append(Spacer(1, 9))
        elif kind == "quote":
            story.append(quote_flowable(b[1]))
            story.append(Spacer(1, 8))
        elif kind in ("ul", "ol"):
            for n, item in enumerate(b[1], 1):
                bullet = f"{n}." if kind == "ol" else "•"
                t = Table([[Paragraph(bullet, listp), Paragraph(inline(item), listp)]],
                          colWidths=[16, FRAME_W - 16])
                t.setStyle(TableStyle([
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]))
                story.append(t)
            story.append(Spacer(1, 6))
        elif kind == "hr":
            story.append(Spacer(1, 5))
    return story


TITLE = "RFTag Messaging SOP"
SUBTITLE = "Sending and Receiving LoRa Messages from a Raspberry Pi"


def decorate(canvas, doc):
    canvas.saveState()
    if doc.page > 1:
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(MARGIN, PAGE_H - MARGIN + 16, TITLE)
        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(0.4)
        canvas.line(MARGIN, PAGE_H - MARGIN + 11, PAGE_W - MARGIN, PAGE_H - MARGIN + 11)
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.4)
    canvas.line(MARGIN, MARGIN - 13, PAGE_W - MARGIN, MARGIN - 13)
    canvas.drawString(MARGIN, MARGIN - 24,
                      "Everbliss Green — firmware 2.2.2-rel — 2026-08-19")
    canvas.drawRightString(PAGE_W - MARGIN, MARGIN - 24, f"Page {doc.page}")
    canvas.restoreState()


def main(src, dst):
    md = open(src).read()
    # The H1 and the intro become a masthead instead of body text.
    blocks = parse(md)
    blocks = [b for b in blocks if b[0] != "h1"]

    doc = BaseDocTemplate(dst, pagesize=letter,
                          leftMargin=MARGIN, rightMargin=MARGIN,
                          topMargin=MARGIN, bottomMargin=MARGIN + 8,
                          title=TITLE, author="Everbliss Green",
                          subject=SUBTITLE)
    frame = Frame(MARGIN, MARGIN + 8, FRAME_W, PAGE_H - 2 * MARGIN - 8, id="f")
    doc.addPageTemplates([PageTemplate(id="all", frames=[frame], onPage=decorate)])

    story = [
        Paragraph(TITLE, h1),
        Paragraph(f'<font color="#5c5c5c" size="10">{SUBTITLE}</font>', body),
        Spacer(1, 4),
    ]
    meta = Table([[
        Paragraph('<b>Firmware</b><br/>2.2.2-rel (b5b3ef9b4470)', cell),
        Paragraph('<b>Host</b><br/>Raspberry Pi OS Lite 64-bit (Debian 13)', cell),
        Paragraph('<b>Verified</b><br/>2026-08-19', cell),
    ]], colWidths=[FRAME_W / 3] * 3)
    meta.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), HEAD_BG),
        ("BOX", (0, 0), (-1, -1), 0.4, RULE),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story += [meta, Spacer(1, 12)]
    story += build_story(blocks)

    doc.build(story)
    print(f"wrote {dst}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
