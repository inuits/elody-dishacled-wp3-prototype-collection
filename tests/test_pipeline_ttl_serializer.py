"""Tests for exporting an Elody pipeline entity graph as a runnable RDF-Connect pipeline.ttl.

Target format (js-runner style, https://w3id.org/rdf-connect#):

    <pipeline> a rdfc:Pipeline;
        rdfc:consistsOf [
            rdfc:instantiates rdfc:NodeRunner;
            rdfc:processor <stage1>, <stage2>;
        ].

    <stage1> a rdfc:LdesClient;
        rdfc:url "https://...";
        rdfc:writer <data-channel>.

    <data-channel> a rdfc:Reader, rdfc:Writer.
"""

import pytest
from rdflib import Graph, Literal, Namespace, RDF, URIRef
from rdflib.namespace import XSD

from apps.dishacled.serializers.pipeline_ttl_serializer import (
    PipelineTtlSerializer,
)


RDFC = Namespace("https://w3id.org/rdf-connect#")
BASE = "https://elody.local/pipelines/pipeline-1/"


LDES_CLIENT_TTL = """\
@prefix rdfc: <https://w3id.org/rdf-connect#>.
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#>.
@prefix sh: <http://www.w3.org/ns/shacl#>.
@prefix xsd: <http://www.w3.org/2001/XMLSchema#>.

rdfc:LdesClient rdfc:jsImplementationOf rdfc:Processor;
  rdfs:label "ldes client";
  rdfc:file <./lib/index.js>;
  rdfc:class "LdesClient";
  rdfc:entrypoint <./>.

[ ] a sh:NodeShape;
  sh:targetClass rdfc:LdesClient;
  sh:property [
    sh:datatype xsd:string;
    sh:path rdfc:url;
    sh:name "url";
    sh:minCount 1;
    sh:maxCount 1;
  ], [
    sh:datatype xsd:integer;
    sh:path rdfc:pollInterval;
    sh:name "pollInterval";
    sh:maxCount 1;
  ], [
    sh:datatype xsd:boolean;
    sh:path rdfc:materialize;
    sh:name "materialize";
    sh:maxCount 1;
  ], [
    sh:class rdfc:Writer;
    sh:path rdfc:writer;
    sh:name "writer";
    sh:minCount 1;
    sh:maxCount 1;
  ].
"""

LOG_PROCESSOR_TTL = """\
@prefix rdfc: <https://w3id.org/rdf-connect#>.
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#>.
@prefix sh: <http://www.w3.org/ns/shacl#>.
@prefix xsd: <http://www.w3.org/2001/XMLSchema#>.

rdfc:LogProcessorJs rdfc:jsImplementationOf rdfc:Processor;
  rdfs:label "log processor";
  rdfc:file <./lib/index.js>;
  rdfc:class "LogProcessor";
  rdfc:entrypoint <./>.

[ ] a sh:NodeShape;
  sh:targetClass rdfc:LogProcessorJs;
  sh:property [
    sh:class rdfc:Reader;
    sh:path rdfc:reader;
    sh:name "reader";
    sh:minCount 1;
    sh:maxCount 1;
  ], [
    sh:datatype xsd:string;
    sh:path rdfc:level;
    sh:name "level";
    sh:maxCount 1;
  ].
"""

PY_PROCESSOR_TTL = """\
@prefix rdfc: <https://w3id.org/rdf-connect#>.
@prefix sh: <http://www.w3.org/ns/shacl#>.
@prefix xsd: <http://www.w3.org/2001/XMLSchema#>.

rdfc:PyLogProcessor rdfc:pyImplementationOf rdfc:Processor.

[ ] a sh:NodeShape;
  sh:targetClass rdfc:PyLogProcessor;
  sh:property [
    sh:class rdfc:Reader;
    sh:path rdfc:reader;
    sh:name "reader";
    sh:maxCount 1;
  ].
"""


def make_processor(identifier, name, runtime, raw_ttl):
    return {
        "_id": identifier,
        "identifiers": [identifier],
        "type": "githubProcessor",
        "metadata": [
            {"key": "name", "value": name},
            {"key": "runtime", "value": runtime},
        ],
        "data": {"rawTtl": raw_ttl},
        "relations": [],
    }


LDES_PROCESSOR = make_processor(
    "rdfc--ldes-client", "ldes-client", "ts", LDES_CLIENT_TTL
)
LOG_PROCESSOR = make_processor(
    "rdfc--log-processor-ts", "log-processor-ts", "ts", LOG_PROCESSOR_TTL
)
PY_PROCESSOR = make_processor(
    "rdfc--py-log-processor", "py-log-processor", "py", PY_PROCESSOR_TTL
)


def make_pipeline(relations):
    return {
        "_id": "pipeline-1",
        "identifiers": ["pipeline-1"],
        "type": "pipeline",
        "metadata": [{"key": "name", "value": "My Pipeline"}],
        "relations": relations,
    }


PIPELINE = make_pipeline(
    [
        {
            "key": "rdfc--ldes-client",
            "type": "hasProcessor",
            "metadata": [
                {"key": "url", "value": "https://ldes.example.org/feed"},
                {"key": "pollInterval", "value": "5000"},
                {"key": "materialize", "value": "true"},
                {"key": "writer", "value": "data channel"},
            ],
        },
        {
            "key": "rdfc--log-processor-ts",
            "type": "hasProcessor",
            "metadata": [
                {"key": "reader", "value": "data channel"},
                {"key": "level", "value": "info"},
            ],
        },
    ]
)

PROCESSORS = {
    "rdfc--ldes-client": LDES_PROCESSOR,
    "rdfc--log-processor-ts": LOG_PROCESSOR,
}


@pytest.fixture
def graph():
    serializer = PipelineTtlSerializer(base_uri=BASE)
    ttl = serializer.serialize(PIPELINE, PROCESSORS)
    g = Graph()
    g.parse(data=ttl, format="turtle")
    return g


class TestPipelineDeclaration:
    def test_output_is_valid_turtle(self, graph):
        assert len(graph) > 0

    def test_pipeline_is_declared(self, graph):
        pipeline_uri = URIRef(BASE)
        assert (pipeline_uri, RDF.type, RDFC.Pipeline) in graph

    def test_pipeline_consists_of_node_runner(self, graph):
        pipeline_uri = URIRef(BASE)
        runner_nodes = list(graph.objects(pipeline_uri, RDFC.consistsOf))
        assert len(runner_nodes) == 1
        assert (runner_nodes[0], RDFC.instantiates, RDFC.NodeRunner) in graph

    def test_both_stages_attached_to_runner(self, graph):
        pipeline_uri = URIRef(BASE)
        runner_node = graph.value(pipeline_uri, RDFC.consistsOf)
        stages = set(graph.objects(runner_node, RDFC.processor))
        assert stages == {
            URIRef(BASE + "ldes-client"),
            URIRef(BASE + "log-processor-ts"),
        }


class TestStageConfiguration:
    def test_stage_is_typed_with_shacl_target_class(self, graph):
        stage = URIRef(BASE + "ldes-client")
        assert (stage, RDF.type, RDFC.LdesClient) in graph

    def test_string_value_uses_sh_path_predicate(self, graph):
        stage = URIRef(BASE + "ldes-client")
        assert graph.value(stage, RDFC.url) == Literal(
            "https://ldes.example.org/feed"
        )

    def test_integer_value_is_typed_literal(self, graph):
        stage = URIRef(BASE + "ldes-client")
        value = graph.value(stage, RDFC.pollInterval)
        assert value == Literal("5000", datatype=XSD.integer)

    def test_boolean_value_is_typed_literal(self, graph):
        stage = URIRef(BASE + "ldes-client")
        value = graph.value(stage, RDFC.materialize)
        assert value == Literal("true", datatype=XSD.boolean)

    def test_empty_values_are_omitted(self):
        pipeline = make_pipeline(
            [
                {
                    "key": "rdfc--ldes-client",
                    "type": "hasProcessor",
                    "metadata": [
                        {"key": "url", "value": ""},
                        {"key": "writer", "value": "data channel"},
                    ],
                }
            ]
        )
        serializer = PipelineTtlSerializer(base_uri=BASE)
        ttl = serializer.serialize(
            pipeline, {"rdfc--ldes-client": LDES_PROCESSOR}
        )
        g = Graph()
        g.parse(data=ttl, format="turtle")
        stage = URIRef(BASE + "ldes-client")
        assert graph_has_no_value(g, stage, RDFC.url)

    def test_unknown_metadata_keys_are_ignored(self):
        pipeline = make_pipeline(
            [
                {
                    "key": "rdfc--ldes-client",
                    "type": "hasProcessor",
                    "metadata": [
                        {"key": "notInShape", "value": "whatever"},
                    ],
                }
            ]
        )
        serializer = PipelineTtlSerializer(base_uri=BASE)
        ttl = serializer.serialize(
            pipeline, {"rdfc--ldes-client": LDES_PROCESSOR}
        )
        g = Graph()
        g.parse(data=ttl, format="turtle")
        stage = URIRef(BASE + "ldes-client")
        assert graph_has_no_value(g, stage, RDFC.notInShape)


class TestChannels:
    def test_channel_fields_point_to_channel_uri(self, graph):
        writer_value = graph.value(URIRef(BASE + "ldes-client"), RDFC.writer)
        reader_value = graph.value(
            URIRef(BASE + "log-processor-ts"), RDFC.reader
        )
        assert isinstance(writer_value, URIRef)
        assert writer_value == reader_value

    def test_channel_is_declared_as_reader_and_writer(self, graph):
        channel = graph.value(URIRef(BASE + "ldes-client"), RDFC.writer)
        assert (channel, RDF.type, RDFC.Reader) in graph
        assert (channel, RDF.type, RDFC.Writer) in graph

    def test_channel_name_is_slugified(self, graph):
        channel = graph.value(URIRef(BASE + "ldes-client"), RDFC.writer)
        assert channel == URIRef(BASE + "data-channel")


class TestRunnerGrouping:
    def test_mixed_runtimes_yield_separate_runner_groups(self):
        pipeline = make_pipeline(
            [
                {
                    "key": "rdfc--log-processor-ts",
                    "type": "hasProcessor",
                    "metadata": [{"key": "reader", "value": "ch"}],
                },
                {
                    "key": "rdfc--py-log-processor",
                    "type": "hasProcessor",
                    "metadata": [{"key": "reader", "value": "ch"}],
                },
            ]
        )
        processors = {
            "rdfc--log-processor-ts": LOG_PROCESSOR,
            "rdfc--py-log-processor": PY_PROCESSOR,
        }
        serializer = PipelineTtlSerializer(base_uri=BASE)
        ttl = serializer.serialize(pipeline, processors)
        g = Graph()
        g.parse(data=ttl, format="turtle")

        pipeline_uri = URIRef(BASE)
        runner_nodes = list(g.objects(pipeline_uri, RDFC.consistsOf))
        assert len(runner_nodes) == 2

        instantiated = {
            g.value(node, RDFC.instantiates) for node in runner_nodes
        }
        assert instantiated == {RDFC.NodeRunner, RDFC.PyRunner}

    def test_processor_without_matching_entity_is_skipped(self):
        pipeline = make_pipeline(
            [
                {
                    "key": "unknown--processor",
                    "type": "hasProcessor",
                    "metadata": [],
                }
            ]
        )
        serializer = PipelineTtlSerializer(base_uri=BASE)
        ttl = serializer.serialize(pipeline, {})
        g = Graph()
        g.parse(data=ttl, format="turtle")
        assert (URIRef(BASE), RDF.type, RDFC.Pipeline) in g
        assert list(g.objects(URIRef(BASE), RDFC.consistsOf)) == []


class TestEmptyPipeline:
    def test_pipeline_without_processors_still_serializes(self):
        pipeline = make_pipeline([])
        serializer = PipelineTtlSerializer(base_uri=BASE)
        ttl = serializer.serialize(pipeline, {})
        g = Graph()
        g.parse(data=ttl, format="turtle")
        assert (URIRef(BASE), RDF.type, RDFC.Pipeline) in g


def graph_has_no_value(g, subject, predicate):
    return g.value(subject, predicate) is None
