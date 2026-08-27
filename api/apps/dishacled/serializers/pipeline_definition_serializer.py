"""Serialize an Elody pipeline as a toolchain `tcs:PipelineDefinition`.

This is the *input* of the DiSHACLed toolchain pipeline generator
(thcarsten/toolchain-specification, "pipeline generator"), not a runnable
pipeline: the generator reads a definition plus a component catalog and
compiles the docker-compose project and the framework configuration files --
including the RDF-Connect `pipeline.ttl` that `pipeline_ttl_serializer.py`
produces directly. Exporting the definition instead is what makes an
Elody-composed pipeline deployable through the toolchain rather than only
runnable by hand.

The vocabulary follows `pipeline generator/data/pipeline_definition.ttl`:

    <pipeline> a tcs:PipelineDefinition .

    <step> a tcs:InstancePipelineComponent ;
        prov:specializationOf <component> ;
        p-plan:isStepOfPlan <pipeline> ;
        p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:embedded [ ... ] ] ;
        tcs:readsFrom <channel> ;
        tcs:writesTo <channel> .

A definition only *names* its components; their identity, dependencies and
config shapes live in the catalog. Elody knows components the toolchain
catalog does not (anything discovered on GitHub, plus the interim contract
catalog), so the export carries a catalog fragment for the components it uses.
It is a fragment on purpose: the framework infrastructure it depends on --
`rdfc:Orchestrator` and its `tcs:DockerComposeConfig` / `tcs:DockerImageConfig`
-- is stable and already declared upstream, so the export is meant to be merged
with the toolchain catalog rather than to replace it.

Two things are known not to round-trip:

  * A `dcat:Dataset` is a source of data, not a deployable component, so it
    cannot be a step. It is declared as `dcterms:source` of the plan and keeps
    the `tcs:writesTo` annotation naming the channel it feeds, but nothing
    writes to that channel in the generated project.
  * The demo components in the interim contract catalog carry placeholder
    package coordinates (see `shacl/catalog/contracts.ttl`); the emitted
    manifest entries are correctly shaped but not installable.
"""

from rdflib import BNode, Graph, Literal, Namespace, RDF, RDFS, URIRef

from apps.dishacled.pipeline.connections import (
    PROCESSOR_RELATION,
    connections_for_pipeline,
    instances_of,
    nest_metadata,
)
from apps.dishacled.serializers.component_catalog_serializer import (
    ComponentCatalogSerializer,
    add_identifier,
)
from apps.dishacled.serializers.pipeline_ttl_serializer import (
    _ShapeIndex,
    _get_metadata_value,
    _slugify,
    emit_config_values,
)


TCS = Namespace("https://w3id.org/toolchain#")
RDFC = Namespace("https://w3id.org/rdf-connect#")
DCAT = Namespace("http://www.w3.org/ns/dcat#")
DCT = Namespace("http://purl.org/dc/terms/")
OWL = Namespace("http://www.w3.org/2002/07/owl#")
SPDX = Namespace("http://spdx.org/rdf/terms#")
PROV = Namespace("http://www.w3.org/ns/prov#")
PPLAN = Namespace("http://purl.org/net/p-plan#")
SH = Namespace("http://www.w3.org/ns/shacl#")

HEADER = """\
# Pipeline definition for the DiSHACLed toolchain pipeline generator
# (thcarsten/toolchain-specification, "pipeline generator").
#
# Exported from Elody. Load it alongside the toolchain component catalog: the
# framework infrastructure it relies on (rdfc:Orchestrator and its
# docker-compose / docker-image configs) is declared there, not here. The
# catalog fragment below covers only the components Elody knows about.
"""

DATASET_NOTE = """\
# A dataset is a source of data, not a deployable component: it is declared as
# a source of the plan and keeps the channel annotation, but the generator will
# not start anything that writes to that channel.
"""


class _Step:
    """One resolved pipeline stage, ready to be written out."""

    def __init__(self, key, uri, component_iri, document, shape, values):
        self.key = key
        self.uri = uri
        self.component_iri = component_iri
        self.document = document
        self.shape = shape
        self.values = values
        self.embedded = None


class PipelineDefinitionSerializer:
    """
    `base_uri` is the same `.../pipelines/<id>/` prefix the RDF-Connect export
    uses, so steps and channels of the two exports line up. The plan itself is
    named without the trailing slash and given a prefix binding: the generator
    interpolates the pipeline id straight into SPARQL and appends `_build` to
    it, so a full IRI has to be able to compact to a CURIE.
    """

    def __init__(self, base_uri, include_catalog=True):
        self.base_uri = base_uri
        self.include_catalog = include_catalog
        self.pipeline_uri = URIRef(base_uri.rstrip("/"))
        self.pipeline_namespace = str(self.pipeline_uri).rsplit("/", 1)[0] + "/"
        # keys of `hasProcessor` relations this serializer could not put in the
        # document. Read it: for the store, a missing step is a deleted step.
        self.unrepresented: list = []

    # -- entry point -------------------------------------------------------

    def serialize(self, pipeline, processors) -> str:
        g = Graph()
        g.bind("tcs", TCS)
        g.bind("rdfc", RDFC)
        g.bind("dcat", DCAT)
        g.bind("dct", DCT)
        g.bind("owl", OWL)
        g.bind("spdx", SPDX)
        g.bind("prov", PROV)
        g.bind("p-plan", PPLAN)
        g.bind("sh", SH)
        g.bind("pipeline", Namespace(self.pipeline_namespace))

        pipeline_uri = self.pipeline_uri
        g.add((pipeline_uri, RDF.type, TCS.PipelineDefinition))
        self._add_identifier(g, pipeline_uri, pipeline)
        self._add_label_and_comment(g, pipeline_uri, pipeline)

        self.unrepresented = []
        steps, datasets = self._resolve_stages(g, pipeline, processors)
        self._emit_connections(g, pipeline, processors, steps, datasets)

        has_dataset = bool(datasets)
        if self.include_catalog:
            self._emit_catalog(g, steps, datasets)

        body = g.serialize(format="turtle")
        header = HEADER + (DATASET_NOTE if has_dataset else "")
        return f"{header}\n{body}"

    # -- steps -------------------------------------------------------------

    def _resolve_stages(self, g, pipeline, processors):
        """Turn each hasProcessor relation into a step, or a dataset source."""
        pipeline_uri = self.pipeline_uri
        steps: dict[str, _Step] = {}
        datasets: dict[str, URIRef] = {}
        used_slugs: set[str] = set()

        for instance in instances_of(pipeline, processors):
            relation = instance.relation
            key = instance.key
            document = processors.get(key)
            if not document:
                # nothing behind the relation: the component could not be
                # fetched. Recorded, because a definition that quietly leaves a
                # step out is written over the pipeline it belongs to.
                self._unrepresented(key)
                continue
            data = document.get("data") or {}

            if data.get("componentKind") == "dataset":
                dataset_iri = data.get("componentIri")
                if dataset_iri:
                    dataset_uri = URIRef(dataset_iri)
                    datasets[instance.id] = dataset_uri
                    g.add((pipeline_uri, DCT.source, dataset_uri))
                    g.add((dataset_uri, RDF.type, DCAT.Dataset))
                    self._add_identifier(g, dataset_uri, document)
                    self._add_label_and_comment(g, dataset_uri, document)
                else:
                    self._unrepresented(key)
                continue

            shape = _ShapeIndex.from_ttl(
                data.get("rawTtl") or "", data.get("componentIri")
            )
            if not shape:
                # no config shape means no step: the component's own processor
                # file is what a step is built from, and this one has none
                self._unrepresented(key)
                continue
            component_iri = URIRef(data.get("componentIri") or shape.target_class)

            name = _get_metadata_value(document, "name") or key
            # the step's own id: unique within the pipeline, and the slug its
            # IRI ends in, which is how reading the definition back recovers it
            slug = self._unique_slug(instance.id, used_slugs)
            step = _Step(
                key=key,
                uri=URIRef(f"{self.base_uri}step/{slug}"),
                component_iri=component_iri,
                document=document,
                shape=shape,
                values=nest_metadata(relation.get("metadata", [])),
            )
            steps[instance.id] = step

            g.add((step.uri, RDF.type, TCS.InstancePipelineComponent))
            g.add((step.uri, PPLAN.isStepOfPlan, pipeline_uri))
            g.add((step.uri, PROV.specializationOf, component_iri))
            if name:
                g.add((step.uri, RDFS.label, Literal(name)))

        return steps, datasets

    def _unrepresented(self, key):
        if key and key not in self.unrepresented:
            self.unrepresented.append(key)

    @staticmethod
    def _unique_slug(slug, used):
        candidate, index = slug, 1
        while candidate in used:
            index += 1
            candidate = f"{slug}-{index}"
        used.add(candidate)
        return candidate

    def _embedded_node(self, g, step):
        """The `tcs:embedded` block of a step, created on first use.

        A step with no configured values gets no `p-plan:hasInputVar` at all --
        an empty config would validate but says nothing.
        """
        if step.embedded is None:
            config = BNode()
            step.embedded = BNode()
            g.add((step.uri, PPLAN.hasInputVar, config))
            g.add((config, RDF.type, TCS.PipelineConfig))
            g.add((config, TCS.embedded, step.embedded))
        return step.embedded

    # -- channels ----------------------------------------------------------

    def _emit_connections(self, g, pipeline, processors, steps, datasets):
        """Write the config values, then bind the declared connections.

        Order matters: a channel name typed into the config form by hand is
        overwritten by the connection the user actually drew, the same way the
        RDF-Connect export resolves the two.
        """
        channels: set[URIRef] = set()

        for step in steps.values():
            scratch = Graph()
            emit_config_values(
                scratch, step.uri, step.shape, step.values, self.base_uri, channels
            )
            if len(scratch) == 0:
                continue
            embedded = self._embedded_node(g, step)
            for _, predicate, obj in scratch.triples((step.uri, None, None)):
                g.add((embedded, predicate, obj))
            # nested config nodes keep their own triples
            for subject, predicate, obj in scratch:
                if subject != step.uri:
                    g.add((subject, predicate, obj))

        for connection in connections_for_pipeline(pipeline, processors):
            channel_uri = URIRef(self.base_uri + _slugify(connection.channel))
            source_bound = self._bind_port(
                g, steps, datasets, connection.source, connection.source_port,
                channel_uri, TCS.writesTo,
            )
            target_bound = self._bind_port(
                g, steps, datasets, connection.target, connection.target_port,
                channel_uri, TCS.readsFrom,
            )
            if source_bound and target_bound:
                channels.add(channel_uri)

        for channel_uri in channels:
            g.add((channel_uri, RDF.type, TCS.Channel))

    def _bind_port(self, g, steps, datasets, key, port, channel_uri, annotation):
        """Point one end of a connection at the shared channel.

        The annotation (`tcs:readsFrom` / `tcs:writesTo`) is what the generator
        reasons over; the matching predicate inside the embedded config is what
        the framework itself reads at run time. Both are written, from the one
        connection, so they cannot drift.
        """
        dataset_uri = datasets.get(key)
        if dataset_uri is not None:
            g.add((dataset_uri, annotation, channel_uri))
            return True

        step = steps.get(key)
        if step is None:
            return False

        g.add((step.uri, annotation, channel_uri))
        path = (step.shape.properties.get(port) or {}).get("path")
        if path is not None:
            embedded = self._embedded_node(g, step)
            g.remove((embedded, path, None))
            g.add((embedded, path, channel_uri))
        return True

    # -- catalog fragment --------------------------------------------------

    def _emit_catalog(self, g, steps, datasets):
        """Describe the components this definition names, in this document.

        The description itself is `ComponentCatalogSerializer`, which is also
        what Elody publishes into the store's catalog graph
        (`pipeline/catalog.py`): a component described in two spellings would
        be two things to keep in step. What is decided here is only whether the
        description travels *with* the definition -- see `include_catalog`.
        """
        catalog = ComponentCatalogSerializer(g)
        runners = set()
        for step in steps.values():
            runner = catalog.add(
                step.document, component_iri=step.component_iri, shape=step.shape
            )
            if runner is not None:
                runners.add(runner)
        catalog.declare_runners(runners)

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _add_identifier(g, subject, document):
        add_identifier(g, subject, document)

    @staticmethod
    def _add_label_and_comment(g, subject, document):
        name = _get_metadata_value(document, "name")
        if name:
            g.add((subject, RDFS.label, Literal(name)))
        description = _get_metadata_value(document, "description")
        if description:
            g.add((subject, RDFS.comment, Literal(description)))
