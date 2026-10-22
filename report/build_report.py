#!/usr/bin/env python3
"""Builds Report.docx. report/make_figures.py must have been run first.

Usage:  .venv/bin/python report/build_report.py [--pages pages.json] [--out Report.docx]
--pages: page numbers for the table of contents (the first pass leaves them at '0'; build_all.sh does two passes).
"""
import argparse
import json
import os
import re

import numpy as np
from docx import Document
from docx.enum.section import WD_ORIENT  # noqa: F401
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT, WD_TAB_LEADER
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
FIGD = os.path.join(HERE, "figures")
ST = json.load(open(os.path.join(HERE, "stats.json")))
EQM = json.load(open(os.path.join(FIGD, "equations.json")))["size"]
DS = np.load(os.path.join(ROOT, "data", "processed", "dataset.npz"))
DEG = 300 / 1023

BLUE, BLUE2, GRAY = RGBColor(0x1F, 0x4E, 0x79), RGBColor(0x2E, 0x75, 0xB6), RGBColor(0x59, 0x59, 0x59)
FONT = "Arial"
TEXT_W = 16.0   # cm (A4 - 2 x 2.5)
TITLE = "Vision-Based Dot Touching with a Leader–Follower Robot Arm — Technical Report"

# document order (fixed up front for cross-references)
FIG_ORDER = ["f01_block", "f04_states", "f05_example", "f06_tracking", "f02_camera", "f03_homography", "f07_dots",
             "f08_duration_voltage", "f09_trajectories", "f10_dot_pose", "f11_redundancy", "f12_models", "f13_predictions",
             "f14_covariance", "f15_surface", "f17_approach", "f16_minjerk", "f18_run", "f19_run_points"]
EQ_ORDER = ["deg", "base", "vfilt", "lead", "goal", "slew", "trackerr", "homog", "dlt_u", "dlt_v", "circ", "moment",
            "fk_r", "fk_c", "phi", "ridge", "pred", "gp", "knn", "loo", "cov", "small", "pre", "mj_j", "mj_s", "mj_v", "mj_q"]


def F(k):
    return FIG_ORDER.index(k) + 1


def E(k):
    return EQ_ORDER.index(k) + 1


def fmt(x, d=1):
    return "%.*f" % (d, x)


def thousands(n):
    return format(n, ",")


# ------------------------------------------------------------------ document base
doc = Document()
sec = doc.sections[0]
sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
sec.left_margin = sec.right_margin = Cm(2.5)
sec.top_margin, sec.bottom_margin = Cm(2.4), Cm(2.2)
sec.different_first_page_header_footer = True

styles = doc.styles


def set_font(style, size=None, bold=None, italic=None, color=None, name=FONT):
    f = style.font
    f.name = name
    rpr = style.element.get_or_add_rPr()
    rf = rpr.find(qn("w:rFonts"))
    if rf is None:
        rf = OxmlElement("w:rFonts"); rpr.append(rf)
    for a in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        rf.set(qn(a), name)
    if size:
        f.size = Pt(size)
    if bold is not None:
        f.bold = bold
    if italic is not None:
        f.italic = italic
    if color is not None:
        f.color.rgb = color


normal = styles["Normal"]
set_font(normal, 10.5)
normal.paragraph_format.space_after = Pt(6)
normal.paragraph_format.line_spacing = 1.2
# document language: English
rpr_default = styles.element.find(qn("w:docDefaults")).find(qn("w:rPrDefault")).find(qn("w:rPr"))
lang = OxmlElement("w:lang"); lang.set(qn("w:val"), "en-US"); lang.set(qn("w:eastAsia"), "en-US"); rpr_default.append(lang)

for name, size, color, before, after in (("Heading 1", 18, BLUE, 0, 10), ("Heading 2", 13.5, BLUE2, 14, 5), ("Heading 3", 11.5, GRAY, 10, 3)):
    s = styles[name]
    set_font(s, size, True, False, color)
    s.paragraph_format.space_before = Pt(before)
    s.paragraph_format.space_after = Pt(after)
    s.paragraph_format.keep_with_next = True
    s.paragraph_format.line_spacing = 1.1
styles["Heading 1"].paragraph_format.page_break_before = True

cap = styles["Caption"]
set_font(cap, 9, False, True, GRAY)
cap.paragraph_format.space_before = Pt(3)
cap.paragraph_format.space_after = Pt(10)
cap.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER

from docx.enum.style import WD_STYLE_TYPE  # noqa: E402
code = styles.add_style("Code", WD_STYLE_TYPE.PARAGRAPH)
set_font(code, 8.5, name="Courier New")
code.paragraph_format.space_after = Pt(0)
code.paragraph_format.line_spacing = 1.0
code.paragraph_format.left_indent = Cm(0.4)
toctitle = styles.add_style("TOCTitle", WD_STYLE_TYPE.PARAGRAPH)
set_font(toctitle, 18, True, False, BLUE)
toctitle.paragraph_format.space_after = Pt(10)

for lvl in ("List Bullet", "List Number"):
    set_font(styles[lvl], 10.5)
    styles[lvl].paragraph_format.space_after = Pt(3)

zoom = doc.settings.element.find(qn("w:zoom"))
if zoom is not None and zoom.get(qn("w:percent")) is None:
    zoom.set(qn("w:percent"), "100")
doc.core_properties.title = TITLE
doc.core_properties.author = ""
doc.core_properties.language = "en-US"


# ------------------------------------------------------------------ helpers
TOKEN = re.compile(r"(</?(?:b|i|c|sub|sup)>)")


def add_runs(p, text, size=None, color=None, bold=None):
    fl = {"b": 0, "i": 0, "c": 0, "sub": 0, "sup": 0}
    for tok in TOKEN.split(text):
        if not tok:
            continue
        m = re.fullmatch(r"<(/?)(b|i|c|sub|sup)>", tok)
        if m:
            fl[m.group(2)] += -1 if m.group(1) else 1
            continue
        r = p.add_run(tok)
        if size:
            r.font.size = Pt(size)
        if color is not None:
            r.font.color.rgb = color
        if bold or fl["b"] > 0:
            r.bold = True
        if fl["i"] > 0:
            r.italic = True
        if fl["sub"] > 0:
            r.font.subscript = True
        if fl["sup"] > 0:
            r.font.superscript = True
        if fl["c"] > 0:
            r.font.name = "Courier New"
            rpr = r._element.get_or_add_rPr()
            rpr.find(qn("w:rFonts")).set(qn("w:hAnsi"), "Courier New")
            r.font.size = Pt((size or 10.5) - 1)
            shd = OxmlElement("w:shd"); shd.set(qn("w:val"), "clear"); shd.set(qn("w:color"), "auto"); shd.set(qn("w:fill"), "F0F0F0")
            rpr.append(shd)
    return p


def P(text, align=None, size=None, space_after=None, keep=False, color=None, italic=False):
    p = doc.add_paragraph()
    add_runs(p, text, size=size, color=color)
    if italic:
        for r in p.runs:
            r.italic = True
    if align == "center":
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    elif align == "justify":
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    if space_after is not None:
        p.paragraph_format.space_after = Pt(space_after)
    if keep:
        p.paragraph_format.keep_with_next = True
    return p


HEADINGS = []   # (level, text)


def H(level, text, newpage=True):
    HEADINGS.append((level, text))
    h = doc.add_heading(text, level=level)
    if level == 1 and not newpage:
        h.paragraph_format.page_break_before = False
        h.paragraph_format.space_before = Pt(22)
    return h


def bullets(items, style="List Bullet"):
    for t in items:
        p = doc.add_paragraph(style=style)
        add_runs(p, t)


def numbered(items):
    # manual numbering so that every list restarts at 1
    for i, t in enumerate(items, 1):
        p = doc.add_paragraph()
        p.paragraph_format.left_indent = Cm(0.9)
        p.paragraph_format.first_line_indent = Cm(-0.6)
        p.paragraph_format.space_after = Pt(3)
        add_runs(p, "<b>%d.</b>  %s" % (i, t))


ORDER_TBLPR = ["tblStyle", "tblpPr", "tblOverlap", "bidiVisual", "tblStyleRowBandSize", "tblStyleColBandSize", "tblW", "jc",
               "tblCellSpacing", "tblInd", "tblBorders", "shd", "tblLayout", "tblCellMar", "tblLook", "tblCaption", "tblDescription"]
ORDER_TCPR = ["cnfStyle", "tcW", "gridSpan", "hMerge", "vMerge", "tcBorders", "shd", "noWrap", "tcMar", "textDirection",
              "tcFitText", "vAlign", "hideMark"]
ORDER_PPR_HEAD = ["pStyle", "keepNext", "keepLines", "pageBreakBefore", "framePr", "widowControl", "numPr", "suppressLineNumbers",
                  "pBdr", "shd", "tabs", "suppressAutoHyphens", "kinsoku", "wordWrap", "overflowPunct", "topLinePunct", "autoSpaceDE",
                  "autoSpaceDN", "bidi", "adjustRightInd", "snapToGrid", "spacing", "ind", "contextualSpacing", "mirrorIndents",
                  "suppressOverlap", "jc", "textDirection", "textAlignment", "textboxTightWrap", "outlineLvl", "divId", "cnfStyle",
                  "rPr", "sectPr", "pPrChange"]


def _local(el):
    return el.tag.split("}")[1]


def place(parent, child, order):
    """Puts child at the position required by the OOXML schema order (replaces an existing element with the same tag)."""
    name = _local(child)
    for el in list(parent):
        if _local(el) == name:
            parent.remove(el)
    idx = order.index(name)
    for el in parent:
        if _local(el) in order and order.index(_local(el)) > idx:
            el.addprevious(child)
            return
    parent.append(child)


def shade(cell, fill):
    tcPr = cell._element.get_or_add_tcPr()
    shd = OxmlElement("w:shd"); shd.set(qn("w:val"), "clear"); shd.set(qn("w:color"), "auto"); shd.set(qn("w:fill"), fill)
    place(tcPr, shd, ORDER_TCPR)


def cell_margins(table, top=50, bottom=50, left=90, right=90):
    tblPr = table._element.tblPr
    m = OxmlElement("w:tblCellMar")
    for k, v in (("top", top), ("left", left), ("bottom", bottom), ("right", right)):
        e = OxmlElement("w:" + k); e.set(qn("w:w"), str(v)); e.set(qn("w:type"), "dxa"); m.append(e)
    place(tblPr, m, ORDER_TBLPR)


def borders(table, color="BFBFBF", sz=4, inside=True):
    tblPr = table._element.tblPr
    b = OxmlElement("w:tblBorders")
    for k in ("top", "left", "bottom", "right") + (("insideH", "insideV") if inside else ()):
        e = OxmlElement("w:" + k); e.set(qn("w:val"), "single"); e.set(qn("w:sz"), str(sz)); e.set(qn("w:space"), "0"); e.set(qn("w:color"), color); b.append(e)
    place(tblPr, b, ORDER_TBLPR)


def no_borders(table):
    tblPr = table._element.tblPr
    b = OxmlElement("w:tblBorders")
    for k in ("top", "left", "bottom", "right", "insideH", "insideV"):
        e = OxmlElement("w:" + k); e.set(qn("w:val"), "nil"); b.append(e)
    place(tblPr, b, ORDER_TBLPR)


TABLES = []


def table(title, headers, rows, widths, align=None, size=9, first_bold=False, zebra=True):
    """title: table caption (above). widths: cm."""
    TABLES.append(title)
    if title:
        p = doc.add_paragraph(style="Caption")
        add_runs(p, "<b>Table %d.</b> %s" % (len(TABLES), title))
        p.paragraph_format.keep_with_next = True
        p.paragraph_format.space_after = Pt(4)
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    t = doc.add_table(rows=1 + len(rows), cols=len(headers))
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    for j, w in enumerate(widths):
        t.columns[j].width = Cm(w)
    borders(t)
    cell_margins(t)
    align = align or ["l"] * len(headers)
    for j, h in enumerate(headers):
        c = t.rows[0].cells[j]
        c.width = Cm(widths[j]); shade(c, "1F4E79"); c.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        p = c.paragraphs[0]; p.paragraph_format.space_after = Pt(0)
        add_runs(p, h, size=size, color=RGBColor(255, 255, 255), bold=True)
        p.alignment = {"l": WD_ALIGN_PARAGRAPH.LEFT, "c": WD_ALIGN_PARAGRAPH.CENTER, "r": WD_ALIGN_PARAGRAPH.RIGHT}[align[j]]
    trPr = t.rows[0]._tr.get_or_add_trPr(); th = OxmlElement("w:tblHeader"); th.set(qn("w:val"), "true"); trPr.append(th)
    for i, row in enumerate(rows):
        tr = t.rows[i + 1]
        trPr = tr._tr.get_or_add_trPr(); cs = OxmlElement("w:cantSplit"); cs.set(qn("w:val"), "true"); trPr.append(cs)
        for j, v in enumerate(row):
            c = tr.cells[j]
            c.width = Cm(widths[j]); c.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            if zebra and i % 2 == 1:
                shade(c, "F3F7FB")
            p = c.paragraphs[0]; p.paragraph_format.space_after = Pt(0); p.paragraph_format.line_spacing = 1.1
            add_runs(p, str(v), size=size, bold=(first_bold and j == 0))
            p.alignment = {"l": WD_ALIGN_PARAGRAPH.LEFT, "c": WD_ALIGN_PARAGRAPH.CENTER, "r": WD_ALIGN_PARAGRAPH.RIGHT}[align[j]]
    if len(rows) <= 12:
        for tr in t.rows[:-1]:
            for c in tr.cells:
                for pp in c.paragraphs:
                    pp.paragraph_format.keep_with_next = True
    sp = doc.add_paragraph(); sp.paragraph_format.space_after = Pt(4)
    return t


FIGS_USED = []


def figure(key, caption, width_cm=15.5):
    n = len(FIGS_USED) + 1
    assert FIG_ORDER[n - 1] == key, "figure order: expected %s, got %s" % (FIG_ORDER[n - 1], key)
    FIGS_USED.append(key)
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.keep_with_next = True
    p.paragraph_format.space_after = Pt(2)
    p.add_run().add_picture(os.path.join(FIGD, key + ".png"), width=Cm(width_cm))
    c = doc.add_paragraph(style="Caption")
    add_runs(c, "<b>Figure %d.</b> %s" % (n, caption))


EQS_USED = []


def eq(key):
    n = len(EQS_USED) + 1
    assert EQ_ORDER[n - 1] == key, "equation order: expected %s, got %s" % (EQ_ORDER[n - 1], key)
    EQS_USED.append(key)
    t = doc.add_table(rows=1, cols=2)
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    no_borders(t)
    w1, w2 = 14.4, 1.6
    t.columns[0].width, t.columns[1].width = Cm(w1), Cm(w2)
    c1, c2 = t.rows[0].cells
    c1.width, c2.width = Cm(w1), Cm(w2)
    for c in (c1, c2):
        c.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    trPr = t.rows[0]._tr.get_or_add_trPr(); cs = OxmlElement("w:cantSplit"); cs.set(qn("w:val"), "true"); trPr.append(cs)
    p = c1.paragraphs[0]; p.alignment = WD_ALIGN_PARAGRAPH.CENTER; p.paragraph_format.space_after = Pt(0)
    m = EQM[key]
    p.add_run().add_picture(os.path.join(FIGD, "eq_%s.png" % key), width=Cm(m["w"] / m["dpi"] * 2.54 * 0.68))
    q = c2.paragraphs[0]; q.alignment = WD_ALIGN_PARAGRAPH.RIGHT; q.paragraph_format.space_after = Pt(0)
    add_runs(q, "(%d)" % n, size=10)
    sp = doc.add_paragraph(); sp.paragraph_format.space_after = Pt(2); sp.paragraph_format.line_spacing = 0.6


def code_block(lines):
    for i, ln in enumerate(lines):
        p = doc.add_paragraph(style="Code")
        r = p.add_run(ln if ln else " ")
        ppr = p._element.get_or_add_pPr()
        shd = OxmlElement("w:shd"); shd.set(qn("w:val"), "clear"); shd.set(qn("w:color"), "auto"); shd.set(qn("w:fill"), "F2F2F2"); place(ppr, shd, ORDER_PPR_HEAD)
    sp = doc.add_paragraph(); sp.paragraph_format.space_after = Pt(4)


def callout(title, text, fill="EAF1F8", bar="2E75B6"):
    t = doc.add_table(rows=1, cols=1)
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    tblPr = t._element.tblPr
    b = OxmlElement("w:tblBorders")
    for k in ("top", "left", "bottom", "right"):
        e = OxmlElement("w:" + k)
        if k == "left":
            e.set(qn("w:val"), "single"); e.set(qn("w:sz"), "24"); e.set(qn("w:color"), bar)
        else:
            e.set(qn("w:val"), "nil")
        b.append(e)
    place(tblPr, b, ORDER_TBLPR)
    cell_margins(t, 90, 90, 160, 120)
    t.columns[0].width = Cm(TEXT_W)
    c = t.rows[0].cells[0]; c.width = Cm(TEXT_W); shade(c, fill)
    trPr = t.rows[0]._tr.get_or_add_trPr(); cs = OxmlElement("w:cantSplit"); cs.set(qn("w:val"), "true"); trPr.append(cs)
    p = c.paragraphs[0]; p.paragraph_format.space_after = Pt(2)
    add_runs(p, "<b>%s</b>" % title, size=10.5, color=BLUE)
    for para in (text if isinstance(text, list) else [text]):
        q = c.add_paragraph(); q.paragraph_format.space_after = Pt(3)
        add_runs(q, para, size=10)
    sp = doc.add_paragraph(); sp.paragraph_format.space_after = Pt(4)


def field_run(p, instr, cached="1", size=9):
    def r(kind=None, text=None, instrtext=None):
        run = p.add_run()
        run.font.size = Pt(size)
        if kind:
            f = OxmlElement("w:fldChar"); f.set(qn("w:fldCharType"), kind); run._element.append(f)
        if instrtext:
            it = OxmlElement("w:instrText"); it.set(qn("xml:space"), "preserve"); it.text = instrtext; run._element.append(it)
        if text:
            run.text = text
        return run
    r("begin"); r(instrtext=instr); r("separate"); r(text=cached); r("end")


# ------------------------------------------------------------------ header/footer
hdr = sec.header.paragraphs[0]
hdr.alignment = WD_ALIGN_PARAGRAPH.RIGHT
add_runs(hdr, TITLE, size=8.5, color=GRAY)
ppr = hdr._element.get_or_add_pPr()
pb = OxmlElement("w:pBdr"); bt = OxmlElement("w:bottom")
for k, v in (("val", "single"), ("sz", "4"), ("space", "4"), ("color", "BFBFBF")):
    bt.set(qn("w:" + k), v)
pb.append(bt); place(ppr, pb, ORDER_PPR_HEAD)
ftr = sec.footer.paragraphs[0]
ftr.alignment = WD_ALIGN_PARAGRAPH.CENTER
add_runs(ftr, "Page ", size=9, color=GRAY)
field_run(ftr, " PAGE ", "1", 9)


# ================================================================== COVER
for _ in range(3):
    doc.add_paragraph()
p = P("<b>Vision-Based Dot Touching</b>", align="center", size=26, color=BLUE, space_after=0)
p = P("<b>with a Leader–Follower Robot Arm</b>", align="center", size=26, color=BLUE, space_after=14)
P("Data collection, modelling and deployment on the real arm", align="center", size=14, color=GRAY, space_after=4)
P("Technical Report", align="center", size=12, color=GRAY, space_after=28)
pp = doc.add_paragraph(); pp.alignment = WD_ALIGN_PARAGRAPH.CENTER
pp.add_run().add_picture(os.path.join(ROOT, "data", "frame_annotated.png"), width=Cm(13.5))
P("ArUco markers at the table corners, the table quadrilateral built with a homography, and the detected white dot.", align="center", size=8.5, color=GRAY, space_after=30, italic=True)
P("Dynamixel AX-12A  ·  OpenCM9.04  ·  OpenCV  ·  Python", align="center", size=11, color=GRAY, space_after=4)
P("October 2026", align="center", size=11, color=GRAY)

# ================================================================== ABSTRACT
H(1, "Abstract")
kept, total = ST["n_used"], ST["n_total"]
rms = ST["rms"]["Polynomial degree 3 (used)"]
kv = ST["covariance"]
P("In this work a second, <b>follower</b> arm was built that copies every motion of a hand-guided <b>leader</b> robot arm "
  "(five Dynamixel AX-12A servos each, OpenCM9.04 controller). Using the leader, the motion of touching a white dot on the table was recorded "
  "%d times; each recording stores the joint positions of the leader and the follower at about 100 Hz and the position of the dot on the table, "
  "measured by a camera looking down from above. The dot position was converted into a table coordinate <i>(u, v)</i> in the range 0–1 using four "
  "ArUco markers placed at the table corners and a <b>homography</b>." % total, align="justify")
P("From the %d episodes left after cleaning, a regression model was trained that maps the dot position to the touch pose. "
  "Leave-one-out (LOO) validation selected degree-3 polynomial ridge regression. The base joint was learned with an error of about %s° (%s units). "
  "Although the individual errors of the shoulder, elbow and wrist joints look large, %s%% of that error was shown to lie in a single direction, "
  "which comes from the <b>redundancy</b> of the three-link arm (the same point can be reached with several poses); the actual error that moves the "
  "end point was found to be about %s°, which corresponds to at most about 1.8 cm for a 34 cm arm." %
  (kept, fmt(rms[0] * DEG), fmt(rms[0]), fmt(kv["share"][0] * 100), fmt(kv["rest_rms_deg"])), align="justify")
P("The predicted poses were executed on the real follower arm with <b>minimum-jerk</b> trajectories (approach, slow descent, lift, return). "
  "%d runs were completed on the real arm; according to the user's observation the arm found the dot every time. However, the results were not "
  "measured in millimetres; this and other open points are listed in the limitations section of this report." % ST["runs"]["ok"], align="justify")

table("Key figures", ["Quantity", "Value"], [
    ["Recorded / used episodes", "%d / %d (5 episodes dropped because they were recorded on a nearly empty battery)" % (total, kept)],
    ["Number of samples used", "%s (≈ 100 Hz)" % thousands(ST["n_samples"])],
    ["Episode duration", "median %s s (%s–%s s)" % (fmt(ST["duration"]["median"]), fmt(ST["duration"]["min"]), fmt(ST["duration"]["max"]))],
    ["Follower–goal difference", "median 2 units, 95%% ≤ %d units (≈ %s°)" % (max(v["p95"] for v in ST["tracking"].values()), fmt(max(v["p95"] for v in ST["tracking"].values()) * DEG))],
    ["LOO error: base", "%s units ≈ %s°" % (fmt(rms[0]), fmt(rms[0] * DEG))],
    ["LOO error: outside the redundancy direction (shoulder–elbow–wrist)", "%s units ≈ %s°" % (fmt(kv["rest_rms_units"]), fmt(kv["rest_rms_deg"]))],
    ["Estimated end-point error (calculated)", "≈ 1–1.8 cm (not measured)"],
    ["Runs completed on the real arm", "%d (%d records in total; %d runs aborted before moving)" % (ST["runs"]["ok"], ST["runs"]["total"], ST["runs"]["aborted"])],
], [6.6, 9.4], align=["l", "l"], first_bold=True)

# ================================================================== TABLE OF CONTENTS (placeholder; filled in later)
TOC_ANCHOR = doc.add_paragraph()   # a style is used instead of H(1, 'Contents')
TOC_ANCHOR.style = styles["TOCTitle"]
TOC_ANCHOR.paragraph_format.page_break_before = True
TOC_ANCHOR.add_run("Contents")
TOC_INSERT_AFTER = TOC_ANCHOR

# ================================================================== 1. INTRODUCTION
H(1, "1. Introduction")
H(2, "1.1 Goal and scope")
P("The goal of the project is to build a <b>leader–follower (teleoperation)</b> robot arm system, to record human demonstrations of a task with it "
  "— touching a white dot on the table — and to derive from these recordings a model that can do the task on its own. The hardware (two arms) "
  "was built by the user from Dynamixel AX-12A servos.", align="justify")
P("Every step is explained in this report together with the mathematics behind it: servo units, leader–follower mapping and latency compensation, "
  "table coordinates from the camera (homography), dot detection, data analysis, regression models and validation, the redundancy of the arm, "
  "minimum-jerk trajectory planning and deployment on the real arm. The appendices give a map of the code and the parameters used.", align="justify")
H(2, "1.2 Overall approach")
numbered([
    "<b>Hardware and firmware:</b> two arms and a leader–follower program running on the OpenCM9.04; data stream, button and PC control mode.",
    "<b>Vision:</b> homography from four ArUco markers at the table corners; detecting the white dot and converting it to table coordinates.",
    "<b>Data collection:</b> the arms go to the home pose, the dot is placed, the leader is guided by hand onto the dot; an episode ends at the moment of touch.",
    "<b>Analysis and model:</b> cleaning the episodes, extracting the touch pose, (u, v) → pose regression and validation.",
    "<b>Deployment:</b> moving to the predicted pose with a minimum-jerk trajectory and touching; safety layers.",
])
callout("Design decision: why a simple model?", [
    "The task is <b>single-step and static</b>: the dot does not move and the arm only has to reach it. In that case, instead of imitating the "
    "whole trajectory, it is enough to predict the <i>final pose</i> from the dot position and to draw the path in between ourselves (minimum-jerk). "
    "With 114 samples, a degree-3 polynomial gave better results than a neural network (Section 8)."])

# ================================================================== 2. SYSTEM
H(1, "2. System architecture")
H(2, "2.1 Hardware")
table("Hardware components", ["Component", "Details"], [
    ["Robot arms", "2 arms with 5 axes each: base, shoulder, elbow, wrist, roll (wrist rotation). 1 × Dynamixel AX-12A per joint"],
    ["Controller", "OpenCM9.04 (STM32 based); Dynamixel Protocol 1.0; 1 Mbps; both arms on the same bus (unique IDs)"],
    ["Camera", "Phone (Iriun Webcam), looking down at the table, 1280 × 720"],
    ["Table frame", "4 ArUco markers at the table corners (DICT_4X4_50, ID 0–3), about 5 cm"],
    ["Computer", "Ubuntu, Python 3.12; OpenCV 4.6; NVIDIA GTX 1650 Ti (4 GB) — training was done on the CPU"],
    ["Power", "Battery (12 V class); a 12 V 30 A power supply was also found while this report was written"],
    ["Button", "User button on the OpenCM; pin 16 for an external button (pin → GND)"],
], [3.6, 12.4], first_bold=True)
table("Servo IDs (bottom to top)", ["", "Base", "Shoulder", "Elbow", "Wrist", "Roll"], [
    ["Leader (moved by hand, torque off)", "13", "17", "14", "9", "10"],
    ["Follower (follows the leader)", "11", "18", "16", "12", "8"],
], [6.0, 2.0, 2.0, 2.0, 2.0, 2.0], align=["l", "c", "c", "c", "c", "c"], first_bold=True)

H(2, "2.2 Software components and data flow")
figure("f01_block", "System block diagram. The camera image goes to the PC; the PC talks to the OpenCM9.04 over a USB serial link; the OpenCM drives "
       "both arms over a shared Dynamixel bus.", 14.8)
P("The system has three layers:", keep=True)
bullets([
    "<b>OpenCM9.04</b> (<c>arm_rec.ino</c>): reads and drives the servos, performs the leader–follower mapping, applies the safety checks, "
    "streams data to the PC and handles commands from the PC.",
    "<b>PC-side Python:</b> <c>robot/opencm.py</c> reads the serial stream and sends commands; <c>vision/</c> does the homography and dot detection; "
    "<c>tools/record.py</c> records the data; <c>train/</c> does analysis and modelling; <c>tools/run_model.py</c> runs the model on the real arm.",
    "<b>Helper tools:</b> <c>servo_lab.ino</c> (a console for inspecting individual servos: scan, info, live monitoring, joint pairing, home/limit settings) "
    "and <c>tools/joint_range.py</c> (joint range and dead-zone check).",
])
P("The OpenCM sends one data line per loop and the PC reads it with a background thread:", keep=True)
table("Serial protocol", ["Direction", "Line", "Meaning"], [
    ["OpenCM → PC", "<c>D,t_ms,state,l1..l5,g1..g5,f1..f5,y1..y5,volt,tmax,tid</c>", "leader position, goal sent to the follower, follower position, follower load, lowest voltage, hottest servo (temperature, ID)"],
    ["OpenCM → PC", "<c>E,B</c>", "button pressed"],
    ["OpenCM → PC", "<c># …</c>", "message for humans"],
    ["PC → OpenCM", "<c>h g p x q r ?</c>", "home, follow, PC mode, stop, torque off, stream on/off, status"],
    ["PC → OpenCM", "<c>G,a,b,c,d,e</c>", "follower goals in PC mode (0–1023)"],
], [2.6, 5.9, 7.5], size=8.5)
P("The data line is streamed over 115200 baud USB serial (the OpenCM's USB is native, so this rate is not a bottleneck). The loop rate is set by "
  "the read time of the Dynamixel bus; during data collection <b>≈ 100 Hz</b> was measured (median interval between samples 9.1 ms, 95% within 14.8 ms).", align="justify")

# ================================================================== 3. SERVO
H(1, "3. Servo basics")
H(2, "3.1 Units")
P("The position register of the AX-12A ranges from 0 to 1023, which corresponds to 0–300°:", keep=True)
eq("deg")
P("So one unit is about 0.293°; 512 = 150° (the middle of the servo). All joint values in this report are in these <b>servo units</b>. "
  "Multiply by 0.293 to convert units to degrees.", align="justify")
table("AX-12A quantities", ["Quantity", "Range / unit", "Note"], [
    ["Position", "0–1023 ↔ 0–300°", "1 unit ≈ 0.293°"],
    ["Speed (moving speed)", "1 unit ≈ 0.111 rpm", "<b>0 = unlimited speed</b>; 0 is never written"],
    ["Load", "10-bit magnitude + direction bit", "converted to a signed value (−1023…1023)"],
    ["Voltage", "0.1 V units", "120 = 12.0 V; operating range 9–12 V"],
    ["Temperature", "°C", "the servo's own limit is 70 °C"],
], [3.8, 4.4, 7.8], size=9, first_bold=True)
H(2, "3.2 Dead zone")
P("The position sensor reads 0–300°; 300° to 360° (i.e. between 1023 and 0) is a <b>dead zone</b>: the position cannot be read and the servo "
  "cannot be controlled there. If a joint's working range in the task gets close to 0 or 1023, the reading jumps suddenly and the arm moves "
  "unexpectedly. Therefore (i) the home pose was chosen so that the joints are near the middle of the servo range (512); (ii) the firmware treats "
  "jumps larger than 100 units in one loop as invalid; (iii) the tools flag values closer than 30 units to 0 or 1023. "
  "The real fix for a joint that enters the dead zone is to rotate the servo/horn mounting.", align="justify")
H(2, "3.3 Power and temperature")
P("The AX-12A operates between 9 and 12 V. The most important practical lesson of the project was that <b>the power supply determines data quality</b>: "
  "in the first five episodes, recorded while the battery was nearly empty, the lowest servo voltage dropped to about 6.5 V; at this voltage some servos "
  "stopped responding to commands (for example servo 14) and the system behaved unreliably. These five episodes were removed from the data set "
  "(Figure %d). For the same reason, the recorder continuously monitors the voltage and the temperature of the hottest servo (Section 6)." % F("f08_duration_voltage"), align="justify")

# ================================================================== 4. CONTROL
H(1, "4. Leader–follower control")
P("The leader arm is moved by hand (its torque is off); on every loop the firmware reads the leader's joint positions and sends them to the follower as goals. "
  "This section describes the mathematics of the mapping, the latency compensation and the safety checks.", align="justify")
H(2, "4.1 Joint mapping")
P("For the leader position <i>l<sub>i</sub></i> of joint i, the follower counterpart is computed as:", keep=True)
eq("base")
P("Here <i>H<sup>L</sup><sub>i</sub></i> and <i>H<sup>F</sup><sub>i</sub></i> are the <b>home</b> values of the two arms, and <i>s<sub>i</sub></i> ∈ {+1, −1} is the mounting "
  "direction sign of the two servos (current: {+1, +1, −1, −1, +1}). The mapping is <b>relative</b>: the follower deviates by as much as the leader deviates "
  "from home. Therefore the absolute offsets of the two arms need not be equal and there is no separate calibration step; the home pose is the "
  "“same pose” reference of both arms.", align="justify")
table("Home values (servo units)", ["", "Base", "Shoulder", "Elbow", "Wrist", "Roll"], [
    ["Leader  H<sup>L</sup>", "512", "512", "512", "512", "665"],
    ["Follower  H<sup>F</sup>", "512", "512", "512", "512", "365"],
    ["Follower safe range  m…M", "100…923", "100…923", "100…923", "100…923", "100…923"],
], [5.2, 2.2, 2.2, 2.2, 2.2, 2.0], align=["l", "c", "c", "c", "c", "c"], first_bold=True)
P("The home pose was chosen with the arms standing upright; the roll has an offset of 45° (≈ 153 units) (512 + 153 = 665 on the leader, 512 − 147 = 365 on the follower).", align="justify")
H(2, "4.2 Latency compensation")
P("The servo cannot follow a command instantly; there is a latency of about 60 ms. To reduce it, the goal is shifted slightly ahead according to the "
  "leader's velocity. First the velocity of the goal is estimated with a low-pass filter:", keep=True)
eq("vfilt")
P("Here <i>α</i> = 0.3 is the filter coefficient and Δ<i>t</i> is the loop period. The lead term and the final goal:", keep=True)
eq("lead")
eq("goal")
P("<i>T<sub>ℓ</sub></i> = 60 ms and <i>ℓ<sub>max</sub></i> = 30 units (≈ 8.8°). Since the velocity estimate is based on a difference, the 1-unit "
  "quantization of the servo position amplifies noise: a 1-unit difference in 10 ms is 100 units/s, i.e. a 6-unit lead offset. "
  "This is why the goal of the roll joint jitters by ±5 units (Figure %d)." % F("f05_example"), align="justify")
H(2, "4.3 Slew-rate limit")
P("The goal itself cannot jump instantly; on every loop it advances by at most <i>R</i>Δ<i>t</i>:", keep=True)
eq("slew")
table("Slew-rate limit R by mode", ["Mode", "R [units/s]", "Angular rate", "Purpose"], [
    ["Sync (start)", "40", "≈ 12 °/s", "the follower approaches the leader slowly; following starts once the error is ≤ 6 units on all joints"],
    ["Follow (teleop)", "1200", "≈ 352 °/s", "only a protection against corrupt reads"],
    ["PC mode (model)", "300", "≈ 88 °/s", "upper bound for the goals sent by the model"],
], [3.6, 2.2, 2.4, 7.8], align=["l", "c", "c", "l"], first_bold=True)
H(2, "4.4 State machine")
figure("f04_states", "Firmware state machine. The numbers in parentheses are the state codes in the data line.", 14.8)
P("The normal flow is <b>WAIT → HOMING → READY</b>. In READY both arms are held at home with torque. During data collection <c>g</c> releases the "
  "leader and starts following; when running the model, <c>p</c> enables PC mode. On any error the follower goes into the <b>HOLD</b> state "
  "(holds its current position with torque on).", align="justify")
H(2, "4.5 Homing")
P("When the home command arrives, every servo first gets torque at its current position (to avoid a jump), then the goal is set to home and the "
  "servo's own speed limit (80 units ≈ 8.9 rpm) moves both arms slowly. Once |position − home| ≤ 10 units on all joints the state becomes READY; "
  "if home is not reached within 10 s the state falls back to HOLD.", align="justify")
H(2, "4.6 Safety mechanisms")
eq("trackerr")
table("Firmware safety checks", ["Check", "Threshold", "Result"], [
    ["Tracking error |g − f|", "> 80 units for > 500 ms", "follower freezes (HOLD)"],
    ["Leader jump (dead zone)", "> 100 units in one loop", "that reading is rejected; HOLD after 10 consecutive errors"],
    ["Read error", "10 consecutive failures", "HOLD"],
    ["No commands from the PC (PC mode)", "300 ms", "HOLD"],
    ["Voltage", "< 6.4 V", "torque off on both arms, program stops"],
    ["Temperature", "> 60 °C", "torque off on both arms, program stops"],
], [5.4, 4.6, 6.0], first_bold=True)
H(2, "4.7 Tracking performance")
P("How well the follower tracks its goal can be measured directly from the recorded data. Figure %d shows the leader (converted to follower units), "
  "the goal sent and the measured follower position in one episode; Figure %d gives the distribution of |goal − measured position| over all episodes." % (F("f05_example"), F("f06_tracking")), align="justify")
figure("f05_example", "Leader, goal and follower in an example episode (%s). Leader values are converted to follower units: <i>F</i><sub>0</sub> + <i>s</i>·(<i>l</i> − <i>L</i><sub>0</sub>). "
       "The jitter in the roll panel is quantization noise in the velocity estimate." % ST["example"], 14.2)
figure("f06_tracking", "Cumulative distribution (CDF) of the absolute difference between the goal and the measured follower position over all used episodes.", 12.5)
tk = ST["tracking"]
table("Goal – measured position difference (servo units), %s samples" % thousands(ST["n_samples"]), ["Joint", "Median", "95%", "99%", "Max"],
      [[j, fmt(v["p50"], 0), fmt(v["p95"], 0), fmt(v["p99"], 0), fmt(v["max"], 0)] for j, v in tk.items()],
      [4.0, 3.0, 3.0, 3.0, 3.0], align=["l", "c", "c", "c", "c"], first_bold=True)
P("The median difference is 1–2 units (≈ 0.3–0.6°) and the 95th percentile is 4–6 units (≈ 1.2–1.8°). So the follower tracks the leader closely and "
  "<b>the recorded follower position is the motion that was actually performed</b>. Since the median goal–position difference at the moment of touch is "
  "also 0–2 units, the measured positions the model uses as targets and the commanded goals are practically equivalent.", align="justify")

# ================================================================== 5. VISION
H(1, "5. Image processing")
P("The position of the dot on the table is measured from the camera only, independently of the coordinates the arm uses. The camera sees the "
  "table from above but at an angle; a pixel position is not directly a table position. Therefore a transformation is first built from the "
  "markers at the table corners.", align="justify")
H(2, "5.1 Table frame: ArUco markers")
P("ArUco markers (4×4-bit dictionary, ID 0–3) are stuck on the four corners of the table: ID0 top left, ID1 top right, ID2 bottom right, ID3 bottom left "
  "(as seen from the camera). The marker corners are found with OpenCV and the <b>center</b> of each marker (the mean of its 4 corners) is used. If the arm "
  "partly covers a marker, its last known center is used; the table is defined once all four markers have been seen at least once. Because the markers "
  "are detected again in every frame, the transformation updates itself if the camera shifts slightly.", align="justify")
figure("f02_camera", "Camera image. (a) markers, the table quadrilateral formed by the marker centers, and the detected dot; (b) mask after the white threshold and morphological opening "
       "(arm parts and the white parts of the markers also pass; they are removed by the area and circularity filters).", 15.5)
H(2, "5.2 Homography: pixel → table coordinates")
P("The relation between two views of points on a plane is expressed by a <b>homography</b>. The pixel coordinate <i>(x, y)</i> in homogeneous form is "
  "multiplied by a 3×3 matrix <b>H</b> and divided by the last component:", keep=True)
eq("homog")
P("<b>H</b> is only defined up to scale, so it has 8 free parameters (<i>h</i><sub>33</sub> = 1). Each point correspondence gives two linear equations; "
  "moving the denominator to the other side:", keep=True)
eq("dlt_u")
eq("dlt_v")
P("Four correspondences (four marker centers) give 8 equations and the linear system in 8 unknowns is solved exactly (<c>cv2.getPerspectiveTransform</c>). "
  "The marker centers are mapped to the corners of the unit square: ID0 → (0,0), ID1 → (1,0), ID2 → (1,1), ID3 → (0,1). The result is the <i>(u, v)</i> "
  "coordinate of the dot: <i>u</i> runs left to right and <i>v</i> top to bottom, both in 0–1 (Figure %d)." % F("f03_homography"), align="justify")
figure("f03_homography", "Homography: the (skewed) quadrilateral in the camera image is mapped to the unit square using the marker centers.", 14.2)
callout("Assumptions and limits", [
    "• The dot lies in the plane defined by the marker centers (the table surface); if it is raised above the surface the homography is wrong.",
    "• The coordinate is <b>dimensionless</b>: the 0–1 range is scaled by the distance between the marker centers, not millimetres.",
    "• Lens distortion is not corrected.",
    "• If two of the four markers are covered, the last known centers are used; the markers must be stuck firmly to the table."])
H(2, "5.3 White dot detection")
P("The dot is found in the following order:", keep=True)
numbered([
    "The image is blurred with a 5×5 Gaussian filter and converted to HSV.",
    "<b>White threshold:</b> white means low saturation and high brightness: <i>S</i> ≤ 60 and <i>V</i> ≥ 170.",
    "Only the inside of the table quadrilateral is considered (cuts out the effect of the arm and the area outside the table).",
    "<b>Morphological opening</b> (7×7 elliptical structuring element): removes thin table lines and noise.",
    "Area <i>A</i> and <b>circularity</b> are computed for each component; those with 300 ≤ <i>A</i> ≤ 8000 pixels and <i>C</i> ≥ 0.70 are candidates.",
    "The centroid of the largest candidate is taken and converted to table coordinates with the homography.",
])
eq("circ")
P("Circularity equals 1 for a perfect circle (<i>A</i> area, <i>P</i> perimeter). Arm parts and cables are long and irregular and score low, so white but "
  "non-round objects are rejected. The center is found from the geometric moments of the component (sub-pixel precision):", keep=True)
eq("moment")
table("Dot detection parameters", ["Parameter", "Value"], [
    ["White threshold", "S ≤ 60, V ≥ 170 (HSV)"],
    ["Morphological opening", "7 × 7 ellipse"],
    ["Area range", "300–8000 pixels"],
    ["Circularity", "C ≥ 0.70"],
    ["Stability", "median of the last 15 frames; (u, v) standard deviation < 0.01"],
], [5.0, 11.0], first_bold=True)
H(2, "5.4 Stability and error sources")
P("During data collection and when running the model, the <b>median of the last 15 frames</b> is used instead of a single frame; if the (u, v) standard "
  "deviation is above 0.01 the dot counts as “unstable” and no episode is started. The main error sources are lighting changes (which affect the threshold), "
  "the dot being covered by the arm or a hand, markers shifting, and the table-plane assumption.", align="justify")

# ================================================================== 6. DATA COLLECTION
H(1, "6. Data collection")
H(2, "6.1 Episode cycle")
P("The data was collected with <c>tools/record.py</c>. An episode consists of the following steps:", keep=True)
numbered([
    "<b>Home:</b> both arms move slowly to the upright pose; the leader is also held with torque so it does not fall over. Meanwhile the dot is placed at a new position on the table.",
    "<b>Start (SPACE or button):</b> the program measures the dot with the camera and stores it; the leader is released, the follower follows it, recording starts. "
    "Starting is blocked if the dot is not visible/unstable or a voltage or temperature threshold is exceeded.",
    "<b>Touch:</b> the leader is guided by hand to touch the dot lightly.",
    "<b>Stop (SPACE/button):</b> recording ends and both arms return home. Because <b>recording ends at the moment of touch</b>, the last sample of each episode is the touch pose; "
    "the return motion is not recorded.",
    "<b>Confirm:</b> ENTER/button saves, <c>d</c> discards. Then the cycle starts again.",
])
P("On screen the table is divided into 4×4 cells and each cell shows how many episodes were collected in that region; this way uncovered regions (red 0) can be seen "
  "and the dot placed there. The battery voltage and the temperature of the hottest servo are shown as well.", align="justify")
table("Voltage and temperature thresholds (recorder)", ["Quantity", "Warning (yellow)", "No new episode (red)", "Firmware emergency stop"], [
    ["Battery voltage", "< 11.0 V", "< 10.5 V", "< 6.4 V"],
    ["Hottest servo", "≥ 50 °C", "≥ 55 °C", "> 60 °C"],
], [4.0, 3.4, 5.0, 3.6], align=["l", "c", "c", "c"], first_bold=True)
H(2, "6.2 Recorded files")
table("Files per episode (<c>data/episodes/ep_NNNN/</c>)", ["File", "Contents"], [
    ["<c>data.npz</c>", "<c>t_pc, t_ms, state, leader, goal, follower, load, volt, temp, frame_t</c> (≈ 100 Hz)"],
    ["<c>video.mp4</c>", "raw camera footage; matched to the serial data via <c>frame_t</c>"],
    ["<c>meta.json</c>", "dot (u, v) and pixel position, homography <b>H</b>, marker positions, duration, start/end voltage and temperature"],
], [3.6, 12.4], first_bold=True)
P("Serial data and video are matched using the PC clock (<c>t_pc</c> and <c>frame_t</c>). Since the dot position is constant during an episode it is measured "
  "once at the start (before the arm covers the dot).", align="justify")
H(2, "6.3 Collected data")
table("Summary of the collected data", ["Quantity", "Value"], [
    ["Recorded episodes", str(total)],
    ["Used episodes", "%d (the first 5 were dropped because the voltage was < 9 V)" % kept],
    ["Used samples", thousands(ST["n_samples"])],
    ["Sampling", "≈ 100 Hz"],
    ["Episode duration", "median %s s; %s–%s s" % (fmt(ST["duration"]["median"]), fmt(ST["duration"]["min"]), fmt(ST["duration"]["max"]))],
    ["Lowest voltage per episode (used ones)", "%s–%s V (median %s V)" % (fmt(ST["voltage_used"][0]), fmt(ST["voltage_used"][2]), fmt(ST["voltage_used"][1]))],
    ["Dot range", "u: %s–%s,  v: %s–%s" % (fmt(ST["uv_range"]["u"][0], 2), fmt(ST["uv_range"]["u"][1], 2), fmt(ST["uv_range"]["v"][0], 2), fmt(ST["uv_range"]["v"][1], 2))],
    ["Disk size", "≈ 0.98 GB (mostly video)"],
], [6.0, 10.0], first_bold=True)
figure("f07_dots", "(a) Touched dots, drawn on the camera frame of one episode with the inverse homography. (b) The same dots in table coordinates with 4×4 cell counts. "
       "The dots cover the reach area of the arm (on the left); the right half is out of reach.", 15.5)
figure("f08_duration_voltage", "(a) Episode durations. (b) Lowest servo voltage measured in each episode; the first five episodes were recorded on a nearly empty battery and dropped.", 14.5)
P("The dots are spread fairly evenly over the reach area in the left half: in the 4×4 count the middle cells contain 18–25 episodes and the edge cells 1–4. "
  "This means the model will be better in the middle than at the edges.", align="justify")
H(2, "6.4 Cleaning criteria")
table("Episode drop criteria (<c>train/prepare.py</c>)", ["Criterion", "Threshold"], [
    ["Lowest voltage", "dropped if < 9.0 V"],
    ["Episode with a warning", "dropped if the follower froze during recording"],
    ["Duration", "dropped if outside 4–40 s"],
    ["Motion at the touch point", "dropped if the joint standard deviation over the last 0.3 s is > 6 units"],
], [6.0, 10.0], first_bold=True)

# ================================================================== 7. ANALYSIS
H(1, "7. Data analysis")
H(2, "7.1 Touch and approach poses")
P("Because an episode ends at the moment of touch, the <b>touch pose</b> is computed from the last samples of the episode (the median is robust to noise):", align="justify")
code_block(["touch_pose = median( follower position , last 0.3 s )            # 5 joints",
            "pre_pose   = median( follower position , 3.0 s before the touch )",
            "pre_delta  = median( pre_pose − touch_pose )   (all episodes)   # a single vector"])
P("The start pose is very similar in all episodes (standard deviation below 1 unit); this shows that the home system works consistently.", align="justify")
H(2, "7.2 Joint trajectories")
figure("f09_trajectories", "Follower joint trajectories of all episodes in normalized time (0 = start, 1 = touch). The orange line is the median.", 15.5)
bullets([
    "The elbow and wrist move first (10–40% of normalized time), the shoulder later; the timing of the motions varies noticeably between episodes.",
    "The motion of the base joint splits into two directions depending on the dot (dot on the left or on the right).",
    "The roll trajectories form an almost flat line.",
])
P("This large variability in timing is the reason for predicting <b>only the final pose</b> and drawing the path in between ourselves, instead of imitating the trajectory exactly.", align="justify")
H(2, "7.3 Dot position and touch pose")
figure("f10_dot_pose", "Relation between the touch pose and the dot coordinate (u: top row, v: bottom row). Color shows the other coordinate. r in the title is the Pearson correlation.", 15.5)
kor = ST["correlation"]
table("Pearson correlation (touch pose — dot coordinate)", ["Joint", "with u", "with v"], [
    [j, fmt(kor["%s~u" % j], 2), fmt(kor["%s~v" % j], 2)] for j in ("Base", "Shoulder", "Elbow", "Wrist")
], [4.0, 3.0, 3.0], align=["l", "c", "c"], first_bold=True)
P("The measured relations agree with the geometry of the arm: the <b>base</b> joint determines which direction the arm turns to and has an almost linear relation "
  "with <i>v</i> (r = %s). The <b>shoulder</b> and <b>elbow</b> determine how far the arm reaches and are strongly related to <i>u</i> (r = %s and %s). "
  "The <b>wrist</b> joint is only weakly related to the dot position and is scattered; the reason is the redundancy explained in the next section." %
  (fmt(kor["Base~v"], 2), fmt(kor["Shoulder~u"], 2), fmt(kor["Elbow~u"], 2)), align="justify")
H(2, "7.4 The roll joint is constant")
P("The roll (wrist rotation) plays no role in this task: its value in the touch poses stays around the home value (365) in nearly every episode; the deviations are "
  "small wobbles from involuntary hand motion. Therefore roll is not part of the model and is kept constant at its home value.", align="justify")

# ================================================================== 8. MODELLING
H(1, "8. Modelling")
H(2, "8.1 Problem definition")
P("The input is the table coordinate of the dot <i>(u, v)</i>; the output is the four joint angles at the moment of touch, <b>q</b> = (<i>q</i><sub>base</sub>, <i>q</i><sub>shoulder</sub>, <i>q</i><sub>elbow</sub>, <i>q</i><sub>wrist</sub>) "
  "(in servo units). Roll is kept constant. The training set is 114 pairs from 114 episodes: {(<i>u<sub>i</sub></i>, <i>v<sub>i</sub></i>) → <b>q</b><sub><i>i</i></sub>}.", align="justify")
H(2, "8.2 Redundancy of the arm")
P("The end point of a planar three-link arm (shoulder, elbow, wrist) touching the table is written in terms of the joint angles <i>θ</i><sub>1…3</sub> and the link lengths <i>l</i><sub>1…3</sub> as:", keep=True)
eq("fk_r")
P("Here <i>r</i> is the horizontal distance of the end point from the base, <i>z</i> its height and <i>φ<sub>k</sub></i> the absolute angle of link k. Touching a point on the table "
  "means <b>two conditions</b>: the distance must equal the target value and the height must be the table surface. There are two equations for three unknowns (<i>θ</i><sub>1…3</sub>):", keep=True)
eq("fk_c")
P("The null space of the Jacobian is 3 − 2 = <b>1-dimensional</b>: there are infinitely many poses that reach the same point; they are all distinguished by a single parameter "
  "(for example the approach angle of the last link) (Figure %d). Since we lowered the arm onto the dot holding the wrist slightly differently each time, the shoulder–elbow–wrist "
  "combination varies between episodes while the end point lands in the same place." % F("f11_redundancy"), align="justify")
figure("f11_redundancy", "Redundancy (schematic; the link lengths are not those of the real arm): two poses reaching the same touch point with two different angles of the last link.", 10.5)
callout("Consequence", "For a given <i>(u, v)</i> the correct poses form a <b>set</b>, not a single point. A single-valued regression model predicts the “mean” of this set. "
        "If the set is roughly a straight line, the mean stays inside the set and is a valid pose; if the set is curved, the mean may be invalid. "
        "The analysis below shows that the set is approximately linear within this data range.")
H(2, "8.3 Candidate models")
P("<b>Polynomial ridge regression</b> (the model used). Degree-3 polynomial feature vector (10 features):", keep=True)
eq("phi")
P("The features are stacked into a matrix <b>X</b> (one row per episode) and the targets into <b>Y</b>. The ridge (Tikhonov) solution is in closed form:", keep=True)
eq("ridge")
eq("pred")
P("<i>λ</i> = 10<sup>−3</sup> keeps the solution stable by damping the near-linear dependence between the features. <b>W</b> is a 10×4 weight matrix; "
  "the whole model consists of 40 numbers.", align="justify")
P("<b>GP / RBF kernel ridge</b> — in addition to a linear base, it models the residuals with a kernel-based function:", keep=True)
eq("gp")
P("<i>ℓ</i> = 0.4 (length scale) and a regularization of 0.01 were chosen. <b>k-nearest neighbours</b> (<i>k</i> = 5):", keep=True)
eq("knn")
P("<b>MLP (neural network)</b>: 2 hidden layers × 64 neurons, tanh activation, AdamW (learning rate 3·10<sup>−3</sup>, weight decay 10<sup>−4</sup>), 1500 iterations, standardized targets.", align="justify")
H(2, "8.4 Validation: leave-one-out (LOO)")
P("Because the training set is small, <b>LOO</b> was used instead of a standard train/test split: each time one episode is held out for testing, the model is trained on the "
  "remaining 113 episodes and the held-out episode is predicted. Doing this for all episodes means each episode is predicted as an “unseen” point. "
  "The error measure is the root-mean-square (RMS) error per joint; <i>q̂</i><sup>(−i)</sup> is the prediction of the model trained with episode i left out:", keep=True)
eq("loo")
P("For the MLP, 10-fold cross-validation was used instead of LOO because training is expensive. The “no model” reference predicts the mean pose for every episode; "
  "its RMS equals the standard deviation of that joint.", align="justify")
H(2, "8.5 Results")
names = ["Mean pose (reference)", "Polynomial degree 1", "Polynomial degree 2", "Polynomial degree 3 (used)", "GP / RBF", "k-nearest neighbours (k=5)", "MLP 2×64 (10-fold)"]
rows = []
for n in names:
    v = ST["rms"][n]
    rows.append([("<b>%s</b>" % n) if "used" in n else n] + ["%s" % fmt(x) for x in v])
table("LOO error, RMS (servo units; 1 unit ≈ 0.293°)", ["Model", "Base", "Shoulder", "Elbow", "Wrist"], rows, [6.6, 2.35, 2.35, 2.35, 2.35], align=["l", "c", "c", "c", "c"])
figure("f12_models", "LOO errors of the models (lower is better). The reference is the error of predicting the mean pose.", 14.5)
figure("f13_predictions", "Degree-3 polynomial: LOO prediction versus the actual touch pose (dashed line: perfect fit).", 15.5)
bullets([
    "The <b>base</b> joint was learned very well with an error of about %s units (≈ %s°); a 15-fold improvement over the reference." % (fmt(rms[0]), fmt(rms[0] * DEG)),
    "The <b>shoulder and elbow</b> errors dropped to roughly a half to a third of the reference.",
    "The <b>wrist</b> joint barely improved; the reason is the redundancy shown in the next section, not a shortcoming of the model.",
    "The polynomial (degree 3) and the GP gave almost the same result; k-nearest neighbours was slightly behind. The <b>MLP did worst</b>: 114 samples are too few for a neural network.",
])
P("In practice the <b>degree-3 polynomial ridge</b> was used: as accurate as the GP, much simpler (40 numbers in total), solved in closed form, and it needs only one "
  "matrix product at run time.", align="justify")
H(2, "8.6 Error structure analysis")
P("Per-joint error is misleading because of redundancy: the shoulder, elbow and wrist compensate for each other. To see this, the covariance of the LOO error vectors of these "
  "three joints was examined; since <b>C</b> is symmetric positive semi-definite, its eigendecomposition shows in which directions the error is concentrated:", keep=True)
eq("cov")
figure("f14_covariance", "Eigen-analysis of the LOO error vector (shoulder, elbow, wrist). (a) share of the error variance in each direction; (b) components of the largest direction.", 14.5)
P("The result is striking: <b>%s%%</b> of the error variance lies in a single direction (shoulder, elbow, wrist components ≈ (%s, %s, %s)). This direction is the "
  "<b>redundancy direction</b> that moves the joints together without changing the end point, i.e. the wrist posture the user chose differently each time. "
  "The error in the remaining two directions is small (standard deviations ≈ %s and %s units); together they give <b>%s units ≈ %s°</b> RMS error. "
  "This is the error that actually moves the end point." % (fmt(kv["share"][0] * 100), fmt(kv["dir1"][0], 2), fmt(kv["dir1"][1], 2), fmt(kv["dir1"][2], 2),
                                                              fmt(kv["std"][1]), fmt(kv["std"][2]), fmt(kv["rest_rms_units"]), fmt(kv["rest_rms_deg"])), align="justify")
H(2, "8.7 Estimating the end-point error")
P("With the small-angle approximation, an error <i>δθ</i> at a joint shifts the end point by about <i>L</i>·<i>δθ</i>. The length of the arm from the base to the tip is about 34 cm "
  "(measured, not from the joint center); in the worst case the whole error acts at the full distance of the tip:", keep=True)
eq("small")
P("In reality part of the error is in the near joints and acts with a shorter lever than the whole arm; the base error (≈ %s°) corresponds to ≈ 0.4 cm at a radius of about 25 cm. "
  "The expected end-point error is therefore around <b>≈ 1–1.5 cm</b>. The diameter of the dot was estimated from the image as about 2.5–3 cm; "
  "so the error is small enough to land inside the dot in most attempts. The 0.5 cm protrusion of the screw tip is not added separately, because the touch poses "
  "were recorded with the screw tip in contact." % fmt(rms[0] * DEG), align="justify")
callout("This is an estimate, not a measurement", "The error in centimetres was calculated geometrically from the angle error. The distance of the end point from the dot was not measured on the real arm (see Section 11).", fill="FFF4E5", bar="D9822B")
H(2, "8.8 Model surface")
figure("f15_surface", "Prediction of the selected model over the table (u, v → joint). White dots are training points; regions farther than 0.12 from the nearest training point are considered unreliable and hidden.", 15.5)
P("The prediction surfaces look smooth and monotonic; this shows that the polynomial model does not overfit the data. Outside the training points (especially with a degree-3 "
  "polynomial) predictions can degrade quickly; this is why the run script rejects dots farther than 0.12 table units from the nearest training point.", align="justify")

# ================================================================== 9. TRAJECTORY
H(1, "9. Trajectory planning")
H(2, "9.1 Approach pose")
P("No clear increase in load or in the goal–position difference was observed at the moment of touch (the touch was very light); therefore lowering the arm by <i>pressing</i> it "
  "onto the table and detecting contact from the load is not reliable. Instead, the human approach is imitated: the arm first goes to an <b>approach pose</b> just above "
  "the table and then descends slowly to the touch pose:", keep=True)
eq("pre")
delta = DS["pre_delta"][:4]
P("Here <b>δ</b> is the per-joint median over all episodes of the difference “pose 3 s before the touch − touch pose”: ≈ (%s) units (base, shoulder, elbow, wrist). "
  "3 s was chosen as the lead time: 1 s before the touch the pose difference is ≈ 1 unit (the arm is already still), 3 s before ≈ 10 units (≈ 3°, roughly 1.5–2 cm above), "
  "4 s before ≈ 20 units (Figure %d)." % (", ".join(fmt(x, 0) for x in delta), F("f17_approach")), align="justify")
figure("f17_approach", "Difference of the follower pose from the touch pose (largest joint) as a function of the time before the touch. The dashed line marks the chosen 3 s.", 12.0)
H(2, "9.2 Minimum-jerk trajectory")
P("Each motion between two poses follows a <b>minimum-jerk</b> profile (Flash and Hogan, 1985). Velocity and acceleration are zero at the start and end of the motion and "
  "the total <i>jerk</i> (derivative of acceleration) energy is minimized:", keep=True)
eq("mj_j")
P("Given the boundary conditions (position, velocity, acceleration), the solution of this variational problem is a degree-5 polynomial; with normalized time <i>τ</i> = <i>t</i>/<i>T</i>:", keep=True)
eq("mj_s")
eq("mj_v")
eq("mj_q")
P("The velocity profile is bell-shaped and its peak at τ = 0.5 is 1.875 times the mean velocity (Figure %d). This makes the motion on the real arm start and stop smoothly, "
  "i.e. it does not strain the servos and the mechanics." % F("f16_minjerk"), align="justify")
figure("f16_minjerk", "Minimum-jerk profile: position, velocity and acceleration (dashed line: linear interpolation).", 15.0)
P("Speed check: the largest joint motion is about 300 units for the accepted dots; in the 6 s approach phase the peak velocity is ≈ 1.875 · 300 / 6 ≈ 94 units/s (≈ 27°/s), "
  "well below the firmware's PC-mode limit (300 units/s).", align="justify")
H(2, "9.3 Motion phases")
table("Run phases (--speed 1)", ["Phase", "Duration", "Start → end", "Note"], [
    ["Approach", "6.0 s", "home → approach pose", "all joints together (linear in joint space)"],
    ["Touch", "4.0 s", "approach → touch pose", "small motion (≈ 10 units), slow descent"],
    ["Hold", "0.7 s", "at the touch pose", "contact time"],
    ["Lift", "3.0 s", "touch → approach", ""],
    ["Return", "6.0 s", "approach → home", "the firmware also homes separately"],
], [2.6, 1.8, 5.0, 6.6], align=["l", "c", "l", "l"], first_bold=True)
P("The <c>--speed</c> parameter divides all durations (0.5 → twice as slow, ≈ 39 s in total). The total duration for <i>--speed 1</i> is 19.7 s.", align="justify")
H(2, "9.4 Safety layers")
table("Safety when running the model", ["Layer", "Measure"], [
    ["PC (run_model.py)", "the dot is <b>rejected</b> if it is farther than 0.12 table units from the nearest training point; goals are clipped to [100, 923]; <b>x / ESC</b> emergency stop (the arm stays in place, homing separately with <c>h</c>)"],
    ["Firmware", "PC-mode slew limit (300 units/s); HOLD if PC commands stop for 300 ms; tracking error > 80 units for 500 ms → HOLD; voltage and temperature emergency stop"],
    ["Human", "cutting the power; first runs with <c>--speed 0.5</c> and a hand on the emergency stop key"],
], [3.6, 12.4], first_bold=True)

# ================================================================== 10. DEPLOYMENT
H(1, "10. Deployment on the real arm")
H(2, "10.1 Run flow")
code_block(["python3 tools/run_model.py 0 --speed 0.5      # camera + real arm",
            "",
            "camera finds (u, v) → model predicts the touch pose → approach pose = touch + δ",
            "→ [home → approach → touch → hold → lift → home]  (G lines at 40 Hz)"])
P("The camera finds the dot; if the median of the last 15 frames is stable, the model predicts the touch pose. The motion starts with SPACE or the button; every run is "
  "saved under <c>data/runs/</c> together with the goals sent and the measured follower positions.", align="justify")
H(2, "10.2 Runs")
kz = ST["runs"]
P("There are %d run records in total: <b>%d runs were completed</b> and <b>%d runs were aborted before moving</b>. Each abort had the same cause, "
  "“could not enter PC mode”: the arm did not switch to PC mode within 3 s. The firmware only accepts the <c>p</c> command in the WAIT, HOLD and READY states; "
  "if the arm is in another state (for example still homing) the command is ignored. The cause of these aborts was not investigated further in this report." % (kz["total"], kz["ok"], kz["aborted"]), align="justify")
ko = ST["run_example"]
figure("f18_run", "A completed run on the real arm (%s; dot u = %s, v = %s; --speed 0.5). Orange: goal sent; dashed: measured follower position. "
       "Shaded areas separate the phases." % (ko["file"].replace(".npz", ""), fmt(ko["uv"][0], 2), fmt(ko["uv"][1], 2)), 14.0)
P("The motion is smooth, following the minimum-jerk profile, and the follower tracks the goal closely: in this run the median goal–position difference is %s units depending on the joint, "
  "and the 95th percentile is at most %s units (shoulder)." % (" – ".join([fmt(min(ko["err_median"]), 1), fmt(max(ko["err_median"]), 1)]), fmt(max(ko["err_p95"]), 1)), align="justify")
figure("f19_run_points", "Training points (light blue) and runs completed on the real arm (orange stars).", 7.0)
P("The completed runs cover a large part of the reach area (approximately u = 0.16–0.42, v = 0.19–0.79).", align="justify")
H(2, "10.3 Evaluation")
P("According to the user, the arm found and touched the dot in all completed runs. This is consistent with the expected error of 1–1.5 cm from Section 8 (dot diameter ≈ 2.5–3 cm). "
  "<b>There is no quantitative measurement</b>: the touches were judged by eye and no end-point error in millimetres is given in this report.", align="justify")

# ================================================================== 11. LIMITATIONS
H(1, "11. Limitations and possible improvements")
numbered([
    "<b>Quantitative accuracy was not measured.</b> Success was judged by eye. Finding the screw tip in the camera image and computing its distance to the dot automatically would give the error in millimetres and allow a fair comparison with other methods.",
    "<b>Risk of path collisions.</b> The trajectory uses linear interpolation in joint space; since the arm geometry is not modelled, it is theoretically possible for the tip to come close to the table mid-path. "
    "No problems have been seen so far at low speed. Next step: build a planar forward-kinematics model and plan the path in Cartesian space.",
    "<b>Extrapolation is unreliable.</b> The model is only valid in the region seen in training (u ≈ %s–%s, v ≈ %s–%s); this region is also the reach area of the arm." %
    (fmt(ST["uv_range"]["u"][0], 2), fmt(ST["uv_range"]["u"][1], 2), fmt(ST["uv_range"]["v"][0], 2), fmt(ST["uv_range"]["v"][1], 2)),
    "<b>Small data set (114 episodes).</b> The neural network lost to the polynomial with this data. More complex tasks will need more data or a different method.",
    "<b>The wrist posture is not chosen.</b> The model produces the mean along the redundancy direction. This is valid as long as the curvature of the set is small; near the joint limits the mean may be an invalid pose. "
    "Fixing the wrist angle with a constraint (e.g. a fixed approach angle relative to the dot) and solving the other joints analytically would remove this.",
    "<b>Dead zone.</b> A basic limitation of the AX-12A; a joint whose working range approaches 0 or 1023 must be fixed by remounting.",
    "<b>Power.</b> A large part of the servo problems came from a drained battery. Collecting data with a stable 12 V power supply makes the data more consistent.",
    "<b>An image-based policy was not tried.</b> The video was recorded but not used; since the dot position is already known, the image adds no information for this task. "
    "When tasks become multi-step and variable (e.g. pick and place), ACT/diffusion-style methods become meaningful.",
    "<b>Aborted runs.</b> The cause of the 8 runs aborted with “could not enter PC mode” was not investigated; most likely the command was sent before the arm was ready.",
])

# ================================================================== 12. CONCLUSION
H(1, "12. Conclusion", newpage=False)
P("In this work a two-arm leader–follower system was built, a pipeline that measures table coordinates from images was developed, 114 usable demonstrations were collected and "
  "a model was trained that predicts the touch pose from the dot position. Despite per-joint errors that look misleading because of the arm's redundancy, the model worked with an "
  "accuracy corresponding to an end-point error of about 3° (≈ 1–1.5 cm) and was deployed on the real arm.", align="justify")
P("The most instructive findings of the project are: (i) for a single-step, static task a simple regression beats a neural network; (ii) per-joint error measures are misleading "
  "in the presence of redundancy, and an eigen-analysis of the error covariance reveals the actual error; (iii) the power supply directly determines data quality; (iv) the same "
  "infrastructure (home pose, data stream, button, monitoring, coverage map, PC mode) can be reused directly for the next projects — target tracking, pick and place, drawing.", align="justify")

# ================================================================== APPENDICES
H(1, "Appendix A. Symbols")
table("Symbols used in the report", ["Symbol", "Meaning"], [
    ["<i>p</i>", "servo position (0–1023)"],
    ["<i>l<sub>i</sub></i>, <i>f<sub>i</sub></i>, <i>g<sub>i</sub></i>", "leader position, follower position, goal sent to the follower (joint <i>i</i>)"],
    ["<i>H<sup>L</sup><sub>i</sub></i>, <i>H<sup>F</sup><sub>i</sub></i>", "leader and follower home value"],
    ["<i>s<sub>i</sub></i>", "joint direction sign (+1 / −1)"],
    ["<i>m<sub>i</sub></i>, <i>M<sub>i</sub></i>", "follower safe lower and upper limit"],
    ["<i>b<sub>i</sub></i>, <i>ℓ<sub>i</sub></i>", "goal before latency compensation, lead term"],
    ["<i>α</i>, <i>T<sub>ℓ</sub></i>, <i>ℓ<sub>max</sub></i>", "velocity filter coefficient, lead time, lead limit"],
    ["<i>R</i>, Δ<i>t</i>", "slew-rate limit [units/s], loop period"],
    ["<b>H</b>", "homography matrix (3×3)"],
    ["<i>(x, y)</i>, <i>(u, v)</i>", "pixel coordinate, table coordinate (0–1)"],
    ["<i>A</i>, <i>P</i>, <i>C</i>", "component area, perimeter, circularity"],
    ["<b>q</b>", "joint angle vector (base, shoulder, elbow, wrist)"],
    ["<b>X</b>, <b>Y</b>, <b>W</b>, <i>φ</i>, <i>λ</i>", "feature matrix, target matrix, weights, feature vector, ridge coefficient"],
    ["<b>C</b>, <b>v</b><sub>k</sub>, <i>λ<sub>k</sub></i>", "error covariance, eigenvector, eigenvalue"],
    ["<i>τ</i>, <i>T</i>, <i>s(τ)</i>", "normalized time, phase duration, minimum-jerk profile"],
    ["<b>δ</b>", "approach pose offset"],
], [5.0, 11.0], size=9)

H(1, "Appendix B. File map and commands")
table("Code structure", ["File", "Purpose"], [
    ["<c>firmware/arm_rec/arm_rec.ino</c>", "<b>Main firmware</b>: home, teleop (leader–follower), data stream, button, PC mode, safety"],
    ["<c>firmware/servo_lab/servo_lab.ino</c>", "servo inspection console (<c>scan, info, watch, pair, go, sweep, home, follow, comm, range</c>)"],
    ["<c>robot/opencm.py</c>", "PC-side serial link; reads data lines, sends commands/goals"],
    ["<c>vision/common.py</c>", "ArUco wrapper (OpenCV 4.6 and 4.7+)"],
    ["<c>vision/table_dot.py</c>", "homography (<c>TableTracker</c>) and white dot detection (<c>find_dot</c>)"],
    ["<c>vision/aruco_make.py</c>", "generates the corner markers"],
    ["<c>tools/record.py</c>", "<b>data collection program</b>"],
    ["<c>tools/run_model.py</c>", "<b>runs the model</b>: find dot → predict pose → trajectory → arm"],
    ["<c>tools/joint_range.py</c>", "joint range and dead-zone check"],
    ["<c>train/prepare.py</c>", "cleans the data, extracts the touch/approach poses"],
    ["<c>train/baseline.py</c>", "compares the models with LOO"],
    ["<c>report/make_figures.py</c>, <c>build_report.py</c>", "generate the figures and the document of this report"],
    ["<c>data/episodes/</c>, <c>data/processed/</c>, <c>data/runs/</c>", "raw episodes, processed data set and plots, run records"],
], [6.4, 9.6], size=8.5)
P("<b>Commands</b> (from an Ubuntu terminal; the VS Code snap terminal can cause problems with the OpenCV window):", keep=True)
code_block(["# Data collection",
            "cd ~/Desktop/arm && python3 tools/record.py 0",
            "",
            "# Prepare the data and compare the models",
            ".venv/bin/python train/prepare.py",
            ".venv/bin/python train/baseline.py",
            "",
            "# Run the model",
            ".venv/bin/python tools/run_model.py --uv 0.3 0.5 --dry --speed 0.5   # plan only",
            ".venv/bin/python tools/run_model.py 0 --speed 0.5                     # real arm",
            "",
            "# Rebuild this report",
            "bash report/build_all.sh"])
P("<b>Building the firmware:</b> select <i>OpenCM9.04 Board</i> in the Arduino IDE; from the command line: "
  "<c>arduino-cli compile --fqbn OpenCM904:OpenCM904:OpenCM904 firmware/arm_rec</c>.", align="justify")

H(1, "References")
for ref in [
    "ROBOTIS. <i>Dynamixel AX-12A e-Manual</i> (Protocol 1.0, control table).",
    "Garrido-Jurado, S., Muñoz-Salinas, R., Madrid-Cuevas, F. J., Marín-Jiménez, M. J. (2014). Automatic generation and detection of highly reliable fiducial markers under occlusion. <i>Pattern Recognition</i>, 47(6).",
    "Hartley, R., Zisserman, A. (2003). <i>Multiple View Geometry in Computer Vision</i>, 2nd edition. Cambridge University Press. (Homography, DLT.)",
    "Hoerl, A. E., Kennard, R. W. (1970). Ridge regression: biased estimation for nonorthogonal problems. <i>Technometrics</i>, 12(1).",
    "Rasmussen, C. E., Williams, C. K. I. (2006). <i>Gaussian Processes for Machine Learning</i>. MIT Press.",
    "Flash, T., Hogan, N. (1985). The coordination of arm movements: an experimentally confirmed mathematical model. <i>Journal of Neuroscience</i>, 5(7).",
    "Zhao, T. Z., Kumar, V., Levine, S., Finn, C. (2023). Learning fine-grained bimanual manipulation with low-cost hardware (ACT). <i>RSS</i>.",
    "OpenCV documentation: <c>getPerspectiveTransform</c>, <c>aruco</c>, <c>moments</c>.",
]:
    p = doc.add_paragraph(); p.paragraph_format.left_indent = Cm(0.8); p.paragraph_format.first_line_indent = Cm(-0.8); p.paragraph_format.space_after = Pt(4)
    add_runs(p, ref, size=9.5)

assert len(FIGS_USED) == len(FIG_ORDER), "unused figure: %s" % (set(FIG_ORDER) - set(FIGS_USED))
assert len(EQS_USED) == len(EQ_ORDER), "unused equation: %s" % (set(EQ_ORDER) - set(EQS_USED))


# ================================================================== TABLE OF CONTENTS (real TOC field + cached entries)
def build_toc(pages):
    """Inserts the TOC field after TOC_INSERT_AFTER (H1 and H2)."""
    entries = [(lv, tx) for lv, tx in HEADINGS if lv <= 2]
    anchor = TOC_INSERT_AFTER._p
    prev = anchor
    for i, (lv, tx) in enumerate(entries):
        p = doc.add_paragraph()
        pf = p.paragraph_format
        pf.space_after = Pt(2 if lv == 2 else 3)
        pf.space_before = Pt(7 if lv == 1 else 0)
        pf.left_indent = Cm(0.7 if lv == 2 else 0)
        pf.tab_stops.add_tab_stop(Cm(TEXT_W), WD_TAB_ALIGNMENT.RIGHT, WD_TAB_LEADER.DOTS)
        if i == 0:
            for kind, instr in (("begin", None), (None, ' TOC \\o "1-2" \\h \\z \\u '), ("separate", None)):
                r = p.add_run()
                if kind:
                    f = OxmlElement("w:fldChar"); f.set(qn("w:fldCharType"), kind); r._element.append(f)
                else:
                    it = OxmlElement("w:instrText"); it.set(qn("xml:space"), "preserve"); it.text = instr; r._element.append(it)
        r = p.add_run(tx); r.font.size = Pt(10 if lv == 1 else 9.5); r.bold = (lv == 1)
        r2 = p.add_run("\t%s" % pages.get(tx, "0")); r2.font.size = Pt(10 if lv == 1 else 9.5)
        if i == len(entries) - 1:
            r3 = p.add_run()
            f = OxmlElement("w:fldChar"); f.set(qn("w:fldCharType"), "end"); r3._element.append(f)
        prev.addnext(p._p)
        prev = p._p


ap = argparse.ArgumentParser()
ap.add_argument("--pages", default=None)
ap.add_argument("--out", default=os.path.join(ROOT, "Report.docx"))
ap.add_argument("--dump-headings", default=None)
args = ap.parse_args()
pages = json.load(open(args.pages)) if args.pages and os.path.exists(args.pages) else {}
build_toc(pages)
doc.save(args.out)
if args.dump_headings:
    json.dump([tx for lv, tx in HEADINGS if lv <= 2], open(args.dump_headings, "w"), ensure_ascii=False)
print("written:", args.out, "| figures:", len(FIGS_USED), "equations:", len(EQS_USED), "tables:", len(TABLES))
