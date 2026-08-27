from dataclasses import dataclass, field
from functools import lru_cache
from rdflib import Graph, Namespace, RDF, Literal, URIRef
from rdflib.collection import Collection

from apps.dishacled.shacl.graphs import namespace_prefixes, parsed_graph


SH = Namespace("http://www.w3.org/ns/shacl#")
XSD = Namespace("http://www.w3.org/2001/XMLSchema#")
RDFC = Namespace("https://w3id.org/rdf-connect#")
RDFS = Namespace("http://www.w3.org/2000/01/rdf-schema#")

DATATYPE_TO_INPUT_FIELD = {
    XSD.string: "baseTextField",
    XSD.integer: "baseNumberField",
    XSD.decimal: "baseNumberField",
    XSD.boolean: "baseCheckbox",
    XSD.anyURI: "baseTextField",
    XSD.dateTime: "baseTextField",
    XSD.date: "baseTextField",
    XSD.float: "baseNumberField",
    XSD.double: "baseNumberField",
    XSD.long: "baseNumberField",
    XSD.int: "baseNumberField",
}

WRITER_CLASSES = {RDFC.Writer}
CHANNEL_CLASSES = {RDFC.Channel}


@dataclass
class ShaclProperty:
    name: str
    path: str
    datatype: str | None = None
    class_ref: str | None = None
    min_count: int = 0
    max_count: int | None = None
    in_values: list = field(default_factory=list)
    input_field_type: str = "baseTextField"

    @property
    def is_required(self) -> bool:
        return self.min_count >= 1


def _uri_to_prefixed(uri, graph):
    for prefix, namespace in namespace_prefixes(graph):
        ns_str = str(namespace)
        uri_str = str(uri)
        if uri_str.startswith(ns_str):
            local = uri_str[len(ns_str):]
            if prefix:
                return f"{prefix}:{local}"
            return local
    return str(uri)


def _local_name(uri):
    uri_str = str(uri)
    if "#" in uri_str:
        return uri_str.split("#")[-1]
    return uri_str.rsplit("/", 1)[-1]


class ShaclParser:
    def parse(self, ttl_string: str) -> dict[str, list[ShaclProperty]]:
        return {
            class_name: list(properties)
            for class_name, properties in _parse(ttl_string)
        }

    def _walk_shapes(self, ttl_string: str) -> dict[str, list[ShaclProperty]]:
        g = parsed_graph(ttl_string)
        if g is None:
            raise ValueError("not turtle")

        shapes = {}

        for node_shape in g.subjects(RDF.type, SH.NodeShape):
            target_class = g.value(node_shape, SH.targetClass)
            if not target_class:
                continue

            class_name = _local_name(target_class)
            properties = []

            for prop_node in g.objects(node_shape, SH.property):
                prop = self._parse_property(g, prop_node)
                if prop:
                    properties.append(prop)

            shapes[class_name] = properties

        return shapes

    def parse_main_processor_properties(
        self, ttl_string: str, target_class=None
    ) -> list[ShaclProperty]:
        """Properties of one processor shape only.

        Multi-shape files (e.g. http-utils: HttpFetch + HttpFetchAuth +
        HttpFetchOptions) otherwise flatten into one big form. The shape is the
        one targeting `target_class` when the caller names it -- a file
        declaring several processors has several answers and only the component
        knows which it is -- else the first class declaring
        `rdfc:*ImplementationOf rdfc:Processor`, in IRI order. Falls back to all
        properties if no such shape is found.
        """
        return list(_main_processor_properties(ttl_string, target_class))

    def _walk_main_processor_properties(
        self, ttl_string: str, target_class=None
    ) -> list[ShaclProperty]:
        g = parsed_graph(ttl_string)
        if g is None:
            raise ValueError("not turtle")

        shapes_by_class = {}
        for node_shape in g.subjects(RDF.type, SH.NodeShape):
            shape_target = g.value(node_shape, SH.targetClass)
            if shape_target is not None:
                shapes_by_class.setdefault(shape_target, node_shape)

        def _properties_of(node_shape):
            return [
                prop
                for prop in (
                    self._parse_property(g, p)
                    for p in g.objects(node_shape, SH.property)
                )
                if prop
            ]

        if target_class is not None:
            node_shape = shapes_by_class.get(URIRef(str(target_class)))
            if node_shape is not None:
                return _properties_of(node_shape)

        declared = sorted(
            (
                s
                for s, p, o in g
                if o == RDFC.Processor
                and str(p).split("#")[-1].endswith("ImplementationOf")
            ),
            key=str,
        )
        for processor_class in declared:
            node_shape = shapes_by_class.get(processor_class)
            if node_shape is not None:
                return _properties_of(node_shape)

        all_props = []
        for node_shape in shapes_by_class.values():
            all_props.extend(_properties_of(node_shape))
        return all_props

    def parse_shape_properties(
        self, ttl_string: str, shape_iri: str | None = None
    ) -> list[ShaclProperty]:
        """Properties of a single node shape, addressed by its own IRI.

        Unlike parse(), this does not key by sh:targetClass. Two shapes may
        legitimately share a target class -- the cm and mm interface shapes
        both describe demo:Measurement -- and would overwrite each other in
        parse()'s dict. Contracts therefore read one shape at a time.

        `shape_iri` of None selects the sole node shape in the document (the
        common case for an extracted shape sub-graph); if several are present,
        the first in iteration order is used.
        """
        return list(_shape_properties(ttl_string, shape_iri))

    def _walk_shape_properties(
        self, ttl_string: str, shape_iri: str | None = None
    ) -> list[ShaclProperty]:
        g = parsed_graph(ttl_string)
        if g is None:
            raise ValueError("not turtle")

        if shape_iri:
            shape = URIRef(shape_iri)
            if (shape, RDF.type, SH.NodeShape) not in g:
                return []
        else:
            shape = next(g.subjects(RDF.type, SH.NodeShape), None)
            if shape is None:
                return []

        return [
            prop
            for prop in (
                self._parse_property(g, p) for p in g.objects(shape, SH.property)
            )
            if prop
        ]

    def parse_processor_metadata(self, ttl_string: str) -> dict:
        g = parsed_graph(ttl_string)
        if g is None:
            raise ValueError("not turtle")

        metadata = {
            "iri": None,
            "label": None,
            "comment": None,
            "class": None,
            "file": None,
            "entrypoint": None,
        }

        for s in g.subjects(RDFC.jsImplementationOf, RDFC.Processor):
            # the subject IRI identifies the component this TTL implements, and
            # is the join key against the contract catalog
            metadata["iri"] = str(s)
            metadata["label"] = str(g.value(s, RDFS.label) or "")
            metadata["comment"] = str(g.value(s, RDFS.comment) or "")
            metadata["class"] = str(g.value(s, RDFC["class"]) or "")
            file_val = g.value(s, RDFC.file)
            if file_val:
                metadata["file"] = str(file_val)
            entry_val = g.value(s, RDFC.entrypoint)
            if entry_val:
                metadata["entrypoint"] = str(entry_val)
            break

        return metadata

    def _parse_property(self, g: Graph, prop_node) -> ShaclProperty | None:
        name_val = g.value(prop_node, SH.name)
        if not name_val:
            return None

        name = str(name_val)
        path_uri = g.value(prop_node, SH.path)
        path = _uri_to_prefixed(path_uri, g) if path_uri else ""

        datatype_uri = g.value(prop_node, SH.datatype)
        datatype = _uri_to_prefixed(datatype_uri, g) if datatype_uri else None

        class_uri = g.value(prop_node, SH["class"])
        class_ref = _uri_to_prefixed(class_uri, g) if class_uri else None

        min_count_val = g.value(prop_node, SH.minCount)
        min_count = int(min_count_val) if min_count_val is not None else 0

        max_count_val = g.value(prop_node, SH.maxCount)
        max_count = int(max_count_val) if max_count_val is not None else None

        in_node = g.value(prop_node, SH["in"])
        in_values = []
        if in_node:
            in_values = [str(item) for item in Collection(g, in_node)]

        input_field_type = self._map_input_field_type(
            datatype_uri, class_uri, in_values
        )

        return ShaclProperty(
            name=name,
            path=path,
            datatype=datatype,
            class_ref=class_ref,
            min_count=min_count,
            max_count=max_count,
            in_values=in_values,
            input_field_type=input_field_type,
        )

    def _map_input_field_type(self, datatype_uri, class_uri, in_values):
        if in_values:
            return "baseSelectField"

        if class_uri and class_uri in WRITER_CLASSES:
            return "hasWriterField"

        if class_uri and class_uri in CHANNEL_CLASSES:
            return "channelRelationField"

        if datatype_uri and datatype_uri in DATATYPE_TO_INPUT_FIELD:
            return DATATYPE_TO_INPUT_FIELD[datatype_uri]

        return "baseTextField"


# Every derivation above walks the same read-only graph to the same answer, and
# a listing asks for it repeatedly: once per processor a repository declares
# (they share the file) and again on the next request. The graph parse is
# already shared (`graphs.parsed_graph`); the walk over it was not, and it was
# the larger half of what a warm listing spent in `rdflib`.
#
# Keyed on the document text and the class asked about, so nothing goes stale:
# a changed file is a different key. The cached value is a tuple and the methods
# hand out a fresh list of it, so a caller that empties its own copy does not
# empty everyone's.
#
# Sized like the graph cache it sits on top of: an entry beyond the number of
# (document, class) pairs in play is one that will never be asked for again.
_DERIVATION_CACHE_SIZE = 512


@lru_cache(maxsize=_DERIVATION_CACHE_SIZE)
def _parse(ttl_string: str) -> tuple:
    return tuple(
        (class_name, tuple(properties))
        for class_name, properties in ShaclParser()._walk_shapes(ttl_string).items()
    )


@lru_cache(maxsize=_DERIVATION_CACHE_SIZE)
def _main_processor_properties(ttl_string: str, target_class=None) -> tuple:
    return tuple(
        ShaclParser()._walk_main_processor_properties(ttl_string, target_class)
    )


@lru_cache(maxsize=_DERIVATION_CACHE_SIZE)
def _shape_properties(ttl_string: str, shape_iri: str | None = None) -> tuple:
    return tuple(ShaclParser()._walk_shape_properties(ttl_string, shape_iri))
