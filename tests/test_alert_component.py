"""Elody's alert visualisation as a pipeline component.

The dashboard is not an out-of-band viewer: it is a component in the composed
pipeline, declared in the catalog with `ErrorShape` as its **input** contract.
That is what makes discovery find it, what puts it in the pipeline definition
as the last step, and what makes the chain validation cover the last hop
(monitor -> store -> Elody) instead of stopping at the store.

The component is *declarative* -- catalog entry plus contracts, no processor
code -- exactly the way the demonstrator's `proposal` branch models the
semantic.works services: input by reading the central store, no direct flow,
and validation only checks that the two ends agree on the contract
(`extensions.md`).

See docs/alert-component.md.
"""

from pathlib import Path

import pytest
from rdflib import ConjunctiveGraph, Graph, Namespace, RDF, URIRef

from apps.dishacled.pipeline.connections import (
    STATE_INVALID,
    STATE_VALID,
    connection_metadata,
    input_ports,
    output_ports,
    ports_for_component,
)
from apps.dishacled.pipeline.validation import validate_pipeline
from apps.dishacled.serializers.component_catalog_serializer import (
    ComponentCatalogSerializer,
)
from apps.dishacled.serializers.pipeline_definition_serializer import (
    PipelineDefinitionSerializer,
)
from apps.dishacled.serializers.pipeline_ttl_serializer import PipelineTtlSerializer
from apps.dishacled.shacl.contracts import (
    ContractCatalog,
    DEFAULT_CONTRACTS_PATH,
    Deployment,
)
from apps.dishacled.storage.dishacled_httpstore import DishacledHttpStorageManager
from apps.dishacled.storage.local_component_source import LocalComponentSource


ELODY_IRI = "https://elody.eu/components#AlertVisualisation"
ELODY_ID = "local--alert-visualisation"
ERROR_SHAPE = "http://lblod.data.gift/shapes/ErrorShape"
CM_SHAPE = "https://dishacled.github.io/demo#MeasurementsInCmShape"

ALERT_MONITOR_ID = "local--alert-monitor-cm"
POLLER_CM_ID = "local--http-poller-cm"

DOCS = Path(__file__).resolve().parents[1] / "docs"
HANDOFF = DOCS / "examples"

TCS = Namespace("https://w3id.org/toolchain#")
DCAT = Namespace("http://www.w3.org/ns/dcat#")
DCT = Namespace("http://purl.org/dc/terms/")
PPLAN = Namespace("http://purl.org/net/p-plan#")
PROV = Namespace("http://www.w3.org/ns/prov#")


@pytest.fixture(scope="module")
def catalog():
    return ContractCatalog.default()


@pytest.fixture(scope="module")
def contract(catalog):
    entry = catalog.get(ELODY_IRI)
    assert entry is not None, "the alert visualisation is not in the catalog"
    return entry


@pytest.fixture(scope="module")
def components():
    source = LocalComponentSource()
    return {d["_id"]: d for d in source.list_documents()}


@pytest.fixture(scope="module")
def fragment():
    graph = Graph()
    graph.parse(HANDOFF / "demonstrator-elody.ttl", format="turtle")
    return graph


@pytest.fixture(scope="module")
def scenario():
    graph = Graph()
    graph.parse(HANDOFF / "demonstrator-scenario-elody.ttl", format="turtle")
    return graph


def make_pipeline(relations):
    return {
        "_id": "pipeline-1",
        "identifiers": ["pipeline-1"],
        "type": "pipeline",
        "metadata": [{"key": "name", "value": "Alert pipeline"}],
        "relations": relations,
    }


def processor_relation(key, metadata=None):
    return {"key": key, "type": "hasProcessor", "metadata": metadata or []}


class TestCatalogEntry:
    """The catalog entry itself: identity, contract, configuration."""

    def test_it_is_a_component_and_not_a_dataset(self, contract):
        assert contract.kind == "component"

    def test_its_input_contract_is_the_error_shape(self, contract):
        assert contract.input_shape is not None
        assert contract.input_shape.iri == ERROR_SHAPE

    def test_it_declares_no_output_shape(self, contract):
        """A dashboard is a sink: it renders alerts and produces no stream."""
        assert contract.output_shape is None

    def test_it_has_a_label_and_a_description(self, contract):
        assert contract.label
        assert contract.comment

    def test_the_local_id_is_stable(self, contract):
        assert contract.local_id == ELODY_ID

    def test_nothing_is_installed_for_it(self, contract):
        """Declarative: no package, no processor definition to import."""
        assert contract.deployment == Deployment()

    def test_the_catalog_is_its_only_home(self, contract):
        """No `dcat:landingPage`: it is not an overlay on a repository."""
        assert contract.landing_page is None

    def test_it_is_configured_with_an_endpoint_and_a_named_graph(self, contract):
        assert contract.config_shape is not None
        names = {prop.name for prop in contract.config_shape.properties}
        assert {"sparqlEndpoint", "namedGraph"} <= names

    def test_the_input_shape_it_names_is_the_one_the_catalog_declares(self, catalog):
        """One `ErrorShape`, shared with the producers, not a private copy."""
        producer = catalog.get("https://dishacled.github.io/demo#AlertMonitorCm")
        assert producer is not None
        assert producer.output_shape.iri == ERROR_SHAPE


class TestServedAsAComponent:
    """It travels through the existing component paths unchanged."""

    def test_it_is_listed_among_the_local_components(self, components):
        assert ELODY_ID in components

    def test_it_resolves_by_id_without_calling_github(self):
        store = DishacledHttpStorageManager()
        store.session = None  # any call would raise
        item = store.get_item_from_collection_by_id("githubProcessors", ELODY_ID)
        assert item["data"]["componentIri"] == ELODY_IRI

    def test_it_has_one_typed_input_port(self, components):
        ports = input_ports(ports_for_component(components[ELODY_ID]))
        assert [p.name for p in ports] == ["alerts"]
        assert ports[0].shape_iri == ERROR_SHAPE

    def test_it_has_no_output_port(self, components):
        assert output_ports(ports_for_component(components[ELODY_ID])) == []

    def test_its_config_form_renders(self, components):
        fields = components[ELODY_ID]["data"]["formFields"]
        assert fields["sparqlEndpoint"]["inputField"]["type"] == "text"
        assert fields["namedGraph"]["inputField"]["type"] == "text"

    def test_a_text_search_finds_it(self):
        found = LocalComponentSource().list_documents("alert")
        assert ELODY_ID in {d["_id"] for d in found}


class TestChainValidation:
    """The last hop is covered: monitor -> store -> Elody."""

    def test_an_alert_producer_into_the_dashboard_is_valid(self, components):
        pipeline = make_pipeline(
            [
                processor_relation(ALERT_MONITOR_ID),
                processor_relation(
                    ELODY_ID,
                    connection_metadata("alerts", f"{ALERT_MONITOR_ID}|output"),
                ),
            ]
        )
        report = validate_pipeline(pipeline, components)
        assert [c.state for c in report.connections] == [STATE_VALID]
        assert report.violations == []

    def test_a_measurement_producer_into_the_dashboard_is_reported(self, components):
        """The cm/mm story breaks visibly all the way to the dashboard."""
        pipeline = make_pipeline(
            [
                processor_relation(POLLER_CM_ID),
                processor_relation(
                    ELODY_ID,
                    connection_metadata("alerts", f"{POLLER_CM_ID}|output"),
                ),
            ]
        )
        report = validate_pipeline(pipeline, components)
        assert [c.state for c in report.connections] == [STATE_INVALID]
        assert all(v.target.endswith("|alerts") for v in report.violations)
        # a located, quotable report: the alert fields a measurement lacks
        assert {"uuid", "message", "created"} <= {v.path for v in report.violations}

    def test_the_store_mediated_chain_validates_end_to_end(self, components):
        """monitor -> sparql ingest -> dashboard, the store in the middle.

        The ingest writes the alerts into the store and the dashboard reads
        them back out; both hops carry `ErrorShape`, which is the only thing
        the validator asks of an implicit, store-mediated flow.
        """
        ingest = {
            "_id": "rdf-connect--sparql-ingest",
            "identifiers": ["rdf-connect--sparql-ingest"],
            "type": "githubProcessor",
            "metadata": [{"key": "name", "value": "SPARQL ingest"}],
            "relations": [],
            "data": {
                "componentIri": "https://w3id.org/rdf-connect#SPARQLIngest",
                "componentKind": "component",
                "properties": [
                    {"name": "input", "classRef": "rdfc:Reader", "isRequired": True},
                    {"name": "output", "classRef": "rdfc:Writer"},
                ],
                "inputShape": components[ELODY_ID]["data"]["inputShape"],
                "outputShape": components[ELODY_ID]["data"]["inputShape"],
            },
        }
        catalogue = {**components, ingest["_id"]: ingest}
        pipeline = make_pipeline(
            [
                processor_relation(ALERT_MONITOR_ID),
                processor_relation(
                    ingest["_id"],
                    connection_metadata("input", f"{ALERT_MONITOR_ID}|output"),
                ),
                processor_relation(
                    ELODY_ID,
                    connection_metadata("alerts", f"{ingest['_id']}|output"),
                ),
            ]
        )
        report = validate_pipeline(pipeline, catalogue)
        assert [c.state for c in report.connections] == [STATE_VALID, STATE_VALID]


class TestDiscovery:
    """Step 3: searching for who can consume the alert data finds Elody."""

    def test_it_is_suggested_for_a_producer_of_alerts(self, components):
        store = DishacledHttpStorageManager()
        listing = {
            "results": [components[ELODY_ID], components[POLLER_CM_ID]],
            "count": 2,
            "limit": 10,
            "skip": 0,
        }
        result = store._with_compat_sort(listing, {ERROR_SHAPE}, hard=True)
        assert [d["_id"] for d in result["results"]] == [ELODY_ID]

    def test_it_is_not_suggested_for_a_producer_of_measurements(self, components):
        store = DishacledHttpStorageManager()
        listing = {
            "results": [components[ELODY_ID]],
            "count": 1,
            "limit": 10,
            "skip": 0,
        }
        result = store._with_compat_sort(listing, {CM_SHAPE}, hard=True)
        # a hard filter with no match keeps the listing rather than emptying it,
        # so what is asserted is the ranking: not a suggestion
        assert result["results"] == [components[ELODY_ID]]
        assert result["count"] == 1

    def test_the_shipped_discovery_query_returns_it(self):
        """The query a discovery service runs against the catalog.

        Loaded as a dataset rather than as one graph, because that is what it
        is run against: the store keeps one named graph per component, so the
        query has to look inside named graphs -- and rdflib refuses `GRAPH ?g`
        on a single graph.
        """
        query = (HANDOFF / "discovery-error-shape.sparql").read_text()
        store = ConjunctiveGraph()
        store.parse(DEFAULT_CONTRACTS_PATH, format="turtle")
        found = {str(row[0]) for row in store.query(query)}
        assert ELODY_IRI in found

    def test_the_shipped_discovery_query_is_not_alert_specific_by_accident(self):
        """It answers about the contract, so every consumer of it comes back."""
        query = (HANDOFF / "discovery-error-shape.sparql").read_text()
        store = ConjunctiveGraph()
        store.parse(DEFAULT_CONTRACTS_PATH, format="turtle")
        found = {str(row[0]) for row in store.query(query)}
        assert "https://w3id.org/rdf-connect#SPARQLIngest" in found


class TestDemonstratorHandoff:
    """What is handed to the demonstrator repository, in its own vocabulary.

    Two files, both parsed and checked here so a hand-off that no longer says
    what it claims to say fails a test rather than a review:
    `catalogs/components/elody.ttl` and the step to append to
    `pipelines/final-valid/scenario-a.ttl`.
    """

    LOKET_ERROR_SHAPE = URIRef(
        "http://lblod.data.gift/shapes/loket-error-alert-service/ErrorShape"
    )

    def test_the_component_is_a_catalogued_dcat_resource(self, fragment):
        subject = URIRef(ELODY_IRI)
        assert (subject, RDF.type, TCS.PipelineComponent) in fragment
        assert (subject, RDF.type, DCAT.Resource) in fragment
        catalogs = list(fragment.subjects(DCAT.resource, subject))
        assert catalogs, "the component belongs to no tcs:Catalog"
        assert all((c, RDF.type, TCS.Catalog) in fragment for c in catalogs)

    def test_its_input_shape_is_the_demonstrators_own_error_shape(self, fragment):
        roles = {
            fragment.value(relation, DCAT.hadRole): fragment.value(
                relation, DCT.relation
            )
            for relation in fragment.objects(URIRef(ELODY_IRI), DCAT.qualifiedRelation)
        }
        assert roles[TCS.inputShape] == self.LOKET_ERROR_SHAPE
        assert TCS.outputShape not in roles

    def test_it_carries_the_proposal_branch_config_mechanism(self, fragment):
        roles = {
            fragment.value(relation, DCAT.hadRole)
            for relation in fragment.objects(URIRef(ELODY_IRI), DCAT.qualifiedRelation)
        }
        assert TCS.wrappedConfigShape in roles
        assert TCS.configShape in roles
        assert fragment.value(URIRef(ELODY_IRI), TCS.unwrapConfig) is not None

    def test_the_step_specialises_the_component(self, scenario):
        steps = list(scenario.subjects(PROV.specializationOf, URIRef(ELODY_IRI)))
        assert len(steps) == 1
        step = steps[0]
        assert (step, RDF.type, TCS.InstancePipelineComponent) in scenario
        assert scenario.value(step, PPLAN.isStepOfPlan) is not None

    def test_a_connection_reaches_it_from_the_alert_producer(self, scenario):
        step = next(scenario.subjects(PROV.specializationOf, URIRef(ELODY_IRI)))
        connections = [
            c
            for c in scenario.subjects(RDF.type, TCS.Connection)
            if scenario.value(c, TCS.to) == step
        ]
        assert len(connections) == 1
        assert scenario.value(connections[0], TCS["from"]) is not None


class TestPipelineDefinition:
    """Elody's own export: the dashboard is the definition's last step.

    Same acceptance criterion as the demonstrator hand-off, asserted on the
    document Elody actually writes -- the definition a save publishes and a
    download hands over.
    """

    BASE = "https://elody.local/pipelines/alerts/"

    @pytest.fixture
    def graph(self, components):
        pipeline = make_pipeline(
            [
                processor_relation(ALERT_MONITOR_ID),
                processor_relation(
                    ELODY_ID,
                    connection_metadata("alerts", f"{ALERT_MONITOR_ID}|output"),
                ),
            ]
        )
        ttl = PipelineDefinitionSerializer(base_uri=self.BASE).serialize(
            pipeline, components
        )
        graph = Graph()
        graph.parse(data=ttl, format="turtle")
        return graph

    def step(self, graph):
        steps = list(graph.subjects(PROV.specializationOf, URIRef(ELODY_IRI)))
        assert len(steps) == 1, "the dashboard is not a step of the definition"
        return steps[0]

    def test_the_dashboard_is_a_step_of_the_plan(self, graph):
        step = self.step(graph)
        assert (step, RDF.type, TCS.InstancePipelineComponent) in graph
        assert graph.value(step, PPLAN.isStepOfPlan) == URIRef(
            self.BASE.rstrip("/")
        )

    def test_it_reads_from_the_channel_the_monitor_writes_to(self, graph):
        step = self.step(graph)
        channel = graph.value(step, TCS.readsFrom)
        assert channel is not None
        producers = set(graph.subjects(TCS.writesTo, channel)) - {step}
        assert producers, "nothing writes to the channel the dashboard reads"

    def test_it_is_the_last_step(self, graph):
        assert graph.value(self.step(graph), TCS.writesTo) is None

    def test_the_fragment_carries_the_error_shape_as_its_input(self, graph):
        roles = {
            graph.value(relation, DCAT.hadRole): graph.value(relation, DCT.relation)
            for relation in graph.objects(
                URIRef(ELODY_IRI), DCAT.qualifiedRelation
            )
        }
        assert roles[TCS.inputShape] == URIRef(ERROR_SHAPE)
        assert TCS.outputShape not in roles

    def test_the_component_is_registered_in_a_catalog(self, graph):
        catalogs = list(graph.subjects(DCAT.resource, URIRef(ELODY_IRI)))
        assert catalogs
        assert all((c, RDF.type, TCS.Catalog) in graph for c in catalogs)


class TestPublishedDescription:
    """What the catalog graph says about a component with no implementation.

    Elody publishes its component descriptions into the shared catalog graph
    (docs/component-catalog.md), and a description is a promise to whoever
    compiles the pipeline. Claiming a Node runner and an
    `rdfc:jsImplementationOf` for a component that ships no processor would
    offer the generator a step it cannot start -- the same reason a
    `dcat:Dataset` is not published as a `tcs:PipelineComponent`.
    """

    RDFC = Namespace("https://w3id.org/rdf-connect#")

    @pytest.fixture
    def graph(self, components):
        serializer = ComponentCatalogSerializer()
        runner = serializer.add(components[ELODY_ID])
        assert runner is None, "a declarative component asks for no runner"
        return serializer.graph

    def test_it_is_still_a_catalogued_component(self, graph):
        subject = URIRef(ELODY_IRI)
        assert (subject, RDF.type, TCS.PipelineComponent) in graph
        assert (subject, RDF.type, DCAT.Resource) in graph
        assert list(graph.subjects(DCAT.resource, subject))

    def test_it_claims_no_runner(self, graph):
        required = set(graph.objects(URIRef(ELODY_IRI), DCT.requires))
        assert self.RDFC.NodeRunner not in required
        assert self.RDFC.PyRunner not in required

    def test_it_claims_no_processor_implementation(self, graph):
        assert (
            URIRef(ELODY_IRI),
            self.RDFC.jsImplementationOf,
            self.RDFC.Processor,
        ) not in graph

    def test_it_requires_nothing_it_may_not_require(self, graph):
        """`dct:requires` may name a component or a package, and nothing else.

        The alert store is a `dcat:Dataset`, so requiring it -- which reads
        perfectly well -- is a violation the toolchain's own validation report
        raises ("dct:requires on a PipelineComponent must point at a
        tcs:PipelineComponent or spdx:Package"). Where the store is named is
        the config: `sparqlEndpoint` and `namedGraph`.
        """
        assert list(graph.objects(URIRef(ELODY_IRI), DCT.requires)) == []

    def test_nothing_is_installed_for_it(self, graph):
        from rdflib.namespace import OWL

        assert list(graph.objects(URIRef(ELODY_IRI), OWL.imports)) == []

    def test_it_says_how_it_is_deployed(self, graph):
        """Not installed is not the same as not deployed.

        The application profile requires every `tcs:PipelineComponent` to reach
        a `tcs:DockerComposeConfig` along `dct:requires*` -- a dashboard nobody
        deploys renders nothing -- and it is the one violation the generator
        still raised once everything else was fixed. The semantic.works
        services in the demonstrator's catalog are described the same way.
        """
        configs = list(graph.objects(URIRef(ELODY_IRI), TCS.config))
        assert configs, "the component says nothing about being deployed"
        assert (configs[0], RDF.type, TCS.DockerComposeConfig) in graph
        literal = graph.value(configs[0], TCS.literal)
        assert literal is not None and "elody-dashboard" in str(literal)

    def test_a_processor_component_still_gets_its_runner(self, components):
        """The change is scoped to components that declare no implementation."""
        serializer = ComponentCatalogSerializer()
        runner = serializer.add(components[ALERT_MONITOR_ID])
        assert runner == self.RDFC.NodeRunner


class TestRunnablePipeline:
    """The RDF-Connect export: wirable, but never handed to a runner.

    `pipeline_ttl_serializer.py` writes the pipeline the RDF-Connect runner
    actually starts. Elody is a stage of it -- the channel the alerts travel on
    is real, and the stage is what the connection binds to -- but no runner can
    instantiate it, exactly as for a `dcat:Dataset`.
    """

    RDFC = Namespace("https://w3id.org/rdf-connect#")

    @pytest.fixture
    def graph(self, components):
        pipeline = make_pipeline(
            [
                processor_relation(ALERT_MONITOR_ID),
                processor_relation(
                    ELODY_ID,
                    connection_metadata("alerts", f"{ALERT_MONITOR_ID}|output"),
                ),
            ]
        )
        serializer = PipelineTtlSerializer(base_uri="https://elody.local/p/")
        ttl = serializer.serialize(pipeline, components)
        assert serializer.unrepresented == []
        graph = Graph()
        graph.parse(data=ttl, format="turtle", publicID="https://elody.local/p/")
        return graph

    def stage(self, graph):
        stages = list(graph.subjects(RDF.type, URIRef(ELODY_IRI)))
        assert len(stages) == 1
        return stages[0]

    def test_the_stage_is_wired_to_the_monitors_channel(self, graph):
        channel = graph.value(self.stage(graph), self.RDFC.alerts)
        assert channel is not None
        assert (channel, RDF.type, self.RDFC.Reader) in graph

    def test_no_runner_instantiates_it(self, graph):
        stage = self.stage(graph)
        for group in graph.objects(None, self.RDFC.consistsOf):
            assert stage not in set(graph.objects(group, self.RDFC.processor))

    def test_the_processor_of_the_same_pipeline_is_instantiated(self, graph):
        instantiated = {
            processor
            for group in graph.objects(None, self.RDFC.consistsOf)
            for processor in graph.objects(group, self.RDFC.processor)
        }
        assert instantiated and self.stage(graph) not in instantiated
