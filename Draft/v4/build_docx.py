#!/usr/bin/env python3
"""Combine v4 markdown draft parts into a formatted Word document."""

import re
import os
from docx import Document
from docx.shared import Pt, Inches, RGBColor, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.section import WD_ORIENT
from docx.oxml.ns import qn, nsdecls
from docx.oxml import parse_xml

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Part definitions: (filename, part_title, subtitle)
PARTS = [
    ("part1.md", "Part One", "The Surface"),
    ("part2.md", "Part Two", "The Descent"),
    ("part3.md", "Part Three", "The Descent"),
    ("part4.md", "Part Four", "The Fall"),
    ("part5.md", "Part Five", "The 48 Hours"),
]

EPIGRAPH = (
    "The technology described in this novel is real. "
    "The political structures are real. "
    "The concentration of power is real. "
    "The only fiction is that someone decided to do something about it."
)


def setup_styles(doc):
    """Configure document styles for a novel."""
    # Page setup
    section = doc.sections[0]
    section.page_width = Inches(6)
    section.page_height = Inches(9)
    section.top_margin = Inches(1)
    section.bottom_margin = Inches(0.8)
    section.left_margin = Inches(0.85)
    section.right_margin = Inches(0.85)

    # Default font
    style = doc.styles["Normal"]
    font = style.font
    font.name = "Garamond"
    font.size = Pt(11)
    style.paragraph_format.space_after = Pt(0)
    style.paragraph_format.space_before = Pt(0)
    style.paragraph_format.line_spacing = 1.15
    style.paragraph_format.first_line_indent = Inches(0.3)

    # Heading 1 — Part titles
    h1 = doc.styles["Heading 1"]
    h1.font.name = "Garamond"
    h1.font.size = Pt(24)
    h1.font.bold = True
    h1.font.color.rgb = RGBColor(0, 0, 0)
    h1.paragraph_format.space_before = Pt(72)
    h1.paragraph_format.space_after = Pt(6)
    h1.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    h1.paragraph_format.first_line_indent = Inches(0)
    h1.paragraph_format.page_break_before = True

    # Heading 2 — Chapter titles
    h2 = doc.styles["Heading 2"]
    h2.font.name = "Garamond"
    h2.font.size = Pt(16)
    h2.font.bold = True
    h2.font.color.rgb = RGBColor(0, 0, 0)
    h2.paragraph_format.space_before = Pt(36)
    h2.paragraph_format.space_after = Pt(18)
    h2.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    h2.paragraph_format.first_line_indent = Inches(0)
    h2.paragraph_format.page_break_before = True

    # Heading 3 — Sub-sections (Raven interludes, Interstitials)
    h3 = doc.styles["Heading 3"]
    h3.font.name = "Garamond"
    h3.font.size = Pt(13)
    h3.font.bold = True
    h3.font.italic = True
    h3.font.color.rgb = RGBColor(0, 0, 0)
    h3.paragraph_format.space_before = Pt(24)
    h3.paragraph_format.space_after = Pt(12)
    h3.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    h3.paragraph_format.first_line_indent = Inches(0)

    # Heading 4 — Epilogue title (like Heading 1 but distinct in TOC)
    h4 = doc.styles["Heading 4"]
    h4.font.name = "Garamond"
    h4.font.size = Pt(24)
    h4.font.bold = True
    h4.font.color.rgb = RGBColor(0, 0, 0)
    h4.paragraph_format.space_before = Pt(72)
    h4.paragraph_format.space_after = Pt(6)
    h4.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    h4.paragraph_format.first_line_indent = Inches(0)
    h4.paragraph_format.page_break_before = True

    # Subtitle style for part subtitles
    if "Subtitle" not in [s.name for s in doc.styles]:
        pass  # Subtitle exists by default
    sub = doc.styles["Subtitle"]
    sub.font.name = "Garamond"
    sub.font.size = Pt(14)
    sub.font.italic = True
    sub.font.color.rgb = RGBColor(80, 80, 80)
    sub.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub.paragraph_format.space_after = Pt(24)
    sub.paragraph_format.first_line_indent = Inches(0)


def add_title_page(doc):
    """Create a title page."""
    # Add significant space at top
    for _ in range(8):
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.first_line_indent = Inches(0)

    # Title
    title_p = doc.add_paragraph()
    title_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title_p.paragraph_format.first_line_indent = Inches(0)
    run = title_p.add_run("LUMEN")
    run.font.name = "Garamond"
    run.font.size = Pt(36)
    run.font.bold = True

    # Subtitle
    sub_p = doc.add_paragraph()
    sub_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub_p.paragraph_format.space_before = Pt(12)
    sub_p.paragraph_format.first_line_indent = Inches(0)
    run = sub_p.add_run("A Novel")
    run.font.name = "Garamond"
    run.font.size = Pt(18)
    run.font.italic = True
    run.font.color.rgb = RGBColor(80, 80, 80)

    # Page break after title
    doc.add_page_break()


def add_epigraph_page(doc):
    """Add the epigraph on its own page."""
    for _ in range(6):
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.first_line_indent = Inches(0)

    ep = doc.add_paragraph()
    ep.alignment = WD_ALIGN_PARAGRAPH.CENTER
    ep.paragraph_format.left_indent = Inches(0.75)
    ep.paragraph_format.right_indent = Inches(0.75)
    ep.paragraph_format.first_line_indent = Inches(0)
    run = ep.add_run(EPIGRAPH)
    run.font.name = "Garamond"
    run.font.size = Pt(11)
    run.font.italic = True
    run.font.color.rgb = RGBColor(80, 80, 80)

    doc.add_page_break()


def add_toc(doc):
    """Add a Table of Contents page using Word field codes."""
    # TOC heading
    toc_heading = doc.add_paragraph()
    toc_heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
    toc_heading.paragraph_format.space_after = Pt(24)
    toc_heading.paragraph_format.first_line_indent = Inches(0)
    run = toc_heading.add_run("Table of Contents")
    run.font.name = "Garamond"
    run.font.size = Pt(24)
    run.font.bold = True

    # Insert TOC field — shows Heading 1 and 2 levels
    paragraph = doc.add_paragraph()
    paragraph.paragraph_format.first_line_indent = Inches(0)
    run = paragraph.add_run()
    fldChar1 = parse_xml(f'<w:fldChar {nsdecls("w")} w:fldCharType="begin"/>')
    run._r.append(fldChar1)

    run2 = paragraph.add_run()
    instrText = parse_xml(
        f'<w:instrText {nsdecls("w")} xml:space="preserve"> TOC \\o "1-2" \\h \\z \\u </w:instrText>'
    )
    run2._r.append(instrText)

    run3 = paragraph.add_run()
    fldChar2 = parse_xml(f'<w:fldChar {nsdecls("w")} w:fldCharType="separate"/>')
    run3._r.append(fldChar2)

    run4 = paragraph.add_run("(Right-click and select 'Update Field' to generate Table of Contents)")
    run4.font.name = "Garamond"
    run4.font.size = Pt(10)
    run4.font.italic = True
    run4.font.color.rgb = RGBColor(128, 128, 128)

    run5 = paragraph.add_run()
    fldChar3 = parse_xml(f'<w:fldChar {nsdecls("w")} w:fldCharType="end"/>')
    run5._r.append(fldChar3)

    doc.add_page_break()


def add_scene_break(doc):
    """Add a scene break (centered asterisks)."""
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(12)
    p.paragraph_format.space_after = Pt(12)
    p.paragraph_format.first_line_indent = Inches(0)
    run = p.add_run("*   *   *")
    run.font.name = "Garamond"
    run.font.size = Pt(11)


def add_formatted_paragraph(doc, text, is_first_after_heading=False):
    """Add a paragraph with inline formatting (bold, italic)."""
    p = doc.add_paragraph()

    if is_first_after_heading:
        p.paragraph_format.first_line_indent = Inches(0)

    # Parse inline markdown formatting
    # Handle bold+italic (***text*** or ___text___), bold (**text**), italic (*text* or _text_)
    # Pattern order matters: longest patterns first
    pattern = re.compile(
        r'(\*\*\*(.+?)\*\*\*)'   # bold+italic
        r'|(\*\*(.+?)\*\*)'       # bold
        r'|(\*(.+?)\*)'           # italic
        r'|(_(.+?)_)'             # italic with underscore (only single _)
    )

    last_end = 0
    for m in pattern.finditer(text):
        # Add plain text before this match
        if m.start() > last_end:
            plain = text[last_end:m.start()]
            if plain:
                run = p.add_run(plain)
                run.font.name = "Garamond"
                run.font.size = Pt(11)

        if m.group(2):  # bold+italic
            run = p.add_run(m.group(2))
            run.font.name = "Garamond"
            run.font.size = Pt(11)
            run.bold = True
            run.italic = True
        elif m.group(4):  # bold
            run = p.add_run(m.group(4))
            run.font.name = "Garamond"
            run.font.size = Pt(11)
            run.bold = True
        elif m.group(6):  # italic with *
            run = p.add_run(m.group(6))
            run.font.name = "Garamond"
            run.font.size = Pt(11)
            run.italic = True
        elif m.group(8):  # italic with _
            run = p.add_run(m.group(8))
            run.font.name = "Garamond"
            run.font.size = Pt(11)
            run.italic = True

        last_end = m.end()

    # Add remaining plain text
    if last_end < len(text):
        remaining = text[last_end:]
        if remaining:
            run = p.add_run(remaining)
            run.font.name = "Garamond"
            run.font.size = Pt(11)

    return p


def process_markdown_file(doc, filepath, part_title, part_subtitle, is_first_part=False):
    """Process a single markdown file and add its content to the document."""
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # Skip title/epigraph metadata in part1
    start_line = 0
    if is_first_part:
        # Skip everything before the first ## Chapter
        for i, line in enumerate(lines):
            if line.startswith("## Chapter"):
                start_line = i
                break

    # Add part heading
    h = doc.add_heading(part_title, level=1)

    # Add part subtitle
    sub_p = doc.add_paragraph()
    sub_p.style = doc.styles["Subtitle"]
    sub_p.add_run(part_subtitle)

    # Track state
    first_para_after_heading = False
    in_epilogue = False
    prev_blank = False

    for i in range(start_line, len(lines)):
        line = lines[i]
        stripped = line.strip()

        # Skip blank lines (track them for scene break detection)
        if not stripped:
            prev_blank = True
            continue

        # Horizontal rule → scene break
        if stripped == "---":
            add_scene_break(doc)
            first_para_after_heading = True
            prev_blank = False
            continue

        # Epilogue heading
        if stripped.startswith("# EPILOGUE"):
            # Extract epilogue title
            title = stripped.replace("# ", "")
            # Clean up: EPILOGUE -- "DAYLIGHT" → Epilogue — "Daylight"
            h = doc.add_heading("Epilogue", level=1)
            sub_p = doc.add_paragraph()
            sub_p.style = doc.styles["Subtitle"]
            sub_p.add_run('"Daylight"')
            in_epilogue = True
            first_para_after_heading = True
            prev_blank = False
            continue

        # Part-level heading (# LUMEN — Part ...) — skip since we added our own
        if stripped.startswith("# LUMEN"):
            prev_blank = False
            continue

        # Chapter heading (## Chapter X or ## Interstitial)
        if stripped.startswith("## "):
            title = stripped[3:].strip()
            doc.add_heading(title, level=2)
            first_para_after_heading = True
            prev_blank = False
            continue

        # Sub-heading (### Raven — T-XX:XX or ### A Novel)
        if stripped.startswith("### "):
            title = stripped[4:].strip()
            # Skip "A Novel" subtitle from part1
            if title == "A Novel":
                prev_blank = False
                continue
            doc.add_heading(title, level=3)
            first_para_after_heading = True
            prev_blank = False
            continue

        # Regular paragraph
        add_formatted_paragraph(doc, stripped, is_first_after_heading=first_para_after_heading)
        first_para_after_heading = False
        prev_blank = False


def main():
    doc = Document()
    setup_styles(doc)

    # Title page
    add_title_page(doc)

    # Epigraph page
    add_epigraph_page(doc)

    # Table of Contents
    add_toc(doc)

    # Process each part
    for i, (filename, part_title, part_subtitle) in enumerate(PARTS):
        filepath = os.path.join(SCRIPT_DIR, filename)
        process_markdown_file(
            doc, filepath, part_title, part_subtitle, is_first_part=(i == 0)
        )

    # Save
    output_path = os.path.join(SCRIPT_DIR, "LUMEN.docx")
    doc.save(output_path)
    print(f"Created: {output_path}")

    # Count some stats
    total_paras = len(doc.paragraphs)
    print(f"Total paragraphs: {total_paras}")


if __name__ == "__main__":
    main()
