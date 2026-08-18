"""The alert view, derived from `lblodsh:ErrorShape`.

One shape drives both halves of the alert feature: the metadata keys the
serializer emits (`alert_field_names`) and the form fields the UI renders
(`alert_form_fields`). Deriving both from the same triples is what makes the
view shape-driven -- change a `sh:name` in the catalog and the API response and
the rendered detail follow together.

The form fields go through `shacl_to_form_fields`, the same SHACL 1.2 UI path
that produces processor configuration forms. See docs/alert-rendering.md.
"""

from functools import lru_cache

from rdflib import Graph, URIRef

from apps.dishacled.shacl.contracts import (
    DEFAULT_CONTRACTS_PATH,
    extract_shape_graph,
    SH,
)
from apps.dishacled.shacl.form import shacl_to_form_fields

ERROR_SHAPE = URIRef("http://lblod.data.gift/shapes/ErrorShape")

# Display order for the alert detail view.
#
# This is the one hand-written piece, and deliberately so: `ErrorShape` carries
# no `sh:order`, and it cannot be given one -- it is a verbatim copy of the
# threshold-monitor processor's published shape, with a gated test asserting it
# stays isomorphic to the original. RDF property sets are unordered and these
# properties are blank nodes, so without an explicit order the fields would come
# out in whatever order rdflib happened to yield.
#
# It orders fields; it does not define them. A property added to the shape still
# appears (appended) with no change here.
ALERT_FIELD_ORDER = (
    "message",
    "created",
    "subject",
    "creator",
    "detail",
    "references",
    "uuid",
)


@lru_cache(maxsize=1)
def error_shape_ttl() -> str:
    """The self-contained ErrorShape graph, serialized.

    `extract_shape_graph` rebinds the catalog's prefixes onto the result, so the
    alert vocabulary (oslc/dct/mu) survives instead of collapsing.
    """
    source = Graph()
    source.parse(DEFAULT_CONTRACTS_PATH, format="turtle")
    return extract_shape_graph(source, ERROR_SHAPE).serialize(format="turtle")


@lru_cache(maxsize=1)
def alert_field_names() -> dict[str, str]:
    """`{predicate IRI: sh:name}` for every property the shape declares."""
    graph = Graph()
    graph.parse(data=error_shape_ttl(), format="turtle")
    names = {}
    for _, _, prop in graph.triples((ERROR_SHAPE, SH.property, None)):
        path = graph.value(prop, SH.path)
        name = graph.value(prop, SH.name)
        if path is not None and name is not None:
            names[str(path)] = str(name)
    return names


def alert_form_fields() -> dict:
    """The Elody form fields for an alert, in display order.

    Same pipeline as the processor configuration forms; only the shape differs.
    """
    fields = shacl_to_form_fields(error_shape_ttl())
    ordered = {key: fields[key] for key in ALERT_FIELD_ORDER if key in fields}
    # Anything the shape gained that the order does not mention still shows up,
    # so a new property is never silently dropped from the view.
    for key, field in fields.items():
        ordered.setdefault(key, field)
    return ordered
