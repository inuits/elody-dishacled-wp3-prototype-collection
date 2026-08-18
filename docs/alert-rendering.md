# Rendering alerts from the shape

The alert overview and detail view in Elody are **generated from
`lblodsh:ErrorShape`**, not written field by field. Add a property to the shape
and it appears in the UI; rename one and the label follows. Nothing in the
frontend enumerates the alert's fields.

This is the rendering half of "Elody for alert visualisations" (demonstrator
Step 5). Ingestion is [`alert-ingestion.md`](alert-ingestion.md); the endpoint
and sample data are [`alert-fixture.md`](alert-fixture.md).

## The path

```
contracts.ttl : lblodsh:ErrorShape
  │   shacl_to_form_fields()          <- the same pipeline as processor config forms
  ▼
GET /shapes/alert                      {"fields": {...}, "order": [...]}
  │   fetched once per process, values merged per alert
  ▼
Alert.shapeFields (JSON)               graphql-service
  │
  ▼
shaclShapeElement -> EntityElementShaclShape.vue -> MetadataWrapper
```

The same shape also drives the **data**: `AlertSerializer` reads its
predicate→`sh:name` mapping from `ErrorShape` rather than keeping its own copy,
so metadata keys and form-field keys are the same set by construction. A test
asserts exactly that (`tests/test_alert_shape.py`), because the two drifting
apart is the failure that would otherwise show up as a detail page of empty
fields.

## Endpoints

| | |
|---|---|
| `GET /shapes/alert` | the Elody form fields, in display order — what the UI reads |
| `GET /shapes/alert/shui.ttl` | the SHACL 1.2 UI shape as Turtle, for inspection |

They live under `/shapes/` rather than `/alerts/` because they describe the
shape, not an alert, and it keeps them out of `/alerts/<id>`'s way. The Turtle
gives alerts the same inspectable artifact processors have at
`/processors/<id>/shui.ttl` — see [`shacl-ui-bridge.md`](shacl-ui-bridge.md).

## Where each piece lives

| Concern | Location |
|---|---|
| Shape → fields, display order | `api/apps/dishacled/shacl/alert_shape.py` |
| Endpoints | `api/apps/dishacled/resources/alert_shape.py` |
| Predicate → metadata key | `serializers/alert_serializer.py` (reads the shape) |
| Fetch + merge values | graphql-service `src/alertShapeFields.ts` |
| The view element | PWA `components/entityElements/EntityElementShaclShape.vue` |
| Overview + detail declaration | graphql-service `src/queries/entities/alert.queries.ts` |

The detail fragment declares a **panel, not fields**:

```graphql
entityView {
  column {
    elements {
      shaclShapeElement {
        label(input: "panel-labels.alert-shape")
        fieldsKey(input: "shapeFields")
      }
    }
  }
}
```

`fieldsKey` names the JSON field on the entity holding the field set. The PWA
component runs it through the existing `getMetadataFields` and renders each leaf
with `MetadataWrapper`, the same renderer every other detail panel uses — which
is why a shape-driven view looks like the rest of Elody. `getMetadataFields`
prefers a `value` already on a field, and that is what lets the panel render
without the query declaring `intialValues` per key.

## Refresh

The overview polls, so a new threshold breach appears without a reload. It is
opt-in per route:

```ts
// dishacledRoutes.ts
meta: { entityType: Entitytyping.Alert, pollIntervalMs: 15000 }
```

`BaseLibrary.vue` starts an interval only when the route asks for one, and
clears it on unmount; it skips a tick while the user is editing. Refetching is
cheap to get wrong and safe here because `useBaseLibrary` only swaps the list
when the result actually differs, so an unchanged poll does not re-render.
No other overview in any client polls.

## Deliberate deviations

**The display order is hand-written** (`ALERT_FIELD_ORDER` in
`alert_shape.py`), and it is the only hand-written part. `ErrorShape` has no
`sh:order` and cannot be given one: it is a verbatim copy of the published
threshold-monitor shape, with a gated test asserting it stays isomorphic to the
original, and it is what we hand to redpencil. RDF property sets are unordered
and these properties are blank nodes, so without an explicit order rdflib yields
them arbitrarily. The list orders fields; it does not define them — a property
added to the shape still appears, appended, with no change here.

**A field renamed in the shape moves to the end** for the same reason, since the
order list no longer mentions it. Visible but harmless, and it makes a shape
change obvious rather than silent.

**Alerts are read-only in the UI.** The route's `entityPageConfig` turns off the
edit and delete buttons for the alert type, because Elody holds no copy and
there is no SPARQL UPDATE path; offering the buttons would promise a write that
cannot happen.

**`created` renders as a raw ISO timestamp** in the detail view. The shape says
`xsd:dateTime` and the field gets the date widget, but the read-only renderer
has no format hint. The overview formats it (`unit: DATETIME_DMY24`) because
that column is declared by hand. Cosmetic.

## A bug this work fixed

`ShuiFormBuilder.to_ttl` rebuilt every `sh:path` and the `sh:targetClass` in the
`rdfc:` namespace from a bare local name, because `UiNode` only kept the local
name. For an alert shape that emitted `sh:path rdfc:message` and
`sh:targetClass rdfc:Error` — IRIs that do not exist. `UiNode` now carries the
full path IRI and the emitted document binds the source shape's own prefixes.
Processor shapes are unaffected (they really are `rdfc:`), and a test pins both.

## Tests

```bash
# shape -> fields, and the serializer/view key invariant
PYTHONPATH=api python -m pytest tests/test_alert_shape.py -q

# the value merge, in the graphql service
npx jest src/alertShapeFields.test.ts
```

The end-to-end check that actually proves the view is shape-driven: rename a
`sh:name` in `ErrorShape`, restart `collection-api` and `dashboard`, and watch
the label change in the browser with no frontend edit. Revert afterwards — the
shape is a verbatim copy.
