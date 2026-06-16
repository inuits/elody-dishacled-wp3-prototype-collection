# SHACL-driven processor configuration UI

**Dishacled WP3 — design note for the consortium**

## Goal

Make Elody pipelines modular in the spirit of [RDF-Connect](https://rdf-connect.github.io/):
a processor declares its configuration as a **SHACL shape**, and Elody renders a
configuration form for it **without any processor-specific UI code**. Adding a new
processor (or changing its options) requires only its SHACL — no frontend changes.

The form is not hand-written and not Elody-specific: it is derived from the
processor's SHACL following the W3C **[SHACL 1.2 UI](https://w3c.github.io/data-shapes/shacl12-ui/)**
specification, so the UI definition is a standard, portable artifact.

## Architecture: SHACL → SHACL 1.2 UI → Elody

```
 processor repo (GitHub)
        │  SHACL shapes (e.g. http-utils: HttpFetch / HttpFetchOptions / HttpFetchAuth)
        ▼
 ┌─────────────────────────┐
 │  ShuiFormBuilder         │   shacl/shui.py
 │  • pick a shui: editor   │   SHACL constraints ──► shui:Editor  (selection algorithm)
 │    per property          │   nested node shape ──► shui:DetailsEditor (recursive)
 │  • emit shui: shape TTL  │   ◄── standard, portable UI definition (to_ttl)
 └─────────────────────────┘
        │  UiNode tree  (+ optional shui: TTL artifact)
        ▼
 ┌─────────────────────────┐
 │  shacl_to_form_fields    │   shacl/form.py
 │  shui:Editor ─► Elody    │   DetailsEditor ─► inputFieldWithSubFields (recursive)
 │  InputFieldTypes         │
 └─────────────────────────┘
        │  modalFormFields (JSON)
        ▼
 baseGraphql resolver  ──►  PWA DynamicForm  ──►  config saved as metadata
 (injects live channels)    (native Elody widgets)   on the hasProcessor relation
        │
        ▼
 export ──► RDF-Connect pipeline TTL  (config round-trips back into the nested shapes)
```

The per-instance configuration (this processor, in this pipeline) is stored as
**metadata on the `hasProcessor` relation** — so the same processor can appear in
two pipelines with different configuration, exactly as RDF-Connect models it.

## Widget selection (SHACL 1.2 UI → Elody)

SHACL 1.2 UI defines a `shui:` editor vocabulary and a selection algorithm that
chooses an editor per property from its constraints. We implement a deterministic
encoding of that algorithm and map each `shui:` editor onto an existing Elody
widget (`InputFieldTypes`):

| SHACL constraint | SHACL 1.2 UI editor | Elody `InputFieldType` |
|---|---|---|
| `sh:datatype xsd:string` | `shui:TextFieldEditor` | `text` |
| `sh:datatype xsd:integer/decimal/…` | `shui:NumberFieldEditor` | `number` |
| `sh:datatype xsd:boolean` | `shui:BooleanEditor` | `checkbox` |
| `sh:datatype xsd:date / dateTime` | `shui:DatePickerEditor` | `date` |
| `sh:in ( … )` | `shui:EnumSelectEditor` | `dropdown` |
| `sh:class rdfc:Writer / Reader / Channel` | `shui:InstancesSelectEditor` | `dropdown` (live channels) |
| **`sh:class` / `sh:node` → another `sh:NodeShape`** | **`shui:DetailsEditor`** | **`inputFieldWithSubFields`** (recursive) |
| `sh:group` / `sh:order` | — | section grouping / field order |

The key row is the nested one: SHACL 1.2 UI renders a nested node shape as a
`shui:DetailsEditor` ("a nested form that recursively evaluates the applicable
shape"). Elody already has the matching primitive — `inputFieldWithSubFields`,
whose `subFields` may themselves be `inputFieldWithSubFields` — so nesting maps
across one-to-one, to any depth.

## Worked example: `http-utils-processor-ts`

Its SHACL is a three-level nesting:

```
HttpFetch  (rdfc:jsImplementationOf rdfc:Processor)
├─ url      xsd:string  (required)
├─ writer   → rdfc:Writer            (channel)
└─ options  → rdfc:HttpFetchOptions  (nested)
              ├─ method, headers, cron        xsd:string
              ├─ timeOutMilliseconds          xsd:integer
              ├─ closeOnEnd, errorsAreFatal…  xsd:boolean
              └─ auth → rdfc:HttpFetchAuth     (nested)
                        └─ type  xsd:string (required)
```

`ShuiFormBuilder.to_ttl()` derives this standard SHACL 1.2 UI shape (abridged):

```turtle
@prefix sh:   <http://www.w3.org/ns/shacl#> .
@prefix shui: <http://www.w3.org/ns/shacl-ui#> .
@prefix rdfc: <https://w3id.org/rdf-connect#> .

[] a sh:NodeShape ;
   sh:targetClass rdfc:HttpFetch ;
   sh:property [ sh:path rdfc:url ;     sh:name "url" ;    sh:minCount 1 ; sh:order 0 ;
                 shui:editor shui:TextFieldEditor ] ,
               [ sh:path rdfc:writer ;  sh:name "writer" ; sh:minCount 1 ; sh:order 1 ;
                 shui:editor shui:InstancesSelectEditor ] ,
               [ sh:path rdfc:options ; sh:name "options" ; sh:order 2 ;
                 shui:editor shui:DetailsEditor ;
                 sh:node [ a sh:NodeShape ;
                   sh:property
                     [ sh:path rdfc:timeout ; sh:name "timeOutMilliseconds" ;
                       shui:editor shui:NumberFieldEditor ] ,
                     [ sh:path rdfc:closeOnEnd ; sh:name "closeOnEnd" ;
                       shui:editor shui:BooleanEditor ] ,
                     [ sh:path rdfc:auth ; sh:name "auth" ;
                       shui:editor shui:DetailsEditor ;
                       sh:node [ a sh:NodeShape ;
                         sh:property [ sh:path rdfc:type ; sh:name "type" ; sh:minCount 1 ;
                                       shui:editor shui:TextFieldEditor ] ] ] ] ] .
```

Elody then renders `url` (text) · `writer` (channel dropdown) · `options` (an
expandable sub-form) → which contains `auth` (a further sub-form), all from this
one definition.

## Why this matters for Dishacled

- **No UI lock-in.** The form definition is a standard SHACL 1.2 UI shape; any
  conformant tool — not only Elody — can render it.
- **Processors stay self-describing.** A processor ships its SHACL; the platform
  derives the UI. New processors need zero UI work.
- **Faithful to RDF-Connect.** Nested options/auth stay nested (not flattened),
  and per-instance config lives on the pipeline↔processor relation, so it
  round-trips into the RDF-Connect pipeline description.

## Showing the generated schema

The derived SHACL 1.2 UI shape is exposed directly, so it can be inspected or
demonstrated:

```
GET /processors/<id>/shui.ttl            # generated shui: shape (Turtle)
GET /processors/<id>/shui.ttl?download=1 # same, as a file download

# e.g. http-utils (local):
http://collection-api.dishacled-wp3-prototype-elody.localhost:8000/processors/rdf-connect--http-utils-processor-ts/shui.ttl
```

A captured example (generated from the live processor) is checked in at
[`docs/examples/http-utils.shui.ttl`](examples/http-utils.shui.ttl).

## Implementation map

| Concern | Location |
|---|---|
| SHACL → SHACL 1.2 UI tree + `shui:` TTL | `api/apps/dishacled/shacl/shui.py` |
| SHACL 1.2 UI → Elody form fields (nested) | `api/apps/dishacled/shacl/form.py` (`shacl_to_form_fields`) |
| Served per processor | `api/apps/dishacled/storage/dishacled_httpstore.py` (`data.formFields`) |
| Generated `shui:` shape endpoint | `api/apps/dishacled/resources/processor_shui.py` |
| Nested config → RDF-Connect TTL | `api/apps/dishacled/serializers/pipeline_ttl_serializer.py` |
| Live channel options injected | graphql-service `ProcessorConfigForm` resolver |
| Rendered | PWA `DynamicForm.vue` + `ShaclDetailsField.vue` |
| Tests | `tests/test_shui.py`, `tests/test_shui_form.py`, `tests/test_pipeline_ttl_serializer.py` |
