"""Рендеринг документа: Markdown (просмотр и копирование в отчёте) и DOCX (Word)."""

import io

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor

from .templates import Doc


def to_markdown(doc: Doc) -> str:
    out = [f"# {doc.title}", ""]
    for b in doc.blocks:
        kind = b[0]
        if kind == "h":
            out += [f"## {b[1]}", ""]
        elif kind == "p":
            out += [b[1], ""]
        elif kind == "note":
            out += [f"> {b[1]}", ""]
        elif kind == "ul":
            out += [f"- {i}" for i in b[1]] + [""]
        elif kind == "code":
            out += ["```html", b[1], "```", ""]
        elif kind == "table":
            head, rows = b[1], b[2]
            out += ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
            out += ["| " + " | ".join(c.replace("|", "/") for c in r) + " |" for r in rows] + [""]
    return "\n".join(out).strip() + "\n"


def to_docx(doc: Doc) -> bytes:
    d = Document()
    style = d.styles["Normal"]
    style.font.name = "Times New Roman"
    style.font.size = Pt(12)
    title = d.add_heading(doc.title, level=1)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for b in doc.blocks:
        kind = b[0]
        if kind == "h":
            d.add_heading(b[1], level=2)
        elif kind == "p":
            p = d.add_paragraph(b[1])
            p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        elif kind == "note":
            run = d.add_paragraph().add_run(b[1])
            run.italic = True
            run.font.color.rgb = RGBColor(0x99, 0x55, 0x00)
        elif kind == "ul":
            for item in b[1]:
                d.add_paragraph(item, style="List Bullet")
        elif kind == "code":
            run = d.add_paragraph().add_run(b[1])
            run.font.name = "Courier New"
            run.font.size = Pt(9)
        elif kind == "table":
            head, rows = b[1], b[2]
            t = d.add_table(rows=1, cols=len(head))
            t.style = "Table Grid"
            for i, h in enumerate(head):
                t.rows[0].cells[i].text = h
                for r in t.rows[0].cells[i].paragraphs[0].runs:
                    r.bold = True
            for row in rows:
                cells = t.add_row().cells
                for i, c in enumerate(row):
                    cells[i].text = c
            d.add_paragraph()
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()
