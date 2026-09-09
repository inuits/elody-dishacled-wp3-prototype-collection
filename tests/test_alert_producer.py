"""The component that actually raises the alerts.

`tm:ThresholdMonitorJs` is a real, published RDF-Connect processor
(`@rdfc/threshold-monitor-processor-ts`), and the catalog carried it as an
*overlay*: shapes only, on the assumption that GitHub discovery finds the
repository and supplies the rest. It does not -- the repository carries no
`rdfc-processor` topic -- so the one component in the demonstrator that turns a
measurement into an `oslc:Error` could not be put in a pipeline at all.

The catalog is where that gap is closed: the entry carries its own config
shape and its own deployment coordinates, the way `rdfc:RmlMapper` already
does for a jvm processor whose repository cannot supply them. Then a pipeline
composed in Elody exports as a runnable one.

See docs/alert-component.md.
"""

import pytest
from rdflib import Graph, Literal, Namespace, RDF, URIRef

from apps.dishacled.pipeline.connections import (
    connection_metadata,
    input_ports,
    output_ports,
    ports_for_component,
)
from apps.dishacled.serializers.pipeline_ttl_serializer import PipelineTtlSerializer
from apps.dishacled.shacl.contracts import ContractCatalog
from apps.dishacled.storage.local_component_source import LocalComponentSource


MONITOR_IRI = "https://w3id.org/rdf-connect/threshold-monitor#ThresholdMonitorJs"
MONITOR_ID = "local--threshold-monitor-js"
ERROR_SHAPE = "http://lblod.data.gift/shapes/ErrorShape"
PACKAGE = "@rdfc/threshold-monitor-processor-ts"

TM = Namespace("https://w3id.org/rdf-connect/threshold-monitor#")
RDFC = Namespace("https://w3id.org/rdf-connect#")
SPDX = Namespace("http://spdx.org/rdf/terms#")
OWL = Namespace("http://www.w3.org/2002/07/owl#")
XSD_IRI = URIRef("http://www.w3.org/2001/XMLSchema#iri")


@pytest.fixture(scope="module")
def contract():
    entry = ContractCatalog.default().get(MONITOR_IRI)
    assert entry is not None, "the threshold monitor is not in the catalog"
    return entry


@pytest.fixture(scope="module")
def components():
    return {d["_id"]: d for d in LocalComponentSource().list_documents()}


class TestTheCatalogEntry:
    def test_it_is_a_runnable_component(self, contract):
        assert contract.kind == "component"
        assert contract.runnable is True

    def test_it_produces_the_alert_shape(self, contract):
        assert contract.output_shape is not None
        assert contract.output_shape.iri == ERROR_SHAPE

    def test_it_declares_no_input_shape(self, contract):
        """It reads SDS members; their shape is the stream's, not its own."""
        assert contract.input_shape is None

    def test_it_carries_the_published_package(self, contract):
        packages = {p.name: p for p in contract.deployment.packages}
        assert PACKAGE in packages
        assert packages[PACKAGE].version
        assert packages[PACKAGE].supplier.endswith("npm")

    def test_it_carries_the_import_the_runner_follows(self, contract):
        assert any(
            i.endswith("threshold-monitor-processor-ts/processor.ttl")
            for i in contract.deployment.imports
        )

    def test_the_catalog_is_now_its_home(self, contract):
        """No `dcat:landingPage`: discovery does not return this repository, so
        an overlay waiting for it is an entry nothing ever completes."""
        assert contract.landing_page is None


class TestItIsPickable:
    def test_it_is_listed_among_the_local_components(self, components):
        assert MONITOR_ID in components

    def test_a_search_for_threshold_finds_it(self):
        found = LocalComponentSource().list_documents("threshold")
        assert MONITOR_ID in {d["_id"] for d in found}

    def test_it_has_a_reader_in_and_a_writer_out(self, components):
        ports = ports_for_component(components[MONITOR_ID])
        assert [p.name for p in input_ports(ports)] == ["reader"]
        assert [p.name for p in output_ports(ports)] == ["writer"]

    def test_its_output_port_carries_the_alert_shape(self, components):
        port = output_ports(ports_for_component(components[MONITOR_ID]))[0]
        assert port.shape_iri == ERROR_SHAPE

    def test_the_bounds_and_the_path_are_configurable(self, components):
        fields = components[MONITOR_ID]["data"]["formFields"]
        assert {"path", "max", "min", "creator"} <= set(fields)

    def test_it_feeds_the_alert_visualisation(self, components):
        """The point of the entry: producer and dashboard agree on the shape."""
        dashboard = components["local--alert-visualisation"]
        producer = output_ports(ports_for_component(components[MONITOR_ID]))[0]
        consumer = input_ports(ports_for_component(dashboard))[0]
        assert producer.shape_iri == consumer.shape_iri


class TestTheRunnableExport:
    """What `npx rdfc` gets handed."""

    @pytest.fixture
    def graph(self, components):
        pipeline = {
            "_id": "p",
            "identifiers": ["p"],
            "type": "pipeline",
            "metadata": [{"key": "name", "value": "Alerts"}],
            "relations": [
                {
                    "key": MONITOR_ID,
                    "type": "hasProcessor",
                    "metadata": [
                        {"key": "path", "value": "http://example.org/ns#level"},
                        {"key": "max", "value": "40.0"},
                        {
                            "key": "creator",
                            "value": "https://dishacled.github.io/demo/agents#threshold-monitor",
                        },
                        {"key": "reader", "value": "measurements"},
                        {"key": "writer", "value": "alerts"},
                    ],
                }
            ],
        }
        ttl = PipelineTtlSerializer(base_uri="https://elody.local/p/").serialize(
            pipeline, components
        )
        graph = Graph()
        graph.parse(data=ttl, format="turtle", publicID="https://elody.local/p/")
        return graph

    def stage(self, graph):
        stages = list(graph.subjects(RDF.type, URIRef(MONITOR_IRI)))
        assert len(stages) == 1
        return stages[0]

    def test_the_stage_is_instantiated_by_the_node_runner(self, graph):
        stage = self.stage(graph)
        instantiated = {
            processor
            for group in graph.objects(None, RDFC.consistsOf)
            for processor in graph.objects(group, RDFC.processor)
        }
        assert stage in instantiated

    def test_the_processor_definition_is_imported(self, graph):
        imports = {str(o) for o in graph.objects(None, OWL.imports)}
        assert any(i.endswith("threshold-monitor-processor-ts/processor.ttl") for i in imports)

    def test_a_nested_config_node_declares_its_class(self, components):
        """`sh:class rdfc:IngestConfig` on the value, so it has to say so.

        The toolchain's profile reports an untyped one as "Value does not have
        class rdfc:IngestConfig", and every processor with nested config was
        emitted that way -- sdsify's metadataConfig, http-fetch's options.
        """
        pipeline = {
            "_id": "p",
            "identifiers": ["p"],
            "type": "pipeline",
            "metadata": [{"key": "name", "value": "Ingest"}],
            "relations": [
                {
                    "key": "rdf-connect--sparql-ingest-processor-ts--SPARQLIngest",
                    "type": "hasProcessor",
                    "metadata": [
                        {"key": "config.targetNamedGraph", "value": "urn:g"},
                        {"key": "memberStream", "value": "alerts"},
                    ],
                }
            ],
        }
        ingest = {
            "_id": "rdf-connect--sparql-ingest-processor-ts--SPARQLIngest",
            "type": "githubProcessor",
            "metadata": [{"key": "name", "value": "SPARQLIngest"}, {"key": "runtime", "value": "ts"}],
            "data": {
                "componentIri": "https://w3id.org/rdf-connect#SPARQLIngest",
                "componentKind": "component",
                "rawTtl": """
@prefix rdfc: <https://w3id.org/rdf-connect#>.
@prefix sh: <http://www.w3.org/ns/shacl#>.
@prefix xsd: <http://www.w3.org/2001/XMLSchema#>.
rdfc:SPARQLIngest rdfc:jsImplementationOf rdfc:Processor.
[] a sh:NodeShape ; sh:targetClass rdfc:SPARQLIngest ;
  sh:property [ sh:path rdfc:memberStream ; sh:name "memberStream" ; sh:class rdfc:Reader ],
              [ sh:path rdfc:ingestConfig ; sh:name "config" ; sh:class rdfc:IngestConfig ] .
[] a sh:NodeShape ; sh:targetClass rdfc:IngestConfig ;
  sh:property [ sh:path rdfc:targetNamedGraph ; sh:name "targetNamedGraph" ; sh:datatype xsd:string ] .
""",
                "properties": [],
            },
        }
        ttl = PipelineTtlSerializer(base_uri="https://elody.local/p/").serialize(
            pipeline, {ingest["_id"]: ingest}
        )
        graph = Graph()
        graph.parse(data=ttl, format="turtle", publicID="https://elody.local/p/")
        configs = list(graph.objects(None, RDFC.ingestConfig))
        assert configs, "no nested config emitted"
        assert (configs[0], RDF.type, RDFC.IngestConfig) in graph

    def test_the_bound_is_a_typed_literal(self, graph):
        value = graph.value(self.stage(graph), TM.max)
        assert value is not None
        assert float(value) == 40.0

    def test_an_iri_valued_parameter_is_emitted_as_an_iri(self, graph):
        """`sh:datatype xsd:iri` means an IRI node, not a typed literal.

        It is not a real datatype: the toolchain's harvested catalog rewrites
        it as `sh:nodeKind sh:IRI ; tcs:upstreamDatatype xsd:iri`, and the
        generator's validation report asks for node kind IRI accordingly.
        Both spellings happen to *run* -- the orchestrator maps the datatype to
        JSON-LD `@id`, and the monitor starts and alerts arrive either way --
        so the profile is what settles it.
        """
        stage = self.stage(graph)
        for predicate in (TM.creator, TM.path):
            value = graph.value(stage, predicate)
            assert value is not None, predicate
            assert isinstance(value, URIRef), f"{predicate} is {value!r}"
