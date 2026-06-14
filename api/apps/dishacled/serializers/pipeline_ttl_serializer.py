import re

from rdflib import BNode, Graph, Literal, Namespace, RDF, URIRef

RDFC = Namespace("https://w3id.org/rdf-connect#")

RUNTIME_TO_RUNNER = {
    "ts": RDFC.NodeRunner,
    "py": RDFC.PyRunner,
    "jvm": RDFC.JvmRunner,
}

SH = Namespace("http://www.w3.org/ns/shacl#")
XSD_STRING = URIRef("http://www.w3.org/2001/XMLSchema#string")

CHANNEL_CLASSES = {RDFC.Reader, RDFC.Writer, RDFC.Channel}


def _slugify(value):
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _get_metadata_value(entity, key):
    for item in entity.get("metadata", []):
        if item.get("key") == key:
            return item.get("value")
    return None


class _ShapeIndex:
    """Property bindings (sh:name -> path/datatype/class) for one processor shape."""

    def __init__(self, target_class, properties):
        self.target_class = target_class
        self.properties = properties  # name -> {path, datatype, class}

    @classmethod
    def from_ttl(cls, raw_ttl):
        g = Graph()
        g.parse(data=raw_ttl, format="turtle")

        # The processor class is the subject of a `rdfc:*ImplementationOf
        # rdfc:Processor` triple (e.g. rdfc:HttpFetch). Prefer the NodeShape
        # whose sh:targetClass is that class, so multi-shape processor files
        # (HttpFetch + HttpFetchAuth + HttpFetchOptions) bind the right shape.
        processor_classes = {
            s
            for s, p, o in g
            if o == RDFC.Processor and str(p).split("#")[-1].endswith("ImplementationOf")
        }

        shapes = []
        for node_shape in g.subjects(RDF.type, SH.NodeShape):
            target_class = g.value(node_shape, SH.targetClass)
            if not target_class:
                continue
            properties = {}
            for prop_node in g.objects(node_shape, SH.property):
                name = g.value(prop_node, SH.name)
                path = g.value(prop_node, SH.path)
                if not name or not path:
                    continue
                properties[str(name)] = {
                    "path": path,
                    "datatype": g.value(prop_node, SH.datatype),
                    "class": g.value(prop_node, SH["class"]),
                }
            shapes.append(cls(target_class, properties))

        if not shapes:
            return None
        for shape in shapes:
            if shape.target_class in processor_classes:
                return shape
        return shapes[0]


class PipelineTtlSerializer:
    """Serialize an Elody pipeline entity graph to a runnable RDF-Connect pipeline.ttl.

    Config values are read from the metadata on the pipeline's hasProcessor
    relations; predicates and datatypes are recovered from the SHACL shape
    stored as rawTtl on each githubProcessor entity.
    """

    def __init__(self, base_uri):
        self.base_uri = base_uri

    def serialize(self, pipeline, processors):
        g = Graph()
        g.bind("rdfc", RDFC)

        pipeline_uri = URIRef(self.base_uri)
        g.add((pipeline_uri, RDF.type, RDFC.Pipeline))

        runner_groups = {}  # runner class URIRef -> [stage URIRef]
        channels = set()

        for relation in pipeline.get("relations", []):
            if relation.get("type") != "hasProcessor":
                continue

            processor = processors.get(relation.get("key"))
            if not processor:
                continue

            raw_ttl = (processor.get("data") or {}).get("rawTtl")
            if not raw_ttl:
                continue
            shape = _ShapeIndex.from_ttl(raw_ttl)
            if not shape:
                continue

            stage_uri = self._stage_uri(processor)
            g.add((stage_uri, RDF.type, shape.target_class))

            runner = RUNTIME_TO_RUNNER.get(
                _get_metadata_value(processor, "runtime"), RDFC.NodeRunner
            )
            runner_groups.setdefault(runner, []).append(stage_uri)

            for item in relation.get("metadata", []):
                binding = shape.properties.get(item.get("key"))
                value = item.get("value")
                if not binding or value in (None, ""):
                    continue

                if binding["class"] in CHANNEL_CLASSES:
                    channel_uri = URIRef(self.base_uri + _slugify(value))
                    g.add((stage_uri, binding["path"], channel_uri))
                    channels.add(channel_uri)
                else:
                    datatype = binding["datatype"]
                    if datatype == XSD_STRING:
                        datatype = None  # plain literal, identical in RDF 1.1
                    g.add(
                        (stage_uri, binding["path"], Literal(value, datatype=datatype))
                    )

        for runner, stages in runner_groups.items():
            group = BNode()
            g.add((pipeline_uri, RDFC.consistsOf, group))
            g.add((group, RDFC.instantiates, runner))
            for stage in stages:
                g.add((group, RDFC.processor, stage))

        for channel_uri in channels:
            g.add((channel_uri, RDF.type, RDFC.Reader))
            g.add((channel_uri, RDF.type, RDFC.Writer))

        return g.serialize(format="turtle")

    def _stage_uri(self, processor):
        name = _get_metadata_value(processor, "name") or processor.get(
            "_id", "stage"
        )
        return URIRef(self.base_uri + _slugify(name))
