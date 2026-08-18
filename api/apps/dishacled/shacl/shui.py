"""Derive a SHACL 1.2 UI form from a processor's SHACL shapes.

Reference: https://w3c.github.io/data-shapes/shacl12-ui/

The SHACL 1.2 UI specification defines a `shui:` widget vocabulary and a
selection algorithm that picks an editor for each property from its
constraints (sh:datatype, sh:class, sh:in, cardinality). Nested node shapes
(a property whose sh:class / sh:node points to another sh:NodeShape) are
rendered with `shui:DetailsEditor` as a recursive nested form.

This module is the principled bridge layer: SHACL -> SHACL 1.2 UI. The
resulting UI tree (`UiNode`) is rendered to Elody form fields by `form.py`,
and can also be emitted as a `shui:`-annotated shape (`to_ttl`) so the UI
definition is itself a standard, portable artifact.
"""

from dataclasses import dataclass, field

from rdflib import Graph, Namespace, RDF, BNode, Literal, URIRef
from rdflib.collection import Collection


SH = Namespace("http://www.w3.org/ns/shacl#")
XSD = Namespace("http://www.w3.org/2001/XMLSchema#")
RDFC = Namespace("https://w3id.org/rdf-connect#")
RDFS = Namespace("http://www.w3.org/2000/01/rdf-schema#")
# SHACL 1.2 UI namespace (per the specification's prefix declaration).
SHUI = Namespace("http://www.w3.org/ns/shacl-ui#")

# sh:class values that resolve to an Elody channel instances-select rather than
# a nested details editor.
CHANNEL_CLASSES = {"Writer", "Reader", "Channel"}

_NUMERIC_DATATYPES = {
    "integer", "decimal", "float", "double", "long", "int", "nonNegativeInteger",
}


@dataclass
class UiNode:
    """A node in the derived SHACL 1.2 UI form tree.

    The root node represents the processor (main) shape; its `children` are the
    property editors. A property mapped to `DetailsEditor` carries the nested
    shape's properties as its own `children` (recursively).
    """

    name: str
    path: str | None = None  # local name, for display and lookups
    # The property's full IRI. Kept alongside the local name because a shape may
    # mix vocabularies -- an alert uses oslc:, dct: and mu: in one shape -- and
    # rebuilding an IRI from a local name would invent one that does not exist.
    path_iri: str | None = None
    editor: str = "TextFieldEditor"  # shui editor local name
    datatype: str | None = None
    class_ref: str | None = None
    is_required: bool = False
    in_values: list = field(default_factory=list)
    order: float | None = None
    group: str | None = None
    children: list["UiNode"] = field(default_factory=list)

    @property
    def is_details(self) -> bool:
        return self.editor == "DetailsEditor"


def _local_name(uri) -> str:
    s = str(uri)
    if "#" in s:
        return s.split("#")[-1]
    return s.rsplit("/", 1)[-1]


def _select_editor(datatype_ln, class_ln, in_values, is_nested_shape) -> str:
    """Choose a shui:Editor (local name) for a property's constraints.

    Deterministic encoding of the spec's scoring priority: an explicit value
    set (sh:in) or class constraint outranks the datatype fallback.
    """
    if in_values:
        return "EnumSelectEditor"
    if is_nested_shape:
        return "DetailsEditor"
    if class_ln in CHANNEL_CLASSES:
        return "InstancesSelectEditor"
    if class_ln:
        # external class reference (not a local shape, not a channel)
        return "AutoCompleteEditor"
    if datatype_ln in _NUMERIC_DATATYPES:
        return "NumberFieldEditor"
    if datatype_ln == "boolean":
        return "BooleanEditor"
    if datatype_ln == "date":
        return "DatePickerEditor"
    if datatype_ln == "dateTime":
        return "DateTimePickerEditor"
    return "TextFieldEditor"


class ShuiFormBuilder:
    def __init__(self, ttl_string: str):
        self._g = Graph()
        self._g.parse(data=ttl_string, format="turtle")
        # map targetClass local name -> node shape subject
        self._shapes_by_class: dict[str, object] = {}
        # ...and the full IRI of that class, so the emitted shape can name it
        # in its own vocabulary rather than guessing one.
        self._class_iris: dict[str, object] = {}
        for ns in self._g.subjects(RDF.type, SH.NodeShape):
            tc = self._g.value(ns, SH.targetClass)
            if tc is not None:
                self._shapes_by_class[_local_name(tc)] = ns
                self._class_iris[_local_name(tc)] = tc

    # -- tree -------------------------------------------------------------

    def _main_class(self) -> str | None:
        """Local name of the shape whose targetClass implements rdfc:Processor."""
        for s, p, o in self._g:
            if o == RDFC.Processor and str(p).split("#")[-1].endswith(
                "ImplementationOf"
            ):
                return _local_name(s)
        # fallback: first shape with a targetClass
        return next(iter(self._shapes_by_class), None)

    def build_tree(self) -> UiNode:
        main = self._main_class()
        root = UiNode(name=main or "root", editor="DetailsEditor")
        if main and main in self._shapes_by_class:
            root.children = self._build_children(main, visited={main})
        return root

    def _build_children(self, class_ln: str, visited: set[str]) -> list[UiNode]:
        shape = self._shapes_by_class[class_ln]
        nodes = []
        for prop in self._g.objects(shape, SH.property):
            node = self._build_node(prop, visited)
            if node:
                nodes.append(node)
        return nodes

    def _build_node(self, prop, visited: set[str]) -> UiNode | None:
        name_val = self._g.value(prop, SH.name)
        if not name_val:
            return None
        name = str(name_val)

        path = self._g.value(prop, SH.path)
        datatype = self._g.value(prop, SH.datatype)
        class_uri = self._g.value(prop, SH["class"])
        node_shape = self._g.value(prop, SH.node)

        datatype_ln = _local_name(datatype) if datatype else None
        class_ln = _local_name(class_uri) if class_uri else None

        in_node = self._g.value(prop, SH["in"])
        in_values = (
            [str(i) for i in Collection(self._g, in_node)] if in_node else []
        )

        min_count = self._g.value(prop, SH.minCount)
        is_required = int(min_count) >= 1 if min_count is not None else False

        order_val = self._g.value(prop, SH.order)
        order = float(order_val) if order_val is not None else None

        group_val = self._g.value(prop, SH.group)
        group = _local_name(group_val) if group_val else None

        # A nested shape is referenced via sh:node, or via sh:class pointing at
        # a local NodeShape's targetClass (and not a channel class).
        nested_class = None
        if node_shape is not None:
            nested_class = _local_name(self._g.value(node_shape, SH.targetClass))
        elif class_ln and class_ln in self._shapes_by_class and (
            class_ln not in CHANNEL_CLASSES
        ):
            nested_class = class_ln

        is_nested = nested_class is not None and nested_class not in visited

        editor = _select_editor(datatype_ln, class_ln, in_values, is_nested)

        node = UiNode(
            name=name,
            path=_local_name(path) if path else None,
            path_iri=str(path) if path else None,
            editor=editor,
            datatype=datatype_ln,
            class_ref=class_ln,
            is_required=is_required,
            in_values=in_values,
            order=order,
            group=group,
        )
        if is_nested:
            node.children = self._build_children(
                nested_class, visited | {nested_class}
            )
        return node

    # -- shui TTL artifact ------------------------------------------------

    def to_ttl(self) -> str:
        """Emit a `shui:`-annotated SHACL shape for the derived form.

        Produces a self-contained Turtle document where each property shape of
        the processor (and its nested shapes) carries a `shui:editor` triple.
        This is the standard, portable UI definition.
        """
        out = Graph()
        out.bind("sh", SH)
        out.bind("shui", SHUI)
        out.bind("rdfc", RDFC)

        root = self.build_tree()
        # Bind whatever vocabularies the source shape used, so the output reads
        # in the same prefixes rather than as bare IRIs.
        for prefix, namespace in self._g.namespaces():
            out.bind(prefix, namespace)

        shape_subject = BNode()
        out.add((shape_subject, RDF.type, SH.NodeShape))
        if root.name:
            target = self._class_iris.get(root.name) or RDFC[root.name]
            out.add((shape_subject, SH.targetClass, URIRef(target)))
        self._emit_properties(out, shape_subject, root.children)
        return out.serialize(format="turtle")

    def _emit_properties(self, out: Graph, subject, children: list[UiNode]):
        for node in children:
            prop = BNode()
            out.add((subject, SH.property, prop))
            if node.path_iri:
                out.add((prop, SH.path, URIRef(node.path_iri)))
            elif node.path:
                out.add((prop, SH.path, RDFC[node.path]))
            out.add((prop, SH.name, Literal(node.name)))
            out.add((prop, SHUI.editor, SHUI[node.editor]))
            if node.order is not None:
                order_lit = (
                    int(node.order) if float(node.order).is_integer() else node.order
                )
                out.add((prop, SH.order, Literal(order_lit)))
            if node.is_required:
                out.add((prop, SH.minCount, Literal(1)))
            if node.is_details and node.children:
                nested = BNode()
                out.add((prop, SH.node, nested))
                out.add((nested, RDF.type, SH.NodeShape))
                self._emit_properties(out, nested, node.children)
