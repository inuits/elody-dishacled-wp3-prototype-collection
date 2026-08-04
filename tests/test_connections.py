"""Tests for directed, typed producer->consumer links between components.

A connection says "component A's output port feeds component B's input port,
over channel C". It is authored on the consumer side (an input port picks its
producer) and read back as a canonical Connection, so the pipeline export and
the B3 validation both see the same directed graph.
"""

import pytest

from apps.dishacled.pipeline.connections import (
    Connection,
    Port,
    PORT_SEPARATOR,
    STATE_UNVALIDATED,
    connection_metadata,
    connections_for_pipeline,
    input_ports,
    nest_metadata,
    output_ports,
    parse_port_reference,
    producer_options_for,
    ports_for_component,
)
from apps.dishacled.storage.local_component_source import LocalComponentSource


CM_SHAPE = "https://elody.local/dishacled/demo#MeasurementsInCmShape"
MM_SHAPE = "https://elody.local/dishacled/demo#MeasurementsInMmShape"


def shape(iri, label):
    return {"iri": iri, "label": label, "ttl": "", "properties": []}


def make_component(
    identifier,
    name,
    properties,
    input_shape=None,
    output_shape=None,
    kind="component",
):
    data = {
        "properties": properties,
        "componentIri": f"https://elody.local/dishacled/demo#{name}",
        "componentKind": kind,
    }
    if input_shape:
        data["inputShape"] = shape(input_shape, "in")
    if output_shape:
        data["outputShape"] = shape(output_shape, "out")
    return {
        "_id": identifier,
        "identifiers": [identifier],
        "type": "githubProcessor",
        "metadata": [{"key": "name", "value": name}],
        "relations": [],
        "data": data,
    }


def prop(name, class_ref=None, required=False):
    return {
        "name": name,
        "inputFieldType": "baseTextField",
        "isRequired": required,
        "inValues": [],
        "classRef": class_ref,
    }


POLLER_CM = make_component(
    "local--http-poller-cm",
    "Http poller (cm)",
    [prop("url"), prop("output", "rdfc:Writer", required=True)],
    output_shape=CM_SHAPE,
)
POLLER_MM = make_component(
    "local--http-poller-mm",
    "Http poller (mm)",
    [prop("url"), prop("output", "rdfc:Writer", required=True)],
    output_shape=MM_SHAPE,
)
MONITOR_CM = make_component(
    "local--threshold-monitor-cm",
    "Threshold monitor (cm)",
    [
        prop("threshold"),
        prop("input", "rdfc:Reader", required=True),
        prop("output", "rdfc:Writer", required=True),
    ],
    input_shape=CM_SHAPE,
    output_shape=CM_SHAPE,
)
DATASET_CM = make_component(
    "local--sensor-feed-cm",
    "Sensor feed (cm)",
    [],
    output_shape=CM_SHAPE,
    kind="dataset",
)

COMPONENTS = {c["_id"]: c for c in [POLLER_CM, POLLER_MM, MONITOR_CM, DATASET_CM]}


def make_pipeline(relations):
    return {
        "_id": "pipeline-1",
        "identifiers": ["pipeline-1"],
        "type": "pipeline",
        "metadata": [{"key": "name", "value": "Demo pipeline"}],
        "relations": relations,
    }


def processor_relation(key, metadata=None):
    return {"key": key, "type": "hasProcessor", "metadata": metadata or []}


class TestPorts:
    def test_writer_property_becomes_an_output_port(self):
        ports = output_ports(ports_for_component(POLLER_CM))
        assert [p.name for p in ports] == ["output"]
        assert ports[0].direction == "out"
        assert ports[0].role == "output"

    def test_reader_property_becomes_an_input_port(self):
        ports = input_ports(ports_for_component(MONITOR_CM))
        assert [p.name for p in ports] == ["input"]
        assert ports[0].direction == "in"
        assert ports[0].role == "input"

    def test_plain_config_properties_are_not_ports(self):
        names = [p.name for p in ports_for_component(POLLER_CM)]
        assert "url" not in names

    def test_port_carries_the_contract_shape_for_its_role(self):
        out = output_ports(ports_for_component(MONITOR_CM))[0]
        inp = input_ports(ports_for_component(MONITOR_CM))[0]
        assert out.shape_iri == CM_SHAPE
        assert inp.shape_iri == CM_SHAPE

    def test_port_knows_which_component_it_belongs_to(self):
        port = output_ports(ports_for_component(POLLER_CM))[0]
        assert port.component == "local--http-poller-cm"
        assert port.reference == f"local--http-poller-cm{PORT_SEPARATOR}output"

    def test_component_without_a_contract_still_yields_ports(self):
        plain = {
            "_id": "rdfc--ldes-client",
            "metadata": [{"key": "name", "value": "ldes client"}],
            "data": {"properties": [prop("writer", "rdfc:Writer")]},
        }
        ports = output_ports(ports_for_component(plain))
        assert [p.name for p in ports] == ["writer"]
        assert ports[0].shape_iri is None

    def test_dataset_without_config_gets_a_synthetic_output_port(self):
        ports = ports_for_component(DATASET_CM)
        assert [p.name for p in ports] == ["output"]
        assert ports[0].direction == "out"
        assert ports[0].shape_iri == CM_SHAPE

    def test_dataset_has_no_input_port(self):
        assert input_ports(ports_for_component(DATASET_CM)) == []

    def test_channel_class_yields_both_directions(self):
        component = make_component(
            "x--y", "Both", [prop("channel", "rdfc:Channel")]
        )
        ports = ports_for_component(component)
        assert {p.direction for p in ports} == {"in", "out"}

    def test_empty_document_yields_no_ports(self):
        assert ports_for_component({}) == []


class TestPortReference:
    def test_round_trip(self):
        port = Port(
            component="a",
            name="output",
            direction="out",
            role="output",
            label="output",
        )
        assert parse_port_reference(port.reference) == ("a", "output")

    def test_missing_reference_is_empty(self):
        assert parse_port_reference(None) == (None, None)
        assert parse_port_reference("") == (None, None)

    def test_reference_without_separator_is_not_a_port(self):
        assert parse_port_reference("just-a-component") == (None, None)

    def test_component_ids_containing_dashes_survive(self):
        assert parse_port_reference(
            f"local--http-poller-cm{PORT_SEPARATOR}output"
        ) == ("local--http-poller-cm", "output")


class TestNestMetadata:
    def test_dotted_keys_become_nested(self):
        assert nest_metadata(
            [{"key": "connections.input.from", "value": "a|out"}]
        ) == {"connections": {"input": {"from": "a|out"}}}

    def test_entries_without_a_key_are_skipped(self):
        assert nest_metadata([{"value": "x"}]) == {}


class TestConnectionsForPipeline:
    def test_a_configured_input_port_yields_a_connection(self):
        pipeline = make_pipeline(
            [
                processor_relation("local--http-poller-cm"),
                processor_relation(
                    "local--threshold-monitor-cm",
                    connection_metadata("input", "local--http-poller-cm|output"),
                ),
            ]
        )
        connections = connections_for_pipeline(pipeline, COMPONENTS)
        assert len(connections) == 1
        connection = connections[0]
        assert connection.source == "local--http-poller-cm"
        assert connection.source_port == "output"
        assert connection.target == "local--threshold-monitor-cm"
        assert connection.target_port == "input"

    def test_connection_is_directed(self):
        pipeline = make_pipeline(
            [
                processor_relation("local--http-poller-cm"),
                processor_relation(
                    "local--threshold-monitor-cm",
                    connection_metadata("input", "local--http-poller-cm|output"),
                ),
            ]
        )
        connection = connections_for_pipeline(pipeline, COMPONENTS)[0]
        assert connection.source_role == "output"
        assert connection.target_role == "input"

    def test_connection_carries_both_shapes(self):
        pipeline = make_pipeline(
            [
                processor_relation("local--http-poller-cm"),
                processor_relation(
                    "local--threshold-monitor-cm",
                    connection_metadata("input", "local--http-poller-cm|output"),
                ),
            ]
        )
        connection = connections_for_pipeline(pipeline, COMPONENTS)[0]
        assert connection.source_shape == CM_SHAPE
        assert connection.target_shape == CM_SHAPE

    def test_state_defaults_to_the_unvalidated_placeholder(self):
        pipeline = make_pipeline(
            [
                processor_relation("local--http-poller-cm"),
                processor_relation(
                    "local--threshold-monitor-cm",
                    connection_metadata("input", "local--http-poller-cm|output"),
                ),
            ]
        )
        connection = connections_for_pipeline(pipeline, COMPONENTS)[0]
        assert connection.state == STATE_UNVALIDATED
        assert connection.state_message == ""

    def test_a_stored_state_wins_over_the_placeholder(self):
        metadata = connection_metadata("input", "local--http-poller-cm|output")
        metadata += [
            {"key": "connections.input.state", "value": "invalid"},
            {"key": "connections.input.stateMessage", "value": "unit mismatch"},
        ]
        pipeline = make_pipeline(
            [
                processor_relation("local--http-poller-cm"),
                processor_relation("local--threshold-monitor-cm", metadata),
            ]
        )
        connection = connections_for_pipeline(pipeline, COMPONENTS)[0]
        assert connection.state == "invalid"
        assert connection.state_message == "unit mismatch"

    def test_a_mismatching_pair_still_connects(self):
        # B3 reports the mismatch; B2 must not silently refuse to model it,
        # otherwise the cm->mm demo cannot be composed at all.
        pipeline = make_pipeline(
            [
                processor_relation("local--http-poller-mm"),
                processor_relation(
                    "local--threshold-monitor-cm",
                    connection_metadata("input", "local--http-poller-mm|output"),
                ),
            ]
        )
        connection = connections_for_pipeline(pipeline, COMPONENTS)[0]
        assert connection.source_shape == MM_SHAPE
        assert connection.target_shape == CM_SHAPE
        assert connection.is_shape_match is False

    def test_a_matching_pair_reports_a_shape_match(self):
        pipeline = make_pipeline(
            [
                processor_relation("local--http-poller-cm"),
                processor_relation(
                    "local--threshold-monitor-cm",
                    connection_metadata("input", "local--http-poller-cm|output"),
                ),
            ]
        )
        assert connections_for_pipeline(pipeline, COMPONENTS)[0].is_shape_match

    def test_a_dataset_can_be_the_source(self):
        pipeline = make_pipeline(
            [
                processor_relation("local--sensor-feed-cm"),
                processor_relation(
                    "local--threshold-monitor-cm",
                    connection_metadata("input", "local--sensor-feed-cm|output"),
                ),
            ]
        )
        connection = connections_for_pipeline(pipeline, COMPONENTS)[0]
        assert connection.source == "local--sensor-feed-cm"
        assert connection.source_shape == CM_SHAPE

    def test_channel_defaults_to_a_derived_name(self):
        pipeline = make_pipeline(
            [
                processor_relation("local--http-poller-cm"),
                processor_relation(
                    "local--threshold-monitor-cm",
                    connection_metadata("input", "local--http-poller-cm|output"),
                ),
            ]
        )
        connection = connections_for_pipeline(pipeline, COMPONENTS)[0]
        assert connection.channel == "http-poller-cm-output-to-threshold-monitor-cm-input"

    def test_an_explicit_channel_is_kept(self):
        pipeline = make_pipeline(
            [
                processor_relation("local--http-poller-cm"),
                processor_relation(
                    "local--threshold-monitor-cm",
                    connection_metadata(
                        "input",
                        "local--http-poller-cm|output",
                        channel="measurements",
                    ),
                ),
            ]
        )
        connection = connections_for_pipeline(pipeline, COMPONENTS)[0]
        assert connection.channel == "measurements"

    def test_an_unset_input_port_yields_no_connection(self):
        pipeline = make_pipeline(
            [
                processor_relation("local--http-poller-cm"),
                processor_relation(
                    "local--threshold-monitor-cm",
                    [{"key": "connections.input.from", "value": ""}],
                ),
            ]
        )
        assert connections_for_pipeline(pipeline, COMPONENTS) == []

    def test_a_source_outside_the_pipeline_is_ignored(self):
        pipeline = make_pipeline(
            [
                processor_relation(
                    "local--threshold-monitor-cm",
                    connection_metadata("input", "local--http-poller-cm|output"),
                )
            ]
        )
        assert connections_for_pipeline(pipeline, COMPONENTS) == []

    def test_a_component_cannot_feed_itself(self):
        pipeline = make_pipeline(
            [
                processor_relation(
                    "local--threshold-monitor-cm",
                    connection_metadata(
                        "input", "local--threshold-monitor-cm|output"
                    ),
                )
            ]
        )
        assert connections_for_pipeline(pipeline, COMPONENTS) == []

    def test_a_port_the_source_does_not_have_is_ignored(self):
        pipeline = make_pipeline(
            [
                processor_relation("local--http-poller-cm"),
                processor_relation(
                    "local--threshold-monitor-cm",
                    connection_metadata("input", "local--http-poller-cm|nope"),
                ),
            ]
        )
        assert connections_for_pipeline(pipeline, COMPONENTS) == []

    def test_non_processor_relations_are_ignored(self):
        pipeline = make_pipeline(
            [{"key": "runner-1", "type": "hasRunner", "metadata": []}]
        )
        assert connections_for_pipeline(pipeline, COMPONENTS) == []

    def test_connections_are_ordered_and_identified(self):
        pipeline = make_pipeline(
            [
                processor_relation("local--http-poller-cm"),
                processor_relation(
                    "local--threshold-monitor-cm",
                    connection_metadata("input", "local--http-poller-cm|output"),
                ),
            ]
        )
        connection = connections_for_pipeline(pipeline, COMPONENTS)[0]
        assert connection.id == (
            "local--http-poller-cm|output->local--threshold-monitor-cm|input"
        )

    def test_to_dict_exposes_the_state_placeholders(self):
        pipeline = make_pipeline(
            [
                processor_relation("local--http-poller-cm"),
                processor_relation(
                    "local--threshold-monitor-cm",
                    connection_metadata("input", "local--http-poller-cm|output"),
                ),
            ]
        )
        payload = connections_for_pipeline(pipeline, COMPONENTS)[0].to_dict()
        assert payload["state"] == STATE_UNVALIDATED
        assert payload["stateMessage"] == ""
        assert payload["sourceLabel"] == "Http poller (cm)"
        assert payload["targetLabel"] == "Threshold monitor (cm)"


class TestProducerOptions:
    def test_lists_output_ports_of_the_other_components(self):
        pipeline = make_pipeline(
            [
                processor_relation("local--http-poller-cm"),
                processor_relation("local--http-poller-mm"),
                processor_relation("local--threshold-monitor-cm"),
            ]
        )
        options = producer_options_for(
            pipeline, COMPONENTS, "local--threshold-monitor-cm"
        )
        assert {o["value"] for o in options} == {
            "local--http-poller-cm|output",
            "local--http-poller-mm|output",
        }

    def test_excludes_the_target_itself(self):
        pipeline = make_pipeline(
            [processor_relation("local--threshold-monitor-cm")]
        )
        assert (
            producer_options_for(
                pipeline, COMPONENTS, "local--threshold-monitor-cm"
            )
            == []
        )

    def test_incompatible_producers_are_offered_but_marked(self):
        pipeline = make_pipeline(
            [
                processor_relation("local--http-poller-mm"),
                processor_relation("local--threshold-monitor-cm"),
            ]
        )
        options = producer_options_for(
            pipeline,
            COMPONENTS,
            "local--threshold-monitor-cm",
            target_shape=CM_SHAPE,
        )
        assert len(options) == 1
        assert options[0]["isShapeMatch"] is False

    def test_compatible_producers_are_marked_as_matching(self):
        pipeline = make_pipeline(
            [
                processor_relation("local--http-poller-cm"),
                processor_relation("local--threshold-monitor-cm"),
            ]
        )
        options = producer_options_for(
            pipeline,
            COMPONENTS,
            "local--threshold-monitor-cm",
            target_shape=CM_SHAPE,
        )
        assert options[0]["isShapeMatch"] is True

    def test_option_labels_name_the_component_and_the_port(self):
        pipeline = make_pipeline(
            [
                processor_relation("local--http-poller-cm"),
                processor_relation("local--threshold-monitor-cm"),
            ]
        )
        options = producer_options_for(
            pipeline, COMPONENTS, "local--threshold-monitor-cm"
        )
        assert options[0]["label"] == "Http poller (cm) → output"


class TestConnectionMetadata:
    def test_writes_the_dotted_keys_the_form_saves(self):
        assert connection_metadata("input", "a|out") == [
            {"key": "connections.input.from", "value": "a|out"}
        ]

    def test_channel_is_written_when_given(self):
        assert {"key": "connections.input.channel", "value": "ch"} in (
            connection_metadata("input", "a|out", channel="ch")
        )


class TestBundledCatalogComponents:
    """The demo components from the interim catalog really do expose ports."""

    @pytest.fixture
    def components(self):
        source = LocalComponentSource()
        return {d["_id"]: d for d in source.list_documents()}

    def test_every_demo_service_has_an_output_port(self, components):
        for identifier, document in components.items():
            if document["data"].get("componentKind") != "component":
                continue
            assert output_ports(ports_for_component(document)), identifier

    def test_the_dataset_has_an_output_port_and_no_input_port(self, components):
        dataset = components["local--sensor-feed-cm"]
        assert output_ports(ports_for_component(dataset))
        assert input_ports(ports_for_component(dataset)) == []

    def test_a_cm_chain_connects_and_matches(self, components):
        pipeline = make_pipeline(
            [
                processor_relation("local--http-poller-cm"),
                processor_relation(
                    "local--threshold-monitor-cm",
                    connection_metadata("input", "local--http-poller-cm|output"),
                ),
            ]
        )
        connection = connections_for_pipeline(pipeline, components)[0]
        assert connection.is_shape_match is True

    def test_an_mm_producer_into_a_cm_consumer_is_a_mismatch(self, components):
        pipeline = make_pipeline(
            [
                processor_relation("local--http-poller-mm"),
                processor_relation(
                    "local--threshold-monitor-cm",
                    connection_metadata("input", "local--http-poller-mm|output"),
                ),
            ]
        )
        connection = connections_for_pipeline(pipeline, components)[0]
        assert connection.is_shape_match is False
        assert connection.state == STATE_UNVALIDATED

    def test_the_dataset_can_feed_the_monitor(self, components):
        pipeline = make_pipeline(
            [
                processor_relation("local--sensor-feed-cm"),
                processor_relation(
                    "local--threshold-monitor-cm",
                    connection_metadata("input", "local--sensor-feed-cm|output"),
                ),
            ]
        )
        connection = connections_for_pipeline(pipeline, components)[0]
        assert connection.source == "local--sensor-feed-cm"
        assert connection.is_shape_match is True


class TestConnectionDataclass:
    def test_is_hashable_and_comparable(self):
        a = Connection(
            source="a",
            source_port="out",
            target="b",
            target_port="in",
            channel="ch",
        )
        b = Connection(
            source="a",
            source_port="out",
            target="b",
            target_port="in",
            channel="ch",
        )
        assert a == b
        assert len({a, b}) == 1


class TestPortsOnComponentDocuments:
    """Ports travel on the entity, so the UI never re-derives them."""

    def test_local_component_document_exposes_its_ports(self):
        document = LocalComponentSource().get_document(
            "local--threshold-monitor-cm"
        )
        ports = document["data"]["ports"]
        assert {p["name"] for p in ports} == {"input", "output"}
        assert {p["direction"] for p in ports} == {"in", "out"}

    def test_exposed_ports_carry_their_shape_and_reference(self):
        document = LocalComponentSource().get_document("local--http-poller-cm")
        out = [p for p in document["data"]["ports"] if p["direction"] == "out"][0]
        assert out["shapeIri"] == document["data"]["outputShape"]["iri"]
        assert out["reference"] == "local--http-poller-cm|output"

    def test_dataset_document_exposes_only_an_output_port(self):
        document = LocalComponentSource().get_document("local--sensor-feed-cm")
        ports = document["data"]["ports"]
        assert [p["direction"] for p in ports] == ["out"]
