---
marp: true
theme: default
paginate: true
title: SHACL-driven processor configuration in Elody
---

# SHACL-driven processor configuration

### Generating pipeline UIs from SHACL, the W3C way

Dishacled WP3 — Elody × RDF-Connect × SHACL 1.2 UI

---

## The vision

Make Elody pipelines **modular**, like [RDF-Connect](https://rdf-connect.github.io/):

- A processor **declares its configuration as a SHACL shape**
- The platform **generates the configuration UI** from that shape
- Adding / changing a processor = **only its SHACL**, zero UI code

> Bridge Elody's JSON/GraphQL-driven UI with semantic, linked-data pipelines.

---

## Where we were

The config form only understood a processor's **main shape**.

Real processors nest their config. `http-utils` is 3 levels deep:

```
HttpFetch
├─ url, writer
└─ options  → HttpFetchOptions
              ├─ method, headers, timeout, …
              └─ auth → HttpFetchAuth → type
```

- Modal showed **3 flat fields** (url, writer, options) — incomplete
- Inline edit **flattened all 14 fields** — and lost the nesting (wrong for RDF)

---

## The principle

Don't invent an Elody-specific form format.

**Derive the UI from the SHACL, following W3C [SHACL 1.2 UI](https://w3c.github.io/data-shapes/shacl12-ui/).**

- `shui:` editor vocabulary, chosen per property from its constraints
- `shui:DetailsEditor` → nested form for nested shapes (recursive)
- The UI definition is itself a **standard, portable shape** — no lock-in

---

## Architecture: SHACL → SHACL 1.2 UI → Elody

```
 processor SHACL (GitHub)
        ▼
 ShuiFormBuilder ──► picks a shui:Editor per property
                ──► nested shape → shui:DetailsEditor (recursive)
                ──► emits a shui: shape (TTL)   ◄── portable UI definition
        ▼
 shacl_to_form_fields ──► shui:Editor → Elody widget
                      ──► DetailsEditor → inputFieldWithSubFields
        ▼
 PWA DynamicForm ──► native Elody widgets, nested sub-forms
        ▼
 config saved on the pipeline↔processor relation
        ▼
 export ──► RDF-Connect pipeline.ttl  (round-trips into the nested shapes)
```

---

## Widget mapping (SHACL 1.2 UI → Elody)

| SHACL constraint | SHACL 1.2 UI editor | Elody widget |
|---|---|---|
| `xsd:string` | `shui:TextFieldEditor` | text |
| `xsd:integer/decimal` | `shui:NumberFieldEditor` | number |
| `xsd:boolean` | `shui:BooleanEditor` | checkbox |
| `xsd:date/dateTime` | `shui:DatePickerEditor` | date |
| `sh:in (…)` | `shui:EnumSelectEditor` | dropdown |
| `sh:class rdfc:Writer/Reader` | `shui:InstancesSelectEditor` | dropdown (live channels) |
| **`sh:class/sh:node → NodeShape`** | **`shui:DetailsEditor`** | **inputFieldWithSubFields** |

---

## The key: nested shapes

`shui:DetailsEditor` = *"a nested form that recursively evaluates the shape."*

Elody already has the matching primitive: **`inputFieldWithSubFields`**, whose
`subFields` can themselves be `inputFieldWithSubFields`.

➡️ One-to-one mapping, to **any depth**. No new widget invented.

---

## How it's generated — repo TTL → shui shape

```
processor repo  ──►  data.rawTtl  ──►  ShuiFormBuilder
 (processors.ttl)                         │
                                          ▼
   1. parse TTL into an RDF graph (meaning, not text)
   2. find the processor shape
        subject of  rdfc:*ImplementationOf rdfc:Processor   → HttpFetch
   3. per sh:property → pick a shui:editor from its constraints
   4. sh:class → a local NodeShape?  → DetailsEditor, recurse ↻
   5. serialise back to a shui:-annotated shape
```

The input is the SHACL the processor author already wrote.
The output is a standard SHACL 1.2 UI shape no one wrote by hand.

---

## The decision, per property

```
 sh:property constraints
   │
   ├─ sh:in ( … )?               ──►  shui:EnumSelectEditor      (dropdown)
   ├─ sh:class → local shape?    ──►  shui:DetailsEditor         (nested form ↻)
   ├─ sh:class rdfc:Writer/Reader ─►  shui:InstancesSelectEditor (channel)
   ├─ xsd:integer / xsd:decimal? ──►  shui:NumberFieldEditor
   ├─ xsd:boolean?               ──►  shui:BooleanEditor
   ├─ xsd:date / xsd:dateTime?   ──►  shui:DatePickerEditor
   └─ else (xsd:string)          ──►  shui:TextFieldEditor
```

Checked top-down: an explicit value set or class outranks the datatype fallback.

---

## Worked trace — http-utils

| In the repo's SHACL | Decision | Generated |
|---|---|---|
| `rdfc:url` · `xsd:string` | string | `shui:TextFieldEditor` |
| `rdfc:writer` · `sh:class rdfc:Writer` | channel class | `shui:InstancesSelectEditor` |
| `rdfc:timeOutMilliseconds` · `xsd:integer` | numeric | `shui:NumberFieldEditor` |
| `rdfc:closeOnEnd` · `xsd:boolean` | boolean | `shui:BooleanEditor` |
| `rdfc:options` · `sh:class rdfc:HttpFetchOptions` | **local shape** | `shui:DetailsEditor` ↻ |

`HttpFetchOptions` is itself a shape in the repo → recurse → its `auth`
(`rdfc:HttpFetchAuth`, also a local shape) → `DetailsEditor` again → `type`.

---

## Worked example — generated `shui:` shape

```turtle
[] a sh:NodeShape ; sh:targetClass rdfc:HttpFetch ;
   sh:property [ sh:path rdfc:url ;    shui:editor shui:TextFieldEditor ] ,
               [ sh:path rdfc:writer ; shui:editor shui:InstancesSelectEditor ] ,
               [ sh:path rdfc:options ; shui:editor shui:DetailsEditor ;
                 sh:node [ a sh:NodeShape ; sh:property
                   [ sh:path rdfc:timeout ; shui:editor shui:NumberFieldEditor ] ,
                   [ sh:path rdfc:auth ; shui:editor shui:DetailsEditor ;
                     sh:node [ … rdfc:type … ] ] ] ] .
```

This shape is generated from the processor's SHACL — and is itself standard.

---

## Show it live

Every processor exposes its generated SHACL 1.2 UI shape:

```
GET /processors/<id>/shui.ttl
```

```
http://collection-api.dishacled-wp3-prototype-elody.localhost:8000/
        processors/rdf-connect--http-utils-processor-ts/shui.ttl
```

➡️ Open it in the browser → the standard shape, generated live from the
processor's SHACL. (Captured: `docs/examples/http-utils.shui.ttl`.)

---

## Live result

The modal, generated entirely from SHACL:

- **URL** (text) · **Writer** (live channel dropdown)
- **Options ▾** — collapsible sub-form
  - method · headers · timeout · close-on-end · …
  - **Auth ▾** — nested sub-form → **type**

A new processor with different shapes → a different form, automatically.

---

## Round-trips to RDF-Connect

Config is stored on the **pipeline ↔ processor relation** (per-instance), then
exported back into the nested shapes:

```turtle
<stage> a rdfc:HttpFetch ;
    rdfc:url "https://…" ;
    rdfc:writer <out-channel> ;
    rdfc:options [ rdfc:method "GET" ;
                   rdfc:auth [ rdfc:type "bearer" ] ] .
```

Same processor, two pipelines, two configs — exactly as RDF-Connect models it.

---

## Why it matters for Dishacled

- **No UI lock-in** — the form is a standard SHACL 1.2 UI shape; any conformant
  tool can render it
- **Self-describing processors** — ship SHACL, get a UI; zero UI work per processor
- **Faithful to RDF-Connect** — nesting preserved, config round-trips into the
  pipeline description

---

## Status

| Piece | State |
|---|---|
| SHACL → SHACL 1.2 UI shape + `shui:` TTL | ✅ |
| SHACL 1.2 UI → Elody nested form | ✅ |
| Nested sub-form rendering (PWA) | ✅ live |
| Config round-trip → RDF-Connect TTL | ✅ |
| Backend test suite | ✅ 75 passing |

**Next:** pyshacl validation on save · `shui:editor` overrides · run exported
pipeline on the RDF-Connect orchestrator.

---

# Thank you

Standards used:
RDF-Connect — https://rdf-connect.github.io/
SHACL 1.2 UI — https://w3c.github.io/data-shapes/shacl12-ui/
