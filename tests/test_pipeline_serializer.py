"""Tests for reading a `tcs:PipelineDefinition` back as an Elody pipeline.

This is the direction that makes the triple store the source of truth rather
than a copy: if a definition cannot be read back, a pipeline that exists only
in the store cannot be listed, opened or edited.

The bar is a **round trip**. Every test below takes an entity the builder could
have saved, exports it, reads it back, and asserts on what came out — so a
change to either serializer that loses a field fails here rather than in the
UI. What deliberately does not survive is asserted too, in
`TestWhatDoesNotRoundTrip`: a definition is shared with the toolchain, so
Elody's own reading of the chain has no business in it.
"""

import pytest
from rdflib import Graph

from apps.dishacled.serializers.pipeline_definition_serializer import (
    PipelineDefinitionSerializer,
)
from apps.dishacled.serializers.pipeline_serializer import PipelineSerializer

from tests.test_validation import (
    CM,
    DEMO,
    MM,
    connected,
    make_component,
    prop,
)


BASE = "https://elody.local/pipelines/pipeline-1/"
PIPELINE_IRI = BASE.rstrip("/")

PROCESSOR_TTL = """\
@prefix rdfc: <https://w3id.org/rdf-connect#>.
@prefix sh:   <http://www.w3.org/ns/shacl#>.
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#>.
@prefix demo: <{demo}>.

demo:{cls} rdfc:jsImplementationOf rdfc:Processor.

[] a sh:NodeShape;
  sh:targetClass demo:{cls};
  sh:property [
    sh:class rdfc:Reader; sh:path rdfc:reader; sh:name "input"; sh:maxCount 1;
  ], [
    sh:class rdfc:Writer; sh:path rdfc:writer; sh:name "output"; sh:maxCount 1;
  ], [
    sh:datatype xsd:string; sh:path demo:url; sh:name "url"; sh:maxCount 1;
  ], [
    sh:datatype xsd:integer; sh:path demo:interval; sh:name "interval";
    sh:maxCount 1;
  ], [
    sh:datatype xsd:boolean; sh:path demo:fatal; sh:name "fatal";
    sh:maxCount 1;
  ].
"""

NESTED_TTL = """\
@prefix rdfc: <https://w3id.org/rdf-connect#>.
@prefix sh:   <http://www.w3.org/ns/shacl#>.
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#>.
@prefix demo: <{demo}>.

demo:Nested rdfc:jsImplementationOf rdfc:Processor.

demo:OptionsShape a sh:NodeShape;
  sh:targetClass demo:Options;
  sh:property [
    sh:datatype xsd:string; sh:path demo:method; sh:name "method";
    sh:maxCount 1;
  ].

[] a sh:NodeShape;
  sh:targetClass demo:Nested;
  sh:property [
    sh:class rdfc:Reader; sh:path rdfc:reader; sh:name "input"; sh:maxCount 1;
  ], [
    sh:node demo:OptionsShape; sh:path demo:options; sh:name "options";
    sh:maxCount 1;
  ].
"""


def component(identifier, class_name, properties, ttl=PROCESSOR_TTL, **shapes):
    document = make_component(identifier, class_name, properties, **shapes)
    document["data"]["rawTtl"] = ttl.format(demo=DEMO, cls=class_name)
    return document


POLLER = component(
    "acme--poller",
    "PollerCm",
    [
        prop("url"),
        prop("interval"),
        prop("output", "rdfc:Writer", required=True),
    ],
    output_shape=CM,
)
POLLER_MM = component(
    "acme--poller-mm",
    "PollerMm",
    [prop("url"), prop("output", "rdfc:Writer", required=True)],
    output_shape=MM,
)
SINK = component(
    "acme--sink",
    "SinkCm",
    [prop("input", "rdfc:Reader", required=True), prop("fatal")],
    input_shape=CM,
)
MONITOR = component(
    "acme--monitor",
    "MonitorCm",
    [
        prop("input", "rdfc:Reader", required=True),
        prop("output", "rdfc:Writer", required=True),
    ],
    input_shape=CM,
    output_shape=CM,
)
NESTED = component(
    "acme--nested",
    "Nested",
    [prop("input", "rdfc:Reader", required=True), prop("options")],
    ttl=NESTED_TTL,
    input_shape=CM,
)

DATASET = make_component(
    "acme--measurements",
    "Measurements",
    [],
    output_shape=CM,
    kind="dataset",
)

COMPONENTS = {
    c["_id"]: c for c in [POLLER, POLLER_MM, SINK, MONITOR, NESTED, DATASET]
}


def relation(key, metadata=None):
    return {"key": key, "type": "hasProcessor", "metadata": metadata or []}


def pipeline(relations, name="My Pipeline", description="A demo chain."):
    return {
        "_id": "pipeline-1",
        "identifiers": ["pipeline-1"],
        "type": "pipeline",
        "metadata": [
            {"key": "name", "value": name},
            {"key": "description", "value": description},
        ],
        "relations": relations,
    }


CHAIN = [
    relation("acme--poller", [{"key": "url", "value": "https://sensors.example/now"}]),
    connected("acme--sink", "input", "acme--poller|output"),
]


def export(entity, components=None, **kwargs):
    return PipelineDefinitionSerializer(base_uri=BASE, **kwargs).serialize(
        entity, components if components is not None else COMPONENTS
    )


def read_back(entity, components=None, **kwargs):
    """entity -> definition -> entity, the way the store round-trips it."""
    graph = Graph()
    graph.parse(data=export(entity, components, **kwargs), format="turtle")
    return PipelineSerializer().from_sparql_to_elody(
        {"iri": PIPELINE_IRI, "graph": graph}
    )


def metadata_of(entity, key):
    return next(
        (
            item["value"]
            for item in entity.get("metadata", [])
            if item["key"] == key
        ),
        None,
    )


def relation_metadata(entity, key) -> dict:
    for item in entity.get("relations", []):
        if item["key"] == key:
            return {m["key"]: m["value"] for m in item["metadata"]}
    return {}


@pytest.fixture
def restored():
    return read_back(pipeline(CHAIN))


class TestThePipelineItself:
    def test_the_entity_is_a_pipeline(self, restored):
        assert restored["type"] == "pipeline"

    def test_it_keeps_the_id_it_was_saved_under(self, restored):
        """The id is what every URL in Elody addresses it by.

        It is carried in the definition rather than parsed out of the IRI, so
        moving the deployment behind another host does not rename pipelines.
        """
        assert restored["_id"] == "pipeline-1"

    def test_the_definition_iri_becomes_an_identifier(self, restored):
        assert restored["identifiers"] == ["pipeline-1", PIPELINE_IRI]

    def test_name_and_description_come_back(self, restored):
        assert metadata_of(restored, "name") == "My Pipeline"
        assert metadata_of(restored, "description") == "A demo chain."

    def test_a_pipeline_with_no_steps_still_reads(self):
        restored = read_back(pipeline([]))

        assert restored["_id"] == "pipeline-1"
        assert restored["relations"] == []

    def test_a_graph_that_holds_no_pipeline_is_not_an_entity(self):
        assert PipelineSerializer().from_sparql_to_elody({"graph": Graph()}) == {}

    def test_the_pipeline_is_found_without_being_named(self):
        """The serializer is usable on a graph on its own."""
        graph = Graph()
        graph.parse(data=export(pipeline(CHAIN)), format="turtle")

        assert PipelineSerializer().from_sparql_to_elody({"graph": graph})["_id"] == (
            "pipeline-1"
        )


class TestSteps:
    def test_every_step_becomes_a_processor_relation(self, restored):
        assert [r["key"] for r in restored["relations"]] == [
            "acme--poller",
            "acme--sink",
        ]

    def test_relations_are_processor_relations(self, restored):
        assert {r["type"] for r in restored["relations"]} == {"hasProcessor"}

    def test_steps_come_back_in_dependency_order(self):
        """Producers before what they feed, whatever the authored order was.

        A plan is a set of steps; the only order the definition records is the
        one the channels describe. Reading it back in graph-iteration order
        would make the processor list shuffle between two reads of the same
        pipeline.
        """
        reversed_chain = [
            connected("acme--sink", "input", "acme--monitor|output"),
            connected("acme--monitor", "input", "acme--poller|output"),
            relation("acme--poller"),
        ]

        restored = read_back(pipeline(reversed_chain))

        assert [r["key"] for r in restored["relations"]] == [
            "acme--poller",
            "acme--monitor",
            "acme--sink",
        ]

    def test_a_dataset_source_comes_back_as_a_processor_relation(self):
        """A dataset is not a step in the plan, but it is one in Elody.

        It is not deployable, so the definition declares it as a source rather
        than a `tcs:InstancePipelineComponent` -- but the builder wires it like
        any other producer, so it has to come back as a relation.
        """
        restored = read_back(
            pipeline(
                [
                    relation("acme--measurements"),
                    connected("acme--sink", "input", "acme--measurements|output"),
                ]
            )
        )

        assert [r["key"] for r in restored["relations"]] == [
            "acme--measurements",
            "acme--sink",
        ]

    def test_the_same_component_used_twice_comes_back_twice(self):
        """Two stages of one component are two steps, and stay two relations.

        The export gives them distinct step IRIs; both specialise the same
        component, so both resolve to the same relation key. Collapsing them
        here would quietly delete a stage of the user's pipeline.
        """
        restored = read_back(
            pipeline([relation("acme--poller"), relation("acme--poller")])
        )

        assert [r["key"] for r in restored["relations"]] == [
            "acme--poller",
            "acme--poller",
        ]


class TestConfigValues:
    def test_a_plain_value_round_trips(self, restored):
        assert relation_metadata(restored, "acme--poller")["url"] == (
            "https://sensors.example/now"
        )

    def test_a_typed_value_keeps_the_form_it_was_typed_in(self):
        """`"10000"` comes back as `"10000"`, not as `10000`.

        The shape types it on the way out and will type it again next time; the
        entity holds what the form field held, and turning it into an int here
        would make the round trip lossy in the one direction nobody checks.
        """
        restored = read_back(
            pipeline([relation("acme--poller", [{"key": "interval", "value": "10000"}])])
        )

        assert relation_metadata(restored, "acme--poller")["interval"] == "10000"

    def test_a_boolean_comes_back_as_a_boolean(self):
        """The exception: a checkbox is stored as a boolean, not as "true"."""
        restored = read_back(
            pipeline([relation("acme--sink", [{"key": "fatal", "value": True}])])
        )

        assert relation_metadata(restored, "acme--sink")["fatal"] is True

    def test_nested_config_keeps_its_dotted_key(self):
        restored = read_back(
            pipeline(
                [relation("acme--nested", [{"key": "options.method", "value": "POST"}])]
            )
        )

        assert relation_metadata(restored, "acme--nested")["options.method"] == "POST"

    def test_a_channel_typed_value_comes_back_as_the_channel_name(self):
        restored = read_back(
            pipeline([relation("acme--poller", [{"key": "output", "value": "members"}])])
        )

        assert relation_metadata(restored, "acme--poller")["output"] == "members"

    def test_a_key_no_config_shape_declares_cannot_come_back(self):
        """It was never published: a definition can only carry predicates."""
        restored = read_back(
            pipeline([relation("acme--poller", [{"key": "nonsense", "value": "x"}])])
        )

        assert "nonsense" not in relation_metadata(restored, "acme--poller")


class TestConnections:
    def test_a_connection_round_trips(self, restored):
        assert relation_metadata(restored, "acme--sink")["connections.input.from"] == (
            "acme--poller|output"
        )

    def test_the_channel_is_named_on_both_ends(self, restored):
        """The port config and the annotation agree, as they did on export."""
        assert relation_metadata(restored, "acme--poller")["output"] == (
            relation_metadata(restored, "acme--sink")["input"]
        )

    def test_a_default_channel_name_is_not_written_out(self, restored):
        """The default is derived from the two ends, so storing it adds a key
        the pipeline never had -- and the next export would derive it again."""
        assert "connections.input.channel" not in relation_metadata(
            restored, "acme--sink"
        )

    def test_a_chosen_channel_name_is_kept(self):
        restored = read_back(
            pipeline(
                [
                    relation("acme--poller"),
                    connected("acme--sink", "input", "acme--poller|output")
                    | {
                        "metadata": [
                            {
                                "key": "connections.input.from",
                                "value": "acme--poller|output",
                            },
                            {"key": "connections.input.channel", "value": "json"},
                        ]
                    },
                ]
            )
        )

        assert relation_metadata(restored, "acme--sink")[
            "connections.input.channel"
        ] == "json"

    def test_a_dataset_feeds_from_its_synthetic_output_port(self):
        """A dataset has no config shape, so no property names its port."""
        restored = read_back(
            pipeline(
                [
                    relation("acme--measurements"),
                    connected("acme--sink", "input", "acme--measurements|output"),
                ]
            )
        )

        assert relation_metadata(restored, "acme--sink")["connections.input.from"] == (
            "acme--measurements|output"
        )

    def test_a_three_step_chain_keeps_both_links(self):
        restored = read_back(
            pipeline(
                [
                    relation("acme--poller"),
                    connected("acme--monitor", "input", "acme--poller|output"),
                    connected("acme--sink", "input", "acme--monitor|output"),
                ]
            )
        )

        assert relation_metadata(restored, "acme--monitor")[
            "connections.input.from"
        ] == "acme--poller|output"
        assert relation_metadata(restored, "acme--sink")[
            "connections.input.from"
        ] == "acme--monitor|output"


class TestRoundTripIsStable:
    def test_reading_and_re_exporting_yields_the_same_definition(self):
        """The fixed point that matters.

        A pipeline read out of the store and written straight back must not
        drift, or every save would produce a new definition and the store would
        churn on documents nobody edited.
        """
        first = export(pipeline(CHAIN))
        restored = read_back(pipeline(CHAIN))
        second = export(restored)

        a, b = Graph(), Graph()
        a.parse(data=first, format="turtle")
        b.parse(data=second, format="turtle")
        assert a.isomorphic(b)

    def test_a_second_round_trip_changes_nothing(self):
        once = read_back(pipeline(CHAIN))
        twice = read_back(once)

        assert once == twice


class TestWhatDoesNotRoundTrip:
    def test_validation_verdicts_are_not_carried(self):
        """They are Elody's reading of the chain, not part of the definition.

        The store is shared with the toolchain; publishing our verdicts there
        would make one service's opinion look like the pipeline's own. They are
        recomputed on demand instead.
        """
        stamped = pipeline(
            [
                relation("acme--poller"),
                connected("acme--sink", "input", "acme--poller|output")
                | {
                    "metadata": [
                        {
                            "key": "connections.input.from",
                            "value": "acme--poller|output",
                        },
                        {"key": "connections.input.state", "value": "valid"},
                        {"key": "connections.input.stateMessage", "value": "fine"},
                    ]
                },
            ]
        )

        restored = read_back(stamped)

        keys = relation_metadata(restored, "acme--sink")
        assert "connections.input.state" not in keys
        assert "connections.input.stateMessage" not in keys

    def test_without_the_catalog_fragment_a_step_cannot_be_placed(self):
        """`?catalog=false` drops what names the components.

        The definition still says which component IRI each step specialises,
        but not which Elody document that is, so there is no relation key to
        store it under. Publication always ships the fragment; this asserts why
        it has to.
        """
        restored = read_back(pipeline(CHAIN), include_catalog=False)

        assert restored["relations"] == []
