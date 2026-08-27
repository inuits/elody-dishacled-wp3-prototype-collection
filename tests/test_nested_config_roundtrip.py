"""Nested config has to survive the store, or it is lost on the next save.

`rdfc:SPARQLIngest` keeps its settings in `rdfc:ingestConfig`, `rdfc:RmlMapper`
its input in `rdfc:source` and its output in `rdfc:defaultTarget`. Those are
nested blocks, and a pipeline is stored as its definition -- so if the reverse
mapping cannot decode a nested block, the value comes back as a blank-node id,
the form shows nothing, and the next save (which re-serializes what it read)
drops the block entirely. Set, gone, with nothing logged.

The cause was one link short in the exported catalog fragment: a property says
`sh:class rdfc:IngestConfig`, and the shape *for* `rdfc:IngestConfig` is an
anonymous `sh:NodeShape` with `sh:targetClass`. `extract_shape_graph` followed
`sh:class` only when the class was itself typed `sh:NodeShape`, which it never
is, so the nested shape stayed behind and the definition was not self-contained
after all.
"""

from rdflib import BNode, Graph, URIRef
from rdflib.namespace import RDF, SH

from apps.dishacled.serializers.pipeline_definition_serializer import (
    PipelineDefinitionSerializer,
)
from apps.dishacled.serializers.pipeline_serializer import PipelineSerializer
from apps.dishacled.shacl.contracts import extract_shape_graph

from tests.test_validation import make_pipeline


RDFC = "https://w3id.org/rdf-connect#"

# The shape of the real sparql-ingest processor file: the nested shape is
# anonymous and reaches its class through sh:targetClass.
NESTED_TTL = """\
@prefix rdfc: <https://w3id.org/rdf-connect#>.
@prefix sh:   <http://www.w3.org/ns/shacl#>.
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#>.

rdfc:Ingest rdfc:jsImplementationOf rdfc:Processor.

[] a sh:NodeShape;
  sh:targetClass rdfc:Ingest;
  sh:property [
    sh:class rdfc:Reader; sh:path rdfc:memberStream; sh:name "memberStream";
    sh:minCount 1; sh:maxCount 1;
  ], [
    sh:class rdfc:IngestConfig; sh:path rdfc:ingestConfig; sh:name "config";
    sh:minCount 1; sh:maxCount 1;
  ].

[] a sh:NodeShape;
  sh:targetClass rdfc:IngestConfig;
  sh:property [
    sh:datatype xsd:string; sh:path rdfc:graphStoreUrl; sh:name "graphStoreUrl";
    sh:maxCount 1;
  ], [
    sh:datatype xsd:boolean; sh:path rdfc:forVirtuoso; sh:name "forVirtuoso";
    sh:maxCount 1;
  ], [
    sh:class rdfc:Writer; sh:path rdfc:sparqlWriter; sh:name "writer";
    sh:maxCount 1;
  ].
"""

INGEST_ID = "acme--ingest--Ingest"
INGEST = {
    "_id": INGEST_ID,
    "type": "githubProcessor",
    "metadata": [
        {"key": "name", "value": "Ingest"},
        {"key": "runtime", "value": "ts"},
    ],
    "data": {
        "rawTtl": NESTED_TTL,
        "componentIri": f"{RDFC}Ingest",
        "deployment": {"imports": [], "packages": []},
    },
}

CONFIGURED = [
    {
        "key": INGEST_ID,
        "type": "hasProcessor",
        "metadata": [
            {"key": "memberStream", "value": "validated"},
            {"key": "config.graphStoreUrl", "value": "http://virtuoso:8890/sparql"},
            {"key": "config.forVirtuoso", "value": "true"},
            {"key": "config.writer", "value": "sparql"},
        ],
    }
]


def _definition(relations=None):
    return PipelineDefinitionSerializer(
        base_uri="http://elody.local/pipelines/pipeline-1/"
    ).serialize(make_pipeline(relations or CONFIGURED), {INGEST_ID: INGEST})


def _read_back(ttl):
    graph = Graph()
    graph.parse(data=ttl, format="turtle")
    return PipelineSerializer().from_sparql_to_elody({"graph": graph})


def _metadata(entity, key):
    relation = next(
        r for r in entity["relations"] if r["type"] == "hasProcessor"
    )
    return {item["key"]: item["value"] for item in relation["metadata"]}.get(key)


class TestTheFragmentCarriesTheNestedShape:
    def test_extract_follows_a_class_to_the_shape_that_targets_it(self):
        source = Graph()
        source.parse(data=NESTED_TTL, format="turtle")
        main = next(
            shape
            for shape in source.subjects(RDF.type, SH.NodeShape)
            if source.value(shape, SH.targetClass) == URIRef(f"{RDFC}Ingest")
        )

        extracted = extract_shape_graph(source, main)

        targets = {
            str(extracted.value(shape, SH.targetClass))
            for shape in extracted.subjects(RDF.type, SH.NodeShape)
        }
        assert f"{RDFC}IngestConfig" in targets

    def test_the_nested_shape_keeps_its_own_properties(self):
        source = Graph()
        source.parse(data=NESTED_TTL, format="turtle")
        main = next(
            shape
            for shape in source.subjects(RDF.type, SH.NodeShape)
            if source.value(shape, SH.targetClass) == URIRef(f"{RDFC}Ingest")
        )
        extracted = extract_shape_graph(source, main)
        names = {str(name) for name in extracted.objects(None, SH.name)}
        assert {"graphStoreUrl", "forVirtuoso", "writer"} <= names

    def test_the_definition_declares_it(self):
        graph = Graph()
        graph.parse(data=_definition(), format="turtle")
        targets = {
            str(graph.value(shape, SH.targetClass))
            for shape in graph.subjects(RDF.type, SH.NodeShape)
        }
        assert f"{RDFC}IngestConfig" in targets


class TestNestedValuesComeBack:
    def test_a_nested_literal_survives(self):
        entity = _read_back(_definition())
        assert _metadata(entity, "config.graphStoreUrl") == (
            "http://virtuoso:8890/sparql"
        )

    def test_a_nested_boolean_survives(self):
        # as a boolean, not the string: a checkbox field is stored as one, and
        # "true" would not render as a ticked box (`_value_of`). The point here
        # is that it comes back at all, and typed the way a flat one is.
        assert _metadata(_read_back(_definition()), "config.forVirtuoso") is True

    def test_a_nested_channel_comes_back_as_its_name(self):
        assert _metadata(_read_back(_definition()), "config.writer") == "sparql"

    def test_the_block_is_not_a_blank_node_id(self):
        # what it used to be: `config = nd85546...`, which the next save drops
        # because it is not a dict
        entity = _read_back(_definition())
        assert _metadata(entity, "config") is None

    def test_a_flat_value_still_survives(self):
        assert _metadata(_read_back(_definition()), "memberStream") == "validated"


class TestSavingWhatWasReadKeepsIt:
    """The load-bearing property: re-saving must not shorten the config.

    Every edit re-serializes the whole pipeline, so a value that does not
    survive one round trip is gone at the next unrelated change.
    """

    def test_a_second_generation_definition_is_the_same(self):
        first = _definition()
        entity = _read_back(first)
        second = PipelineDefinitionSerializer(
            base_uri="http://elody.local/pipelines/pipeline-1/"
        ).serialize(entity, {INGEST_ID: INGEST})

        for expected in (
            "rdfc:graphStoreUrl",
            "rdfc:forVirtuoso",
            "rdfc:ingestConfig",
        ):
            assert expected in second, f"{expected} lost on the second save"

    def test_the_values_are_still_right_after_two_round_trips(self):
        once = _read_back(_definition())
        twice = _read_back(
            PipelineDefinitionSerializer(
                base_uri="http://elody.local/pipelines/pipeline-1/"
            ).serialize(once, {INGEST_ID: INGEST})
        )
        assert _metadata(twice, "config.graphStoreUrl") == (
            "http://virtuoso:8890/sparql"
        )
        assert _metadata(twice, "config.writer") == "sparql"
