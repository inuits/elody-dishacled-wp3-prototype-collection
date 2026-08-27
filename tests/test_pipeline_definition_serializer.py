"""Tests for exporting an Elody pipeline as a toolchain `tcs:PipelineDefinition`.

This is the *input* format of the DiSHACLed toolchain pipeline generator
(thcarsten/toolchain-specification, "pipeline generator"), not a runnable
RDF-Connect pipeline -- the generator produces the latter from the former.

Target format, per `pipeline generator/data/pipeline_definition.ttl`:

    <pipeline> a tcs:PipelineDefinition ;
        rdfs:label "..." .

    <step> a tcs:InstancePipelineComponent ;
        prov:specializationOf <component> ;
        p-plan:isStepOfPlan <pipeline> ;
        p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:embedded [ ... ] ] ;
        tcs:readsFrom <channel> ;
        tcs:writesTo <channel> .

plus the catalog entries the steps specialize, so a definition exported from
Elody names components the generator can resolve.
"""

import os
import sys
from pathlib import Path

import pytest
from rdflib import Graph, Literal, Namespace, RDF, RDFS, URIRef
from rdflib.namespace import XSD

from apps.dishacled.serializers.pipeline_definition_serializer import (
    PipelineDefinitionSerializer,
)


TCS = Namespace("https://w3id.org/toolchain#")
RDFC = Namespace("https://w3id.org/rdf-connect#")
DCAT = Namespace("http://www.w3.org/ns/dcat#")
DCT = Namespace("http://purl.org/dc/terms/")
OWL = Namespace("http://www.w3.org/2002/07/owl#")
SPDX = Namespace("http://spdx.org/rdf/terms#")
PROV = Namespace("http://www.w3.org/ns/prov#")
PPLAN = Namespace("http://purl.org/net/p-plan#")
EX = Namespace("http://example.org/example/")

BASE = "https://elody.local/pipelines/pipeline-1/"
PIPELINE_URI = URIRef(BASE.rstrip("/"))


POLLER_TTL = """\
@prefix rdfc: <https://w3id.org/rdf-connect#>.
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#>.
@prefix sh: <http://www.w3.org/ns/shacl#>.
@prefix xsd: <http://www.w3.org/2001/XMLSchema#>.

rdfc:HttpPoller rdfc:jsImplementationOf rdfc:Processor;
  rdfs:label "http poller";
  rdfc:file <./lib/index.js>;
  rdfc:class "HttpPoller";
  rdfc:entrypoint <./>.

[ ] a sh:NodeShape;
  sh:targetClass rdfc:HttpPoller;
  sh:property [
    sh:datatype xsd:string; sh:path rdfc:url; sh:name "url";
    sh:minCount 1; sh:maxCount 1;
  ], [
    sh:datatype xsd:integer; sh:path rdfc:interval; sh:name "interval";
    sh:maxCount 1;
  ], [
    sh:class rdfc:Writer; sh:path rdfc:writer; sh:name "writer";
    sh:minCount 1; sh:maxCount 1;
  ].
"""

LOGGER_TTL = """\
@prefix rdfc: <https://w3id.org/rdf-connect#>.
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#>.
@prefix sh: <http://www.w3.org/ns/shacl#>.
@prefix xsd: <http://www.w3.org/2001/XMLSchema#>.

rdfc:LogProcessorJs rdfc:jsImplementationOf rdfc:Processor;
  rdfs:label "log processor".

[ ] a sh:NodeShape;
  sh:targetClass rdfc:LogProcessorJs;
  sh:property [
    sh:class rdfc:Reader; sh:path rdfc:reader; sh:name "reader";
    sh:minCount 1; sh:maxCount 1;
  ], [
    sh:datatype xsd:string; sh:path rdfc:level; sh:name "level";
    sh:maxCount 1;
  ].
"""

PY_LOGGER_TTL = """\
@prefix rdfc: <https://w3id.org/rdf-connect#>.
@prefix sh: <http://www.w3.org/ns/shacl#>.

rdfc:LogProcessorPy rdfc:pyImplementationOf rdfc:Processor.

[ ] a sh:NodeShape;
  sh:targetClass rdfc:LogProcessorPy;
  sh:property [
    sh:class rdfc:Reader; sh:path rdfc:reader; sh:name "reader"; sh:maxCount 1;
  ].
"""


SPARQL_INGEST_TTL = """\
@prefix rdfc: <https://w3id.org/rdf-connect#>.
@prefix sh: <http://www.w3.org/ns/shacl#>.

rdfc:SPARQLIngest rdfc:jsImplementationOf rdfc:Processor.

[ ] a sh:NodeShape;
  sh:targetClass rdfc:SPARQLIngest;
  sh:property [
    sh:class rdfc:Reader; sh:path rdfc:memberStream; sh:name "memberStream";
    sh:minCount 1; sh:maxCount 1;
  ].
"""


def port(name, class_ref):
    return {
        "name": name,
        "inputFieldType": "baseTextField",
        "isRequired": True,
        "inValues": [],
        "classRef": class_ref,
    }


def make_component(
    identifier,
    name,
    runtime,
    raw_ttl,
    properties=None,
    deployment=None,
    **data,
):
    return {
        "_id": identifier,
        "identifiers": [identifier],
        "type": "githubProcessor",
        "metadata": [
            {"key": "name", "value": name},
            {"key": "description", "value": f"{name} description"},
            {"key": "runtime", "value": runtime},
            {"key": "url", "value": f"https://github.com/acme/{name}"},
            {"key": "owner", "value": "acme"},
            {"key": "defaultBranch", "value": "main"},
            {"key": "shaclFiles", "value": "processors.ttl"},
        ],
        "relations": [],
        "data": {
            "rawTtl": raw_ttl,
            "properties": properties or [],
            "componentKind": "component",
            "deployment": deployment,
            **data,
        },
    }


POLLER = make_component(
    "acme--http-poller",
    "http-poller",
    "ts",
    POLLER_TTL,
    properties=[port("writer", "rdfc:Writer")],
    deployment={
        "imports": ["./node_modules/@acme/http-poller/processors.ttl"],
        "packages": [
            {
                "name": "@acme/http-poller",
                "version": "^1.0.0",
                "supplier": "http://example.org/example/npm",
            }
        ],
    },
)

LOGGER = make_component(
    "acme--log-processor",
    "log-processor",
    "ts",
    LOGGER_TTL,
    properties=[port("reader", "rdfc:Reader")],
)

PY_LOGGER = make_component(
    "acme--py-log-processor",
    "py-log-processor",
    "py",
    PY_LOGGER_TTL,
    properties=[port("reader", "rdfc:Reader")],
)

COMPONENTS = {
    "acme--http-poller": POLLER,
    "acme--log-processor": LOGGER,
}


def make_pipeline(relations, name="My Pipeline"):
    return {
        "_id": "pipeline-1",
        "identifiers": ["pipeline-1"],
        "type": "pipeline",
        "metadata": [
            {"key": "name", "value": name},
            {"key": "description", "value": "A demo chain."},
        ],
        "relations": relations,
    }


CONNECTED = [
    {
        "key": "acme--http-poller",
        "type": "hasProcessor",
        "metadata": [
            {"key": "url", "value": "https://sensors.example.org/current"},
            {"key": "interval", "value": "10000"},
        ],
    },
    {
        "key": "acme--log-processor",
        "type": "hasProcessor",
        "metadata": [
            {"key": "level", "value": "debug"},
            {
                "key": "connections.reader.from",
                "value": "acme--http-poller|writer",
            },
        ],
    },
]

CHANNEL = URIRef(BASE + "http-poller-writer-to-log-processor-reader")
POLLER_STEP = URIRef(BASE + "step/http-poller")
LOGGER_STEP = URIRef(BASE + "step/log-processor")


def serialize(relations=None, components=None, **kwargs):
    ttl = PipelineDefinitionSerializer(base_uri=BASE, **kwargs).serialize(
        make_pipeline(relations if relations is not None else CONNECTED),
        components if components is not None else COMPONENTS,
    )
    graph = Graph()
    graph.parse(data=ttl, format="turtle")
    return graph


@pytest.fixture
def graph():
    return serialize()


class TestPipelineDefinition:
    def test_output_is_valid_turtle(self, graph):
        assert len(graph) > 0

    def test_pipeline_is_a_pipeline_definition(self, graph):
        assert (PIPELINE_URI, RDF.type, TCS.PipelineDefinition) in graph

    def test_pipeline_carries_its_elody_name_as_label(self, graph):
        assert graph.value(PIPELINE_URI, RDFS.label) == Literal("My Pipeline")

    def test_pipeline_carries_its_description_as_comment(self, graph):
        assert graph.value(PIPELINE_URI, RDFS.comment) == Literal("A demo chain.")

    def test_it_is_not_an_rdf_connect_pipeline(self, graph):
        # the runnable pipeline.ttl is what the generator *produces*; a
        # definition that also claimed to be one would be ambiguous input
        assert (PIPELINE_URI, RDF.type, RDFC.Pipeline) not in graph


class TestSteps:
    def test_every_processor_becomes_a_step(self, graph):
        steps = set(graph.subjects(RDF.type, TCS.InstancePipelineComponent))
        assert steps == {POLLER_STEP, LOGGER_STEP}

    def test_step_belongs_to_the_plan(self, graph):
        assert graph.value(POLLER_STEP, PPLAN.isStepOfPlan) == PIPELINE_URI

    def test_step_specializes_its_component(self, graph):
        assert graph.value(POLLER_STEP, PROV.specializationOf) == RDFC.HttpPoller
        assert graph.value(LOGGER_STEP, PROV.specializationOf) == RDFC.LogProcessorJs

    def test_step_is_labelled(self, graph):
        assert graph.value(POLLER_STEP, RDFS.label) == Literal("http-poller")

    def test_step_without_a_resolvable_component_is_skipped(self):
        graph = serialize(
            [{"key": "missing--component", "type": "hasProcessor", "metadata": []}]
        )
        assert list(graph.subjects(RDF.type, TCS.InstancePipelineComponent)) == []
        assert (PIPELINE_URI, RDF.type, TCS.PipelineDefinition) in graph

    def test_the_same_component_used_twice_yields_two_steps(self):
        relations = [
            {"key": "acme--http-poller", "type": "hasProcessor", "metadata": []},
            {"key": "acme--log-processor", "type": "hasProcessor", "metadata": []},
        ]
        graph = serialize(relations)
        assert len(set(graph.subjects(RDF.type, TCS.InstancePipelineComponent))) == 2


class TestStepConfig:
    def test_config_is_a_pipeline_config(self, graph):
        config = graph.value(POLLER_STEP, PPLAN.hasInputVar)
        assert (config, RDF.type, TCS.PipelineConfig) in graph

    def test_config_values_live_under_tcs_embedded(self, graph):
        config = graph.value(POLLER_STEP, PPLAN.hasInputVar)
        embedded = graph.value(config, TCS.embedded)
        assert graph.value(embedded, RDFC.url) == Literal(
            "https://sensors.example.org/current"
        )

    def test_typed_literals_keep_their_datatype(self, graph):
        embedded = graph.value(
            graph.value(POLLER_STEP, PPLAN.hasInputVar), TCS.embedded
        )
        assert graph.value(embedded, RDFC.interval) == Literal(
            "10000", datatype=XSD.integer
        )

    def test_channel_properties_reference_the_channel_iri(self, graph):
        embedded = graph.value(
            graph.value(POLLER_STEP, PPLAN.hasInputVar), TCS.embedded
        )
        assert graph.value(embedded, RDFC.writer) == CHANNEL

    def test_a_step_without_config_declares_no_input_var(self):
        relations = [
            {"key": "acme--log-processor", "type": "hasProcessor", "metadata": []}
        ]
        graph = serialize(relations)
        assert graph.value(LOGGER_STEP, PPLAN.hasInputVar) is None


class TestChannels:
    def test_producer_writes_to_the_connection_channel(self, graph):
        assert graph.value(POLLER_STEP, TCS.writesTo) == CHANNEL

    def test_consumer_reads_from_the_same_channel(self, graph):
        assert graph.value(LOGGER_STEP, TCS.readsFrom) == CHANNEL

    def test_channel_is_typed(self, graph):
        assert (CHANNEL, RDF.type, TCS.Channel) in graph

    def test_the_embedded_configs_agree_with_the_channel_annotations(self, graph):
        producer = graph.value(
            graph.value(POLLER_STEP, PPLAN.hasInputVar), TCS.embedded
        )
        consumer = graph.value(
            graph.value(LOGGER_STEP, PPLAN.hasInputVar), TCS.embedded
        )
        assert graph.value(producer, RDFC.writer) == graph.value(
            consumer, RDFC.reader
        )

    def test_an_unconnected_pipeline_declares_no_channels(self):
        graph = serialize(
            [{"key": "acme--http-poller", "type": "hasProcessor", "metadata": []}]
        )
        assert list(graph.subjects(RDF.type, TCS.Channel)) == []


class TestCatalogFragment:
    """A definition exported from Elody names components; it must also
    declare the ones the toolchain catalog does not already know."""

    def test_component_is_declared_as_a_pipeline_component(self, graph):
        assert (RDFC.HttpPoller, RDF.type, TCS.PipelineComponent) in graph
        assert (RDFC.HttpPoller, RDF.type, DCAT.Resource) in graph

    def test_component_carries_label_and_description(self, graph):
        assert graph.value(RDFC.HttpPoller, RDFS.label) == Literal("http-poller")
        assert graph.value(
            RDFC.HttpPoller, URIRef(str(RDFS) + "description")
        ) == Literal("http-poller description")

    def test_component_links_to_where_it_came_from(self, graph):
        assert graph.value(RDFC.HttpPoller, DCAT.landingPage) == Literal(
            "https://github.com/acme/http-poller"
        )

    def test_component_requires_the_runner_for_its_runtime(self, graph):
        assert RDFC.NodeRunner in set(graph.objects(RDFC.HttpPoller, DCT.requires))

    def test_python_runtime_requires_the_python_runner(self):
        relations = [
            {"key": "acme--py-log-processor", "type": "hasProcessor", "metadata": []}
        ]
        graph = serialize(relations, {"acme--py-log-processor": PY_LOGGER})
        assert RDFC.PyRunner in set(
            graph.objects(RDFC.LogProcessorPy, DCT.requires)
        )

    def test_the_runner_is_linked_to_the_orchestrator(self, graph):
        # RdfcConfigCompiler finds processors by walking runner -> orchestrator
        assert (RDFC.NodeRunner, RDF.type, RDFC.Runner) in graph
        assert (RDFC.NodeRunner, DCT.requires, RDFC.Orchestrator) in graph

    def test_declared_imports_stay_relative(self):
        # The generator parses with base `file:///workspace/pipeline/` (rdfine
        # GraphReader), which is where the RDF-Connect runner mounts the
        # pipeline. Resolving the import here would nail it to the wrong root,
        # so it has to leave Elody in its relative form -- which means
        # asserting on the text, since re-parsing resolves it again.
        ttl = PipelineDefinitionSerializer(base_uri=BASE).serialize(
            make_pipeline(CONNECTED), COMPONENTS
        )
        assert "<./node_modules/@acme/http-poller/processors.ttl>" in ttl

    def test_the_import_is_attached_to_the_component(self, graph):
        assert str(graph.value(RDFC.HttpPoller, OWL.imports)).endswith(
            "node_modules/@acme/http-poller/processors.ttl"
        )

    def test_package_dependency_is_emitted_with_supplier(self, graph):
        packages = [
            o
            for o in graph.objects(RDFC.HttpPoller, DCT.requires)
            if (o, RDF.type, SPDX.Package) in graph
        ]
        assert len(packages) == 1
        assert graph.value(packages[0], SPDX.name) == Literal("@acme/http-poller")
        assert graph.value(packages[0], SPDX.versionInfo) == Literal("^1.0.0")
        assert graph.value(packages[0], SPDX.suppliedBy) == EX.npm

    def test_a_component_without_declared_imports_falls_back_to_its_repository(
        self, graph
    ):
        # log-processor declares no coordinates, so the .ttl it was read from
        # is the only import target we can honestly name
        assert graph.value(RDFC.LogProcessorJs, OWL.imports) == URIRef(
            "https://raw.githubusercontent.com/acme/log-processor/main/processors.ttl"
        )

    def test_config_shape_is_attached_in_the_config_role(self, graph):
        roles = {
            graph.value(relation, DCAT.hadRole)
            for relation in graph.objects(RDFC.HttpPoller, DCAT.qualifiedRelation)
        }
        assert TCS.configShape in roles

    def test_the_catalog_fragment_can_be_left_out(self):
        graph = serialize(include_catalog=False)
        assert (RDFC.HttpPoller, RDF.type, TCS.PipelineComponent) not in graph
        # the steps still reference it -- the toolchain catalog supplies it
        assert graph.value(POLLER_STEP, PROV.specializationOf) == RDFC.HttpPoller


class TestContractShapes:
    """B1's input/output shapes ride along, so the toolchain's shape-matching
    test suite sees the same contracts Elody validated against."""

    def _graph(self):
        from apps.dishacled.storage.local_component_source import (
            LocalComponentSource,
        )

        components = {d["_id"]: d for d in LocalComponentSource().list_documents()}
        relations = [
            {"key": "local--http-poller-cm", "type": "hasProcessor", "metadata": []},
            {
                "key": "local--threshold-monitor-cm",
                "type": "hasProcessor",
                "metadata": [
                    {
                        "key": "connections.input.from",
                        "value": "local--http-poller-cm|output",
                    }
                ],
            },
        ]
        return serialize(relations, components)

    def test_input_and_output_roles_are_declared(self):
        graph = self._graph()
        monitor = URIRef("https://dishacled.github.io/demo#ThresholdMonitorCm")
        roles = {
            graph.value(relation, DCAT.hadRole)
            for relation in graph.objects(monitor, DCAT.qualifiedRelation)
        }
        assert TCS.inputShape in roles
        assert TCS.outputShape in roles

    def test_the_shape_bodies_travel_with_the_definition(self):
        graph = self._graph()
        cm_shape = URIRef("https://dishacled.github.io/demo#MeasurementsInCmShape")
        assert (
            cm_shape,
            RDF.type,
            URIRef("http://www.w3.org/ns/shacl#NodeShape"),
        ) in graph

    def test_the_demo_chain_is_wired(self):
        graph = self._graph()
        poller = URIRef(BASE + "step/http-poller-cm")
        monitor = URIRef(BASE + "step/threshold-monitor-cm")
        channel = graph.value(poller, TCS.writesTo)
        assert channel is not None
        assert graph.value(monitor, TCS.readsFrom) == channel


class TestDatasetSource:
    """A dataset produces data but is not deployable, so it is a source of the
    plan rather than a step of it."""

    def _graph(self):
        from apps.dishacled.storage.local_component_source import (
            LocalComponentSource,
        )

        components = {d["_id"]: d for d in LocalComponentSource().list_documents()}
        components["acme--log-processor"] = LOGGER
        relations = [
            {"key": "local--sensor-feed-cm", "type": "hasProcessor", "metadata": []},
            {
                "key": "acme--log-processor",
                "type": "hasProcessor",
                "metadata": [
                    {
                        "key": "connections.reader.from",
                        "value": "local--sensor-feed-cm|output",
                    }
                ],
            },
        ]
        return serialize(relations, components)

    def test_dataset_is_not_a_step(self):
        graph = self._graph()
        steps = set(graph.subjects(RDF.type, TCS.InstancePipelineComponent))
        assert steps == {LOGGER_STEP}

    def test_dataset_is_declared_as_a_source_of_the_plan(self):
        graph = self._graph()
        dataset = URIRef("https://dishacled.github.io/demo#SensorFeedCm")
        assert (PIPELINE_URI, DCT.source, dataset) in graph
        assert (dataset, RDF.type, DCAT.Dataset) in graph

    def test_dataset_still_names_the_channel_it_feeds(self):
        graph = self._graph()
        dataset = URIRef("https://dishacled.github.io/demo#SensorFeedCm")
        channel = graph.value(dataset, TCS.writesTo)
        assert channel is not None
        assert graph.value(LOGGER_STEP, TCS.readsFrom) == channel

    def test_the_dataset_is_not_given_a_runner(self):
        graph = self._graph()
        dataset = URIRef("https://dishacled.github.io/demo#SensorFeedCm")
        assert RDFC.NodeRunner not in set(graph.objects(dataset, DCT.requires))


class TestEmptyPipeline:
    def test_a_pipeline_without_processors_still_serializes(self):
        graph = serialize([])
        assert (PIPELINE_URI, RDF.type, TCS.PipelineDefinition) in graph


class TestProvenanceHeader:
    def test_the_output_says_where_it_came_from(self):
        ttl = PipelineDefinitionSerializer(base_uri=BASE).serialize(
            make_pipeline(CONNECTED), COMPONENTS
        )
        header = ttl[: ttl.index("@prefix")] if "@prefix" in ttl else ttl
        assert "toolchain" in header.lower()
        assert "catalog" in header.lower()


TOOLCHAIN_PATH = os.getenv("TOOLCHAIN_SPECIFICATION_PATH", "")


def _toolchain_data():
    return Path(TOOLCHAIN_PATH) / "pipeline generator" / "data"


def _toolchain_available():
    if not TOOLCHAIN_PATH or not _toolchain_data().exists():
        return False
    src = str(Path(TOOLCHAIN_PATH) / "pipeline generator" / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    try:
        import compilers  # noqa: F401
        import rdfine  # noqa: F401
    except Exception:
        return False
    return True


@pytest.mark.skipif(
    not _toolchain_available(),
    reason=(
        "set TOOLCHAIN_SPECIFICATION_PATH to a toolchain-specification checkout "
        "with the pipeline generator's dependencies installed"
    ),
)
class TestToolchainPipelineGenerator:
    """Feed the export to the real pipeline generator.

    This is the objective form of "the generator accepts it": the same
    application-profile shapes, inference rules and compilers the toolchain
    runs, over the definition Elody exports. Skipped unless a checkout is
    available, so the suite itself has no external dependency.
    """

    CATALOG_FILES = (
        "catalog-core.ttl",
        "catalog-ldio.ttl",
        "catalog-rdfc.ttl",
        "catalog-sw.ttl",
        "catalog-application-profile-shapes.ttl",
    )
    # The RDF-Connect runner mounts the pipeline here, and the toolchain's
    # GraphReader parses with this base -- which is what makes a relative
    # `owl:imports` resolve to the right place.
    RUNTIME_BASE = "file:///workspace/pipeline/"

    def _catalog(self):
        graph = Graph()
        for name in self.CATALOG_FILES:
            # the generator's own demo notebook parses every catalog file with
            # this base; loading them any other way resolves their relative
            # owl:imports somewhere else and the comparison stops being real
            graph.parse(
                _toolchain_data() / name, format="turtle", publicID=self.RUNTIME_BASE
            )
        return graph

    def _reader(self, ttl=None):
        from rdfine import GraphReader

        graph = self._catalog()
        if ttl:
            graph.parse(data=ttl, format="turtle", publicID=self.RUNTIME_BASE)
        return GraphReader(graph).infer(_toolchain_data() / "inference_rules.yaml")

    def _violations(self, ttl=None):
        import re

        report = self._reader(ttl).validate(advanced=True, inference="rdfs")
        if report.ask("?r sh:conforms true"):
            return set()
        return set(re.findall(r"sh:value (\S+)", report.serialize("ttl")))

    def _export(self, relations, components):
        return PipelineDefinitionSerializer(base_uri=BASE).serialize(
            make_pipeline(relations), components
        )

    def _demo_chain(self):
        from apps.dishacled.storage.local_component_source import (
            LocalComponentSource,
        )

        components = {d["_id"]: d for d in LocalComponentSource().list_documents()}
        relations = [
            {
                "key": "local--http-poller-cm",
                "type": "hasProcessor",
                "metadata": [{"key": "url", "value": "https://sensors.example/a"}],
            },
            {
                "key": "local--threshold-monitor-cm",
                "type": "hasProcessor",
                "metadata": [
                    {"key": "threshold", "value": "300"},
                    {
                        "key": "connections.input.from",
                        "value": "local--http-poller-cm|output",
                    },
                ],
            },
        ]
        return relations, components

    def test_the_export_adds_no_application_profile_violations(self):
        # The shipped catalog does not fully conform on its own (two entries
        # in :DishacledCatalog are never defined), so the bar is that our
        # definition introduces nothing beyond that baseline.
        baseline = self._violations()
        after = self._violations(self._export(CONNECTED, COMPONENTS))
        assert after - baseline == set()

    def test_the_demo_chain_adds_no_application_profile_violations(self):
        relations, components = self._demo_chain()
        baseline = self._violations()
        after = self._violations(self._export(relations, components))
        assert after - baseline == set()

    def test_a_definition_without_a_fragment_compiles_against_the_catalog_graph(self):
        """The fragment is removable once the store describes the components.

        `?catalog=false` is only a real option if what the catalog graph holds
        is enough on its own, so the graphs Elody publishes
        (`pipeline/catalog.py`) are fed in exactly where the fragment used to
        be, and the same bar applies: no violation the shipped catalog does not
        already have.
        """
        from apps.dishacled.pipeline.catalog import component_turtle

        relations, components = self._demo_chain()
        named = sorted({relation["key"] for relation in relations})
        definition = PipelineDefinitionSerializer(
            base_uri=BASE, include_catalog=False
        ).serialize(make_pipeline(relations), components)
        catalog = "\n".join(component_turtle(components[key]) for key in named)

        baseline = self._violations()
        after = self._violations(f"{catalog}\n{definition}")
        assert after - baseline == set()

    def _compile(self, relations, components):
        from compilers import PipelineGenerator, ProjectBuilder

        reader = self._reader(self._export(relations, components))
        pipeline_id = "pipeline:" + BASE.rstrip("/").rsplit("/", 1)[-1]
        build = PipelineGenerator(pipeline_id, reader.graph).compile()
        builder = ProjectBuilder(build)
        return {
            f"{row.filepath}/{row.filename}": row.content
            for row in builder.files.itertuples()
        }

    def test_the_generator_compiles_a_runnable_project(self):
        files = self._compile(*self._demo_chain())
        assert "rdfc/pipeline.ttl" in files
        assert "./docker-compose.yml" in files

    def test_the_compiled_pipeline_carries_both_steps(self):
        files = self._compile(*self._demo_chain())
        pipeline_ttl = files["rdfc/pipeline.ttl"]
        assert f"{BASE}step/http-poller-cm" in pipeline_ttl
        assert f"{BASE}step/threshold-monitor-cm" in pipeline_ttl

    def test_the_compiled_pipeline_wires_the_two_steps_to_one_channel(self):
        graph = Graph()
        graph.parse(
            data=self._compile(*self._demo_chain())["rdfc/pipeline.ttl"],
            format="turtle",
            publicID=self.RUNTIME_BASE,
        )
        producer = URIRef(BASE + "step/http-poller-cm")
        consumer = URIRef(BASE + "step/threshold-monitor-cm")
        channel = graph.value(producer, RDFC.output)
        assert channel is not None
        assert graph.value(consumer, RDFC.input) == channel
        assert (channel, RDF.type, RDFC.Reader) in graph
        assert (channel, RDF.type, RDFC.Writer) in graph

    def test_the_declared_package_reaches_the_generated_manifest(self):
        files = self._compile(*self._demo_chain())
        assert "@dishacled/demo-processors" in files["rdfc/package.json"]

    def test_the_relative_import_resolves_to_the_runner_mount_point(self):
        pipeline_ttl = self._compile(*self._demo_chain())["rdfc/pipeline.ttl"]
        assert (
            "file:///workspace/pipeline/node_modules/"
            "@dishacled/demo-processors/processors.ttl" in pipeline_ttl
        )

    def test_redeclaring_a_known_component_does_not_duplicate_its_import(self):
        # Our catalog fragment restates components the toolchain catalog may
        # already know. Because both sides express owl:imports relative to the
        # runner's mount point, the two descriptions merge into one triple
        # rather than importing the same file twice.
        import re

        component = make_component(
            "rdf-connect--sparql-ingest-processor-ts",
            "sparql-ingest-processor-ts",
            "ts",
            SPARQL_INGEST_TTL,
            properties=[port("memberStream", "rdfc:Reader")],
            deployment={
                "imports": [
                    "./node_modules/@rdfc/sparql-ingest-processor-ts/processors.ttl"
                ],
                "packages": [
                    {
                        "name": "@rdfc/sparql-ingest-processor-ts",
                        "version": "^2.1.7",
                        "supplier": "http://example.org/example/npm",
                    }
                ],
            },
        )
        relations = [
            {
                "key": "rdf-connect--sparql-ingest-processor-ts",
                "type": "hasProcessor",
                "metadata": [{"key": "memberStream", "value": "violations"}],
            }
        ]
        files = self._compile(
            relations, {"rdf-connect--sparql-ingest-processor-ts": component}
        )
        imports = re.findall(
            r"<(file:[^>]*sparql-ingest[^>]*)>", files["rdfc/pipeline.ttl"]
        )
        assert imports == [
            "file:///workspace/pipeline/node_modules/"
            "@rdfc/sparql-ingest-processor-ts/processors.ttl"
        ]
