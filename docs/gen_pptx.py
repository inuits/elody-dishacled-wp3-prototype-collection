"""Generate a natively-editable PPTX for the SHACL 1.2 UI bridge deck.

Every slide uses standard text boxes and real PPTX tables, so Google Slides
treats the content as editable text (not flattened images).
"""

from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR

TEAL = RGBColor(0x00, 0x3A, 0x52)
ACCENT = RGBColor(0x2A, 0x9D, 0x8F)
GREY = RGBColor(0x44, 0x44, 0x44)
CODEBG = RGBColor(0xF2, 0xF5, 0xF7)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)

prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)
BLANK = prs.slide_layouts[6]

MARGIN = Inches(0.7)
CONTENT_W = prs.slide_width - 2 * MARGIN


def add_slide():
    return prs.slides.add_slide(BLANK)


def textbox(slide, top, height, left=MARGIN, width=CONTENT_W):
    tb = slide.shapes.add_textbox(left, top, width, height)
    tf = tb.text_frame
    tf.word_wrap = True
    return tf


def style_run(run, size, color=GREY, bold=False, mono=False):
    f = run.font
    f.size = Pt(size)
    f.bold = bold
    f.color.rgb = color
    f.name = "Consolas" if mono else "Calibri"


def add_title(slide, text):
    tf = textbox(slide, Inches(0.45), Inches(1.0))
    p = tf.paragraphs[0]
    r = p.add_run()
    r.text = text
    style_run(r, 30, TEAL, bold=True)
    # accent underline bar
    bar = slide.shapes.add_textbox(MARGIN, Inches(1.25), Inches(1.2), Inches(0.06))
    bar.fill.solid() if False else None
    return Inches(1.5)


def add_paragraphs(slide, y, items):
    """items: list of (kind, payload). Returns new y (EMU)."""
    for kind, payload in items:
        if kind == "para":
            tf = textbox(slide, y, Inches(0.5))
            p = tf.paragraphs[0]
            _runs(p, payload, 18)
            y += Inches(0.55)
        elif kind == "space":
            y += Inches(payload)
        elif kind == "bullets":
            tf = textbox(slide, y, Inches(0.4 * len(payload)))
            for i, item in enumerate(payload):
                p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
                p.space_after = Pt(6)
                r = p.add_run()
                r.text = "•  "
                style_run(r, 18, ACCENT, bold=True)
                _runs(p, item, 18, append=True)
            y += Inches(0.42 * len(payload) + 0.1)
        elif kind == "quote":
            tf = textbox(slide, y, Inches(0.6))
            p = tf.paragraphs[0]
            r = p.add_run()
            r.text = payload
            style_run(r, 18, ACCENT, bold=False)
            r.font.italic = True
            y += Inches(0.7)
        elif kind == "code":
            lines = payload.split("\n")
            h = Inches(0.235 * len(lines) + 0.2)
            box = slide.shapes.add_textbox(MARGIN, y, CONTENT_W, h)
            box.fill.solid()
            box.fill.fore_color.rgb = CODEBG
            box.line.color.rgb = RGBColor(0xD5, 0xDD, 0xE1)
            tf = box.text_frame
            tf.word_wrap = False
            tf.margin_left = Pt(10)
            tf.margin_top = Pt(6)
            tf.margin_bottom = Pt(6)
            for i, line in enumerate(lines):
                p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
                r = p.add_run()
                r.text = line if line else " "
                style_run(r, 12, TEAL, mono=True)
            y += h + Inches(0.15)
        elif kind == "table":
            headers, rows = payload
            y = _table(slide, y, headers, rows)
    return y


def _runs(p, payload, size, append=False):
    """payload: str, or list of (text, bold) tuples for inline emphasis."""
    if isinstance(payload, str):
        payload = [(payload, False)]
    for text, bold in payload:
        r = p.add_run()
        r.text = text
        style_run(r, size, TEAL if bold else GREY, bold=bold)


def _table(slide, y, headers, rows):
    nrows = len(rows) + 1
    ncols = len(headers)
    h = Inches(0.42 * nrows)
    gr = slide.shapes.add_table(nrows, ncols, MARGIN, y, CONTENT_W, h).table
    for c, head in enumerate(headers):
        cell = gr.cell(0, c)
        cell.fill.solid()
        cell.fill.fore_color.rgb = TEAL
        para = cell.text_frame.paragraphs[0]
        run = para.add_run()
        run.text = head
        style_run(run, 13, WHITE, bold=True)
    for r_i, row in enumerate(rows, start=1):
        for c, val in enumerate(row):
            cell = gr.cell(r_i, c)
            cell.fill.solid()
            cell.fill.fore_color.rgb = WHITE if r_i % 2 else RGBColor(0xEF, 0xF3, 0xF5)
            para = cell.text_frame.paragraphs[0]
            mono = val.startswith("`") and val.endswith("`")
            run = para.add_run()
            run.text = val.strip("`")
            style_run(run, 12, GREY, mono=mono)
    return y + h + Inches(0.2)


def title_slide(title, subtitle, footer):
    slide = add_slide()
    tf = textbox(slide, Inches(2.6), Inches(1.4))
    p = tf.paragraphs[0]
    r = p.add_run(); r.text = title; style_run(r, 40, TEAL, bold=True)
    tf2 = textbox(slide, Inches(4.0), Inches(0.6))
    p = tf2.paragraphs[0]
    r = p.add_run(); r.text = subtitle; style_run(r, 22, ACCENT, bold=False)
    tf3 = textbox(slide, Inches(4.8), Inches(0.5))
    p = tf3.paragraphs[0]
    r = p.add_run(); r.text = footer; style_run(r, 16, GREY)


def content_slide(title, items):
    slide = add_slide()
    y = add_title(slide, title)
    add_paragraphs(slide, y, items)


# ---- slides -------------------------------------------------------------

title_slide(
    "SHACL-driven processor configuration",
    "Generating pipeline UIs from SHACL, the W3C way",
    "Dishacled WP3  —  Elody × RDF-Connect × SHACL 1.2 UI",
)

content_slide("The vision", [
    ("bullets", [
        "A processor declares its configuration as a SHACL shape",
        "The platform generates the configuration UI from that shape",
        "Adding / changing a processor = only its SHACL, zero UI code",
    ]),
    ("space", 0.2),
    ("quote", "Bridge Elody's JSON/GraphQL-driven UI with semantic, linked-data pipelines."),
])

content_slide("Where we were", [
    ("para", "The config form only understood a processor's main shape. Real processors nest their config — http-utils is 3 levels deep:"),
    ("code", "HttpFetch\n |- url, writer\n |- options  -> HttpFetchOptions\n                 |- method, headers, timeout, ...\n                 |- auth -> HttpFetchAuth -> type"),
    ("bullets", [
        "Modal showed 3 flat fields (url, writer, options) — incomplete",
        "Inline edit flattened all 14 fields — and lost the nesting (wrong for RDF)",
    ]),
])

content_slide("The principle", [
    ("para", [("Don't invent an Elody-specific form format. ", False),
              ("Derive the UI from the SHACL, following W3C SHACL 1.2 UI.", True)]),
    ("space", 0.15),
    ("bullets", [
        "shui: editor vocabulary, chosen per property from its constraints",
        "shui:DetailsEditor -> nested form for nested shapes (recursive)",
        "The UI definition is itself a standard, portable shape — no lock-in",
    ]),
])

content_slide("Architecture: SHACL -> SHACL 1.2 UI -> Elody", [
    ("code",
     "processor SHACL (GitHub)\n"
     "      |\n"
     "      v\n"
     "ShuiFormBuilder  -->  picks a shui:Editor per property\n"
     "                 -->  nested shape -> shui:DetailsEditor (recursive)\n"
     "                 -->  emits a shui: shape (TTL)   <- portable UI definition\n"
     "      |\n"
     "      v\n"
     "shacl_to_form_fields  -->  shui:Editor -> Elody widget\n"
     "                      -->  DetailsEditor -> inputFieldWithSubFields\n"
     "      |\n"
     "      v\n"
     "PWA DynamicForm  -->  native Elody widgets, nested sub-forms\n"
     "      |\n"
     "      v\n"
     "config saved on the pipeline<->processor relation\n"
     "      |\n"
     "      v\n"
     "export  -->  RDF-Connect pipeline.ttl  (round-trips into the nested shapes)"),
])

content_slide("Widget mapping (SHACL 1.2 UI -> Elody)", [
    ("table", (
        ["SHACL constraint", "SHACL 1.2 UI editor", "Elody widget"],
        [
            ["`xsd:string`", "`shui:TextFieldEditor`", "text"],
            ["`xsd:integer/decimal`", "`shui:NumberFieldEditor`", "number"],
            ["`xsd:boolean`", "`shui:BooleanEditor`", "checkbox"],
            ["`xsd:date/dateTime`", "`shui:DatePickerEditor`", "date"],
            ["`sh:in (...)`", "`shui:EnumSelectEditor`", "dropdown"],
            ["`sh:class Writer/Reader`", "`shui:InstancesSelectEditor`", "dropdown (channels)"],
            ["`sh:class/sh:node -> NodeShape`", "`shui:DetailsEditor`", "inputFieldWithSubFields"],
        ],
    )),
])

content_slide("The key: nested shapes", [
    ("quote", "shui:DetailsEditor = a nested form that recursively evaluates the shape."),
    ("para", "Elody already has the matching primitive: inputFieldWithSubFields, whose subFields can themselves be inputFieldWithSubFields."),
    ("space", 0.1),
    ("para", [("One-to-one mapping, to any depth. No new widget invented.", True)]),
])

content_slide("How it's generated — repo TTL -> shui shape", [
    ("code",
     "processor repo  -->  data.rawTtl  -->  ShuiFormBuilder\n"
     " (processors.ttl)\n\n"
     "  1. parse TTL into an RDF graph (meaning, not text)\n"
     "  2. find the processor shape\n"
     "       subject of  rdfc:*ImplementationOf rdfc:Processor  -> HttpFetch\n"
     "  3. per sh:property -> pick a shui:editor from its constraints\n"
     "  4. sh:class -> a local NodeShape?  -> DetailsEditor, recurse\n"
     "  5. serialise back to a shui:-annotated shape"),
    ("para", "The input is the SHACL the author already wrote; the output is a standard SHACL 1.2 UI shape no one wrote by hand."),
])

content_slide("The decision, per property", [
    ("code",
     "sh:property constraints\n"
     "  |\n"
     "  |- sh:in ( ... )?                -> shui:EnumSelectEditor      (dropdown)\n"
     "  |- sh:class -> local shape?      -> shui:DetailsEditor         (nested form)\n"
     "  |- sh:class rdfc:Writer/Reader?  -> shui:InstancesSelectEditor (channel)\n"
     "  |- xsd:integer / xsd:decimal?    -> shui:NumberFieldEditor\n"
     "  |- xsd:boolean?                  -> shui:BooleanEditor\n"
     "  |- xsd:date / xsd:dateTime?      -> shui:DatePickerEditor\n"
     "  |- else (xsd:string)            -> shui:TextFieldEditor"),
    ("para", "Checked top-down: an explicit value set or class outranks the datatype fallback."),
])

content_slide("Worked trace — http-utils", [
    ("table", (
        ["In the repo's SHACL", "Decision", "Generated"],
        [
            ["`rdfc:url` - xsd:string", "string", "`shui:TextFieldEditor`"],
            ["`rdfc:writer` - sh:class rdfc:Writer", "channel class", "`shui:InstancesSelectEditor`"],
            ["`rdfc:timeOutMilliseconds` - xsd:integer", "numeric", "`shui:NumberFieldEditor`"],
            ["`rdfc:closeOnEnd` - xsd:boolean", "boolean", "`shui:BooleanEditor`"],
            ["`rdfc:options` - sh:class HttpFetchOptions", "local shape", "`shui:DetailsEditor`"],
        ],
    )),
    ("para", "HttpFetchOptions is itself a shape in the repo -> recurse -> its auth (rdfc:HttpFetchAuth, also a local shape) -> DetailsEditor again -> type."),
])

content_slide("Worked example — generated shui: shape", [
    ("code",
     "[] a sh:NodeShape ; sh:targetClass rdfc:HttpFetch ;\n"
     "   sh:property [ sh:path rdfc:url ;    shui:editor shui:TextFieldEditor ] ,\n"
     "               [ sh:path rdfc:writer ; shui:editor shui:InstancesSelectEditor ] ,\n"
     "               [ sh:path rdfc:options ; shui:editor shui:DetailsEditor ;\n"
     "                 sh:node [ a sh:NodeShape ; sh:property\n"
     "                   [ sh:path rdfc:timeout ; shui:editor shui:NumberFieldEditor ] ,\n"
     "                   [ sh:path rdfc:auth ; shui:editor shui:DetailsEditor ;\n"
     "                     sh:node [ ... rdfc:type ... ] ] ] ] ."),
    ("para", "This shape is generated from the processor's SHACL — and is itself standard."),
])

content_slide("Show it live", [
    ("para", "Every processor exposes its generated SHACL 1.2 UI shape:"),
    ("code",
     "GET /processors/<id>/shui.ttl\n\n"
     "http://collection-api.dishacled-wp3-prototype-elody.localhost:8000\n"
     "        /processors/rdf-connect--http-utils-processor-ts/shui.ttl"),
    ("para", "Open it in the browser -> the standard shape, generated live from the processor's SHACL."),
])

content_slide("Live result", [
    ("para", "The modal, generated entirely from SHACL:"),
    ("bullets", [
        "URL (text)  -  Writer (live channel dropdown)",
        "Options (collapsible sub-form): method - headers - timeout - close-on-end - ...",
        "Auth (nested sub-form) -> type",
    ]),
    ("space", 0.1),
    ("para", [("A new processor with different shapes -> a different form, automatically.", True)]),
])

content_slide("Round-trips to RDF-Connect", [
    ("para", "Config is stored on the pipeline <-> processor relation (per-instance), then exported back into the nested shapes:"),
    ("code",
     "<stage> a rdfc:HttpFetch ;\n"
     "    rdfc:url \"https://...\" ;\n"
     "    rdfc:writer <out-channel> ;\n"
     "    rdfc:options [ rdfc:method \"GET\" ;\n"
     "                   rdfc:auth [ rdfc:type \"bearer\" ] ] ."),
    ("para", "Same processor, two pipelines, two configs — exactly as RDF-Connect models it."),
])

content_slide("Why it matters for Dishacled", [
    ("bullets", [
        "No UI lock-in — the form is a standard SHACL 1.2 UI shape; any conformant tool can render it",
        "Self-describing processors — ship SHACL, get a UI; zero UI work per processor",
        "Faithful to RDF-Connect — nesting preserved, config round-trips into the pipeline description",
    ]),
])

content_slide("Status", [
    ("table", (
        ["Piece", "State"],
        [
            ["SHACL -> SHACL 1.2 UI shape + shui: TTL", "done"],
            ["SHACL 1.2 UI -> Elody nested form", "done"],
            ["Nested sub-form rendering (PWA)", "done - live"],
            ["Config round-trip -> RDF-Connect TTL", "done"],
            ["Backend test suite", "75 passing"],
        ],
    )),
    ("para", [("Next: ", True), ("pyshacl validation on save - shui:editor overrides - run exported pipeline on the RDF-Connect orchestrator.", False)]),
])

content_slide("Thank you", [
    ("space", 0.5),
    ("para", "Standards used:"),
    ("bullets", [
        "RDF-Connect  —  https://rdf-connect.github.io/",
        "SHACL 1.2 UI  —  https://w3c.github.io/data-shapes/shacl12-ui/",
    ]),
])

import os

out = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "shacl-ui-presentation.pptx"
)
prs.save(out)
print(f"saved {len(prs.slides.__iter__.__self__._sldIdLst)} slides -> {out}")
