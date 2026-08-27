"""Reading a `tcs:PipelineDefinition` back as an Elody pipeline entity.

The inverse of `pipeline_definition_serializer.py`, which is what makes the
central triple store the source of truth for pipelines rather than a copy of
one: Elody's list and detail views render a pipeline that exists only in the
store, and every edit is written back there.

The definition is read as a **whole named graph**, not as a bag of properties
per subject. A pipeline is a graph, not a record -- its steps, their config
blocks and its channels are separate subjects, most of them blank nodes -- so
the SPARQL engine hands this serializer the graph it CONSTRUCTed and the
mapping happens here, where the vocabulary lives.

Nothing outside the graph is consulted. In particular no component is looked up
on GitHub: everything the mapping needs is in the definition, because the
export ships a catalog fragment for the components it uses.

    <pipeline> a tcs:PipelineDefinition ;   -> the entity
        dct:identifier "<id>" ;             -> _id
        rdfs:label / rdfs:comment .         -> metadata name / description

    <step> a tcs:InstancePipelineComponent ;      -> one hasProcessor relation
        prov:specializationOf <component> ;       -> relation key, via the
                                                     component's dct:identifier
        p-plan:hasInputVar [ tcs:embedded [...] ] -> relation metadata, with the
                                                     keys read off the config
                                                     shape in the fragment
        tcs:readsFrom / tcs:writesTo <channel> .  -> connections.<port>.from

Three things deliberately do not come back:

  * **Per-connection validation verdicts.** `connections.<port>.state` is
    Elody's reading of the chain, not part of the shared definition, and the
    store is shared. It is recomputed (`GET /pipelines/<id>/validation`) rather
    than published; see the "out of scope: UI state" line in the ticket.
  * **Authored step order.** A plan is a set of steps; the order that means
    anything is the one the channels describe, so steps come back in
    dependency order (producers before consumers) with an alphabetical
    tie-break, not in the order they were added.
  * **Metadata a config shape does not declare.** A definition can only carry
    what a predicate exists for. An unknown key was never published, so it
    cannot be read back.
"""

from rdflib import BNode, Graph, Literal, Namespace, RDF, RDFS, URIRef
from rdflib.namespace import XSD

from apps.dishacled.pipeline.connections import (
    CHANNEL_FIELD,
    CONNECTIONS_KEY,
    DATASET_OUTPUT_PORT,
    INSTANCE_FIELD,
    INSTANCE_SEPARATOR,
    PORT_SEPARATOR,
    PROCESSOR_RELATION,
    SOURCE_FIELD,
    channel_name_between,
    slugify,
)
from apps.dishacled.serializers.pipeline_ttl_serializer import (
    CHANNEL_CLASSES,
    shape_index_for,
)


TCS = Namespace("https://w3id.org/toolchain#")
DCAT = Namespace("http://www.w3.org/ns/dcat#")
DCT = Namespace("http://purl.org/dc/terms/")
PROV = Namespace("http://www.w3.org/ns/prov#")
PPLAN = Namespace("http://purl.org/net/p-plan#")
SH = Namespace("http://www.w3.org/ns/shacl#")

PIPELINE_TYPE = "pipeline"


def _instance_of(node) -> str:
    """A step's id: the last segment of its IRI.

    The export writes `.../step/<instance>`, so the identity travels in the
    document already and needs no term of its own.
    """
    return str(node).rstrip("/").rsplit("/", 1)[-1]


def _as_graph(payload) -> Graph | None:
    """The definition graph, however the engine handed it over."""
    graph = payload.get("graph") if isinstance(payload, dict) else None
    if isinstance(graph, Graph):
        return graph
    turtle = payload.get("turtle") if isinstance(payload, dict) else None
    if not turtle:
        return None
    parsed = Graph()
    try:
        parsed.parse(data=turtle, format="turtle")
    except Exception:
        return None
    return parsed


def _value_of(literal):
    """One config value, in the form the entity held it.

    The lexical form is kept rather than the Python object, because that is
    what round-trips: `"10000"` typed as an integer comes back as `"10000"`,
    not `10000`, and the next export retypes it from the shape exactly as
    before. A boolean is the exception -- a checkbox field is stored as a
    boolean, and `"true"` would not render as a ticked box.
    """
    if isinstance(literal, Literal) and literal.datatype == XSD.boolean:
        return bool(literal.toPython())
    return str(literal)


class _Component:
    """One component as the catalog fragment describes it."""

    def __init__(self, iri, identifier, shape):
        self.iri = iri
        self.identifier = identifier
        self.shape = shape
        # predicate -> (metadata key, binding), the inverse of what
        # `emit_config_values` wrote
        self.by_path = {}
        if shape is not None:
            self._index(shape, self.by_path)

    @staticmethod
    def _index(shape, target):
        for name, binding in shape.properties.items():
            target[binding["path"]] = (name, binding)

    def port_named(self, predicate):
        entry = self.by_path.get(predicate)
        return entry[0] if entry else None


class PipelineSerializer:
    """Both directions between a pipeline entity and its definition graph."""

    # -- store -> Elody ---------------------------------------------------

    def from_sparql_to_elody(self, subject, **kwargs):
        graph = _as_graph(subject)
        if graph is None:
            return {}

        pipeline = self._pipeline_subject(graph, subject)
        if pipeline is None:
            return {}

        identifier = self._identifier_of(graph, pipeline)
        if not identifier:
            # Without an id there is nothing to address the pipeline by, the
            # same rule the engine applies to every other resource.
            return {}

        components = self._components(graph)
        steps = self._steps(graph, pipeline, components)
        channel_base = str(pipeline) + "/"

        relations = self._relations(graph, steps, components, channel_base)

        return {
            "_id": identifier,
            "identifiers": [identifier, str(pipeline)],
            "type": PIPELINE_TYPE,
            "metadata": self._pipeline_metadata(graph, pipeline),
            "relations": relations,
        }

    def _pipeline_subject(self, graph, payload):
        """The plan this graph is about.

        The engine names the subject it paged in; trust that when it is really
        in the graph, and fall back to the only `tcs:PipelineDefinition` there
        is, so the serializer is usable on a graph on its own.
        """
        named = (payload or {}).get("iri")
        if named:
            candidate = URIRef(str(named))
            if (candidate, RDF.type, TCS.PipelineDefinition) in graph:
                return candidate
        return next(graph.subjects(RDF.type, TCS.PipelineDefinition), None)

    @staticmethod
    def _identifier_of(graph, subject):
        identifier = graph.value(subject, DCT.identifier)
        if identifier:
            return str(identifier)
        # An older definition, published before the identifier was carried:
        # the IRI still ends in the id it was built from.
        tail = str(subject).rstrip("/").rsplit("/", 1)[-1]
        return tail or ""

    @staticmethod
    def _pipeline_metadata(graph, pipeline):
        metadata = []
        for predicate, key in ((RDFS.label, "name"), (RDFS.comment, "description")):
            value = graph.value(pipeline, predicate)
            if value is not None:
                metadata.append({"key": key, "value": str(value)})
        return metadata

    # -- the catalog fragment ---------------------------------------------

    def _components(self, graph) -> dict:
        """`component IRI -> _Component`, from the fragment travelling along.

        The config shape is what names the config fields: the definition
        carries predicates, the entity carries the `sh:name` the form was built
        from, and the shape is the only thing that relates the two.
        """
        shapes_by_class = {
            target: shape
            for shape in graph.subjects(RDF.type, SH.NodeShape)
            if (target := graph.value(shape, SH.targetClass)) is not None
        }

        components = {}
        subjects = set(graph.subjects(RDF.type, TCS.PipelineComponent))
        subjects |= set(graph.subjects(RDF.type, DCAT.Dataset))
        for iri in subjects:
            shape_node = self._config_shape(graph, iri)
            target_class = (
                graph.value(shape_node, SH.targetClass) if shape_node else None
            )
            shape = (
                shape_index_for(graph, target_class, shapes_by_class)
                if target_class is not None
                else None
            )
            components[iri] = _Component(
                iri, self._identifier_of(graph, iri), shape
            )
        return components

    @staticmethod
    def _config_shape(graph, component):
        for relation in graph.objects(component, DCAT.qualifiedRelation):
            if graph.value(relation, DCAT.hadRole) != TCS.configShape:
                continue
            shape = graph.value(relation, DCT.relation)
            if shape is not None:
                return shape
        return None

    # -- steps -------------------------------------------------------------

    def _steps(self, graph, pipeline, components) -> list:
        """Every stage of the plan, in dependency order.

        A dataset is a stage too: it is not a `tcs:InstancePipelineComponent`
        because it is not deployable, but Elody stores it as an ordinary
        `hasProcessor` relation and it is the producer at the head of a chain.
        """
        steps = []
        for node in graph.subjects(RDF.type, TCS.InstancePipelineComponent):
            if (node, PPLAN.isStepOfPlan, pipeline) not in graph:
                continue
            component = components.get(graph.value(node, PROV.specializationOf))
            if component is None or not component.identifier:
                continue
            steps.append(
                {
                    "node": node,
                    "component": component,
                    "key": component.identifier,
                    "instance": _instance_of(node),
                    "label": str(graph.value(node, RDFS.label) or ""),
                    "embedded": self._embedded(graph, node),
                }
            )

        for node in graph.objects(pipeline, DCT.source):
            component = components.get(node)
            if component is None or not component.identifier:
                continue
            steps.append(
                {
                    "node": node,
                    "component": component,
                    "key": component.identifier,
                    "label": str(graph.value(node, RDFS.label) or ""),
                    "embedded": None,
                }
            )

        return self._in_dependency_order(graph, steps)

    @staticmethod
    def _embedded(graph, node):
        config = graph.value(node, PPLAN.hasInputVar)
        if config is None:
            return None
        return graph.value(config, TCS.embedded)

    @staticmethod
    def _in_dependency_order(graph, steps) -> list:
        """Producers before the steps they feed, alphabetical otherwise.

        The definition records no authored order, so inventing one from
        iteration order would make the processor list shuffle between reads.
        What the graph does record is which stage feeds which, and that is the
        order the chain is read in.
        """
        writes = {
            id(step): set(graph.objects(step["node"], TCS.writesTo)) for step in steps
        }
        reads = {
            id(step): set(graph.objects(step["node"], TCS.readsFrom)) for step in steps
        }

        remaining = sorted(steps, key=lambda step: (step["label"], str(step["node"])))
        ordered = []
        satisfied = set()
        while remaining:
            ready = [
                step
                for step in remaining
                if not (reads[id(step)] - satisfied)
            ]
            if not ready:
                # a cycle, or a channel nothing in this plan writes to: take
                # the next one rather than dropping the rest of the pipeline
                ready = [remaining[0]]
            for step in ready:
                ordered.append(step)
                satisfied |= writes[id(step)]
                remaining.remove(step)
        return ordered

    # -- relations ---------------------------------------------------------

    def _relations(self, graph, steps, components, channel_base) -> list:
        by_channel_writer = {}
        for step in steps:
            for channel in graph.objects(step["node"], TCS.writesTo):
                by_channel_writer[channel] = step

        # Only a component used more than once needs its steps spelled out in
        # the key: one step is unambiguous as the component, which keeps the
        # ids of every existing pipeline exactly as they were.
        repeated = {
            key
            for key in (step["key"] for step in steps)
            if [s["key"] for s in steps].count(key) > 1
        }

        relations = []
        for step in steps:
            metadata = self._config_metadata(graph, step, channel_base)
            metadata += self._connection_metadata(
                graph, step, by_channel_writer, channel_base
            )
            instance = step.get("instance")
            if instance:
                # the step's identity, so a component used twice comes back as
                # two steps rather than one -- and so the connections that
                # name them keep pointing at the right one
                metadata.append({"key": INSTANCE_FIELD, "value": instance})
            relations.append(
                {
                    # keyed by the step, because that is what the UI addresses:
                    # one row per key, and a config form writes back to the
                    # relation whose key it was opened on
                    "key": (
                        f"{step['key']}{INSTANCE_SEPARATOR}{instance}"
                        if instance and step["key"] in repeated
                        else step["key"]
                    ),
                    "type": PROCESSOR_RELATION,
                    "metadata": metadata,
                }
            )
        return relations

    def _config_metadata(self, graph, step, channel_base) -> list:
        if step["embedded"] is None:
            return []
        return self._decode(
            graph, step["embedded"], step["component"].by_path, "", channel_base
        )

    def _decode(self, graph, node, by_path, prefix, channel_base) -> list:
        """One config block as flat, dotted metadata keys.

        The dotted key is the same convention the config form writes
        (`options.auth.type`), so a value read out of the store is
        indistinguishable from one typed into the form.
        """
        metadata = []
        for predicate, value in graph.predicate_objects(node):
            entry = by_path.get(predicate)
            if entry is None:
                continue
            name, binding = entry
            key = f"{prefix}{name}"

            if binding["nested"] is not None:
                if not isinstance(value, (BNode, URIRef)):
                    continue
                nested = {}
                _Component._index(binding["nested"], nested)
                metadata += self._decode(
                    graph, value, nested, f"{key}.", channel_base
                )
                continue

            if binding["class"] in CHANNEL_CLASSES and isinstance(value, URIRef):
                metadata.append(
                    {"key": key, "value": self._channel_name(value, channel_base)}
                )
                continue

            metadata.append({"key": key, "value": _value_of(value)})
        return sorted(metadata, key=lambda item: item["key"])

    def _connection_metadata(
        self, graph, step, by_channel_writer, channel_base
    ) -> list:
        """The `connections.<port>.*` keys for everything feeding this step."""
        metadata = []
        for channel in sorted(graph.objects(step["node"], TCS.readsFrom), key=str):
            producer = by_channel_writer.get(channel)
            if producer is None or producer is step:
                continue
            target_port = self._port_for(graph, step, channel)
            source_port = self._port_for(graph, producer, channel)
            if not target_port or not source_port:
                continue

            metadata.append(
                {
                    "key": f"{CONNECTIONS_KEY}.{target_port}.{SOURCE_FIELD}",
                    # the producing *step*, not its component: a component used
                    # twice is two producers, and naming the component would
                    # leave which one feeds this port to a guess
                    "value": (
                        f"{producer.get('instance') or producer['key']}"
                        f"{PORT_SEPARATOR}{source_port}"
                    ),
                }
            )
            channel_name = self._channel_name(channel, channel_base)
            if channel_name != self._default_channel(
                producer, source_port, step, target_port
            ):
                # the default is derived, not stored: writing it out would
                # add a key the pipeline never had
                metadata.append(
                    {
                        "key": f"{CONNECTIONS_KEY}.{target_port}.{CHANNEL_FIELD}",
                        "value": channel_name,
                    }
                )
        return metadata

    def _port_for(self, graph, step, channel):
        """Which port of this step is bound to that channel.

        The embedded config is what says so: the export writes the channel both
        as the `tcs:readsFrom` / `tcs:writesTo` annotation and as the value of
        the port's own property, and only the latter names the port.
        """
        embedded = step["embedded"]
        if embedded is not None:
            for predicate, value in graph.predicate_objects(embedded):
                if value != channel:
                    continue
                if name := step["component"].port_named(predicate):
                    return name
        if step["embedded"] is None and (step["node"], TCS.writesTo, channel) in graph:
            # a dataset has no config shape, so it has no property to name; it
            # is pure source and carries exactly one, synthetic, output port
            return DATASET_OUTPUT_PORT
        return None

    @staticmethod
    def _default_channel(producer, source_port, consumer, target_port) -> str:
        """What the channel would be called if nobody named it.

        Derived from the two step ids, the same way the export derives it --
        otherwise a channel between two steps of one component looks
        "non-default" here and is written back as an explicit name.
        """
        return channel_name_between(
            producer.get("instance") or producer["label"],
            source_port,
            consumer.get("instance") or consumer["label"],
            target_port,
        )

    @staticmethod
    def _channel_name(channel, channel_base) -> str:
        text = str(channel)
        if text.startswith(channel_base):
            return text[len(channel_base) :]
        return slugify(text.rsplit("/", 1)[-1])

    # -- Elody -> store ----------------------------------------------------

    def from_elody_to_sparql(self, entity, **kwargs):
        """The definition to store for this pipeline, as turtle.

        An empty document means "this pipeline does not belong in the store",
        which is how an incompatible chain is withdrawn rather than published --
        the same rule both export routes answer 409 with. See
        `pipeline/publication.py`.
        """
        from apps.dishacled.pipeline import publication

        return publication.definition_for_store(entity)

    def from_elody_filter_to_elody_filter(self, filters, **kwargs):
        """Translate a list filter into the engine's restrictions.

        Named for `SCHEMA_TYPE`, which is the convention the framework looks
        the translator up by (`from_<spec>_filter_to_<SCHEMA_TYPE>_filter`) and
        which every wrapper type in every client follows. It reads oddly here
        because a pipeline document *is* an elody document -- what is external
        is its storage, not its schema -- but matching the convention is what
        keeps the framework free of a special case.
        """

        restrictions = {}
        for filter in filters or []:
            if filter.get("type") != "selection":
                continue
            if "identifiers" not in str(filter.get("key", [])):
                continue
            value = filter.get("value")
            if value is None:
                ids = []
            elif isinstance(value, list):
                ids = value
            else:
                ids = [value]
            # An explicit selection restricts to exactly these ids, so an empty
            # list means "nothing matches". Same rule as the alert serializer.
            restrictions["ids"] = ids
        return restrictions
