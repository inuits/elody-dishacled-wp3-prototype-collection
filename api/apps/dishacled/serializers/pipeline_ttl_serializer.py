import re

from rdflib import BNode, Graph, Literal, Namespace, RDF, URIRef

from apps.dishacled.pipeline.connections import (
    connections_for_pipeline,
    nest_metadata,
)

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
    """Property bindings for one processor shape, with nested shapes resolved.

    `properties` maps sh:name -> {path, datatype, class, nested}. A property
    whose sh:class / sh:node points to another local NodeShape carries that
    shape as `nested` (an _ShapeIndex), recursively — so nested config
    (shui:DetailsEditor) round-trips into nested RDF-Connect blank nodes.
    """

    def __init__(self, target_class, properties):
        self.target_class = target_class
        self.properties = properties  # name -> {path, datatype, class, nested}

    @classmethod
    def from_ttl(cls, raw_ttl):
        g = Graph()
        try:
            g.parse(data=raw_ttl, format="turtle")
        except Exception:
            # a processor with unparseable TTL should not break the whole
            # pipeline export; its stage is skipped
            return None

        shapes_by_class = {}
        for node_shape in g.subjects(RDF.type, SH.NodeShape):
            target_class = g.value(node_shape, SH.targetClass)
            if target_class is not None:
                shapes_by_class[target_class] = node_shape

        # The processor class is the subject of a `rdfc:*ImplementationOf
        # rdfc:Processor` triple (e.g. rdfc:HttpFetch). Prefer the NodeShape
        # whose sh:targetClass is that class, so multi-shape processor files
        # (HttpFetch + HttpFetchAuth + HttpFetchOptions) bind the right shape.
        processor_classes = {
            s
            for s, p, o in g
            if o == RDFC.Processor and str(p).split("#")[-1].endswith("ImplementationOf")
        }

        main_class = next(
            (c for c in processor_classes if c in shapes_by_class), None
        )
        if main_class is None:
            main_class = next(iter(shapes_by_class), None)
        if main_class is None:
            return None

        return cls._build(g, main_class, shapes_by_class, {main_class})

    @classmethod
    def _build(cls, g, target_class, shapes_by_class, visited):
        node_shape = shapes_by_class[target_class]
        properties = {}
        for prop_node in g.objects(node_shape, SH.property):
            name = g.value(prop_node, SH.name)
            path = g.value(prop_node, SH.path)
            if not name or not path:
                continue
            class_ref = g.value(prop_node, SH["class"])
            node_ref = g.value(prop_node, SH.node)

            nested = None
            nested_class = None
            if node_ref is not None:
                nested_class = g.value(node_ref, SH.targetClass)
            elif (
                class_ref is not None
                and class_ref not in CHANNEL_CLASSES
                and class_ref in shapes_by_class
            ):
                nested_class = class_ref
            if (
                nested_class is not None
                and nested_class in shapes_by_class
                and nested_class not in visited
            ):
                nested = cls._build(
                    g, nested_class, shapes_by_class, visited | {nested_class}
                )

            properties[str(name)] = {
                "path": path,
                "datatype": g.value(prop_node, SH.datatype),
                "class": class_ref,
                "nested": nested,
            }
        return cls(target_class, properties)


def emit_config_values(g, subject, shape, values, base_uri, channels):
    """Emit one shape's config values onto `subject`, recursing into nested nodes.

    Shared by both exports: the RDF-Connect pipeline puts these directly on the
    stage, the toolchain pipeline definition puts them inside a `tcs:embedded`
    block, but the mapping from a form value to a predicate, a datatype and a
    channel IRI is the same in either case.
    """
    for name, binding in shape.properties.items():
        if name not in values:
            continue
        value = values[name]

        if binding["nested"] is not None:
            if not isinstance(value, dict):
                continue
            nested_node = BNode()
            emit_config_values(
                g, nested_node, binding["nested"], value, base_uri, channels
            )
            # only attach if the nested node carries any triples
            if next(g.predicate_objects(nested_node), None) is not None:
                g.add((subject, binding["path"], nested_node))
            continue

        if value in (None, ""):
            continue

        if binding["class"] in CHANNEL_CLASSES:
            channel_uri = URIRef(base_uri + _slugify(value))
            g.add((subject, binding["path"], channel_uri))
            channels.add(channel_uri)
        else:
            datatype = binding["datatype"]
            if datatype == XSD_STRING:
                datatype = None  # plain literal, identical in RDF 1.1
            g.add((subject, binding["path"], Literal(value, datatype=datatype)))


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
        stages = {}  # processor key -> (stage URIRef, _ShapeIndex)

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
            stages[relation["key"]] = (stage_uri, shape)

            # A dataset is a source of data, not an implementation, so it is
            # emitted as a stage that can be wired but is never handed to a
            # runner to instantiate.
            if (processor.get("data") or {}).get("componentKind") != "dataset":
                runner = RUNTIME_TO_RUNNER.get(
                    _get_metadata_value(processor, "runtime"), RDFC.NodeRunner
                )
                runner_groups.setdefault(runner, []).append(stage_uri)

            # Flat dotted-key metadata (url, options.method, options.auth.type)
            # -> nested value dict, then emit following the (nested) shape.
            values = nest_metadata(relation.get("metadata", []))
            self._emit_values(g, stage_uri, shape, values, channels)

        self._emit_connections(g, pipeline, processors, stages, channels)

        for runner, stages_of_runner in runner_groups.items():
            group = BNode()
            g.add((pipeline_uri, RDFC.consistsOf, group))
            g.add((group, RDFC.instantiates, runner))
            for stage in stages_of_runner:
                g.add((group, RDFC.processor, stage))

        for channel_uri in channels:
            g.add((channel_uri, RDF.type, RDFC.Reader))
            g.add((channel_uri, RDF.type, RDFC.Writer))

        return g.serialize(format="turtle")

    def _emit_connections(self, g, pipeline, processors, stages, channels):
        """Bind each declared connection's two ports to one shared channel.

        The connection is authoritative: a channel value typed into the config
        form by hand is replaced, so the exported pipeline always reflects the
        links the user drew rather than two names that may have drifted apart.
        """
        for connection in connections_for_pipeline(pipeline, processors):
            source = stages.get(connection.source)
            target = stages.get(connection.target)
            if not source or not target:
                continue

            source_uri, source_shape = source
            target_uri, target_shape = target
            source_path = (source_shape.properties.get(connection.source_port) or {}).get(
                "path"
            )
            target_path = (target_shape.properties.get(connection.target_port) or {}).get(
                "path"
            )
            if source_path is None or target_path is None:
                continue

            channel_uri = URIRef(self.base_uri + _slugify(connection.channel))
            g.remove((source_uri, source_path, None))
            g.add((source_uri, source_path, channel_uri))
            g.remove((target_uri, target_path, None))
            g.add((target_uri, target_path, channel_uri))
            channels.add(channel_uri)

    def _emit_values(self, g, subject, shape, values, channels):
        emit_config_values(g, subject, shape, values, self.base_uri, channels)

    def _stage_uri(self, processor):
        name = _get_metadata_value(processor, "name") or processor.get(
            "_id", "stage"
        )
        return URIRef(self.base_uri + _slugify(name))
