from dataclasses import dataclass, field
from rdflib import Graph, Namespace, RDF, Literal, URIRef
from rdflib.collection import Collection


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
    for prefix, namespace in graph.namespaces():
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
        g = Graph()
        g.parse(data=ttl_string, format="turtle")

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

    def parse_processor_metadata(self, ttl_string: str) -> dict:
        g = Graph()
        g.parse(data=ttl_string, format="turtle")

        metadata = {
            "label": None,
            "comment": None,
            "class": None,
            "file": None,
            "entrypoint": None,
        }

        for s in g.subjects(RDFC.jsImplementationOf, RDFC.Processor):
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
