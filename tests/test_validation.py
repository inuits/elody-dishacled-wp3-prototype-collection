"""Tests for static validation of a pipeline chain.

Compatibility is decided the way the demonstrator asks for it: a sample is
produced under the producer's output shape and SHACL-validated against the
consumer's input shape, rather than by comparing the two shapes structurally.
That is what makes the cm->mm mismatch surface as a located, human-readable
violation instead of a bare "not equal".
"""

import pytest

from apps.dishacled.pipeline.connections import (
    STATE_INVALID,
    STATE_UNKNOWN,
    STATE_VALID,
    connection_metadata,
    connections_for_pipeline,
)
from apps.dishacled.pipeline.validation import (
    Violation,
    apply_validation_state,
    sample_for_shape,
    validate_pipeline,
    validate_shape_pair,
)
from apps.dishacled.storage.local_component_source import LocalComponentSource


DEMO = "https://dishacled.github.io/demo#"

CM_TTL = f"""
@prefix sh:   <http://www.w3.org/ns/shacl#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
@prefix demo: <{DEMO}> .

demo:MeasurementsInCmShape a sh:NodeShape ;
  sh:targetClass demo:Measurement ;
  sh:property [
    sh:path demo:unit ; sh:name "unit" ; sh:datatype xsd:string ;
    sh:hasValue "cm" ; sh:in ( "cm" ) ; sh:minCount 1 ; sh:maxCount 1 ;
  ] , [
    sh:path demo:value ; sh:name "value" ; sh:datatype xsd:decimal ;
    sh:minCount 1 ; sh:maxCount 1 ;
  ] .
"""

MM_TTL = CM_TTL.replace("Cm", "Mm").replace('"cm"', '"mm"')

# Same unit, but it additionally demands a field the measurement shapes do not
# produce -- the "consumer wants more than the producer offers" case.
STRICT_CM_TTL = f"""
@prefix sh:   <http://www.w3.org/ns/shacl#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
@prefix demo: <{DEMO}> .

demo:StrictCmShape a sh:NodeShape ;
  sh:targetClass demo:Measurement ;
  sh:property [
    sh:path demo:unit ; sh:name "unit" ; sh:datatype xsd:string ;
    sh:hasValue "cm" ; sh:minCount 1 ;
  ] , [
    sh:path demo:sensorId ; sh:name "sensorId" ; sh:datatype xsd:string ;
    sh:minCount 1 ;
  ] .
"""

CM_SHAPE = f"{DEMO}MeasurementsInCmShape"
MM_SHAPE = f"{DEMO}MeasurementsInMmShape"
STRICT_SHAPE = f"{DEMO}StrictCmShape"


def shape_payload(iri, ttl):
    return {"iri": iri, "label": iri.split("#")[-1], "ttl": ttl, "properties": []}


CM = shape_payload(CM_SHAPE, CM_TTL)
MM = shape_payload(MM_SHAPE, MM_TTL)
STRICT = shape_payload(STRICT_SHAPE, STRICT_CM_TTL)


def prop(name, class_ref=None, required=False):
    return {
        "name": name,
        "inputFieldType": "baseTextField",
        "isRequired": required,
        "inValues": [],
        "classRef": class_ref,
    }


def make_component(
    identifier, name, properties, input_shape=None, output_shape=None, kind="component"
):
    data = {
        "properties": properties,
        "componentIri": f"{DEMO}{name}",
        "componentKind": kind,
    }
    if input_shape:
        data["inputShape"] = input_shape
    if output_shape:
        data["outputShape"] = output_shape
    document = {
        "_id": identifier,
        "identifiers": [identifier],
        "type": "githubProcessor",
        "metadata": [{"key": "name", "value": name}],
        "relations": [],
        "data": data,
    }
    from apps.dishacled.pipeline.connections import ports_for_component

    document["data"]["ports"] = [p.to_dict() for p in ports_for_component(document)]
    return document


POLLER_CM = make_component(
    "poller-cm",
    "Poller cm",
    [prop("url"), prop("output", "rdfc:Writer", required=True)],
    output_shape=CM,
)
POLLER_MM = make_component(
    "poller-mm",
    "Poller mm",
    [prop("url"), prop("output", "rdfc:Writer", required=True)],
    output_shape=MM,
)
MONITOR_CM = make_component(
    "monitor-cm",
    "Monitor cm",
    [
        prop("input", "rdfc:Reader", required=True),
        prop("output", "rdfc:Writer", required=True),
    ],
    input_shape=CM,
    output_shape=CM,
)
SINK_CM = make_component(
    "sink-cm",
    "Sink cm",
    [prop("input", "rdfc:Reader", required=True)],
    input_shape=CM,
)
STRICT_SINK = make_component(
    "strict-sink",
    "Strict sink",
    [prop("input", "rdfc:Reader", required=True)],
    input_shape=STRICT,
)
# no contract at all: nothing is known about what it produces
UNTYPED_POLLER = make_component(
    "untyped-poller",
    "Untyped poller",
    [prop("output", "rdfc:Writer", required=True)],
)

COMPONENTS = {
    c["_id"]: c
    for c in [
        POLLER_CM,
        POLLER_MM,
        MONITOR_CM,
        SINK_CM,
        STRICT_SINK,
        UNTYPED_POLLER,
    ]
}


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


def connected(target, port, source_reference):
    return processor_relation(target, connection_metadata(port, source_reference))


class TestSampleGeneration:
    """A sample stands in for the data a producer would actually emit."""

    def test_a_sample_is_typed_with_the_shapes_target_class(self):
        graph, node = sample_for_shape(CM_TTL, CM_SHAPE)
        types = {str(o) for o in graph.objects(node, None)}
        assert f"{DEMO}Measurement" in types

    def test_a_sample_carries_the_value_the_shape_fixes(self):
        graph, node = sample_for_shape(CM_TTL, CM_SHAPE)
        units = [str(o) for o in graph.objects(node, None) if str(o) in ("cm", "mm")]
        assert units == ["cm"]

    def test_a_sample_fills_every_required_property(self):
        graph, node = sample_for_shape(CM_TTL, CM_SHAPE)
        paths = {str(p) for p in graph.predicates(node, None)}
        assert f"{DEMO}unit" in paths
        assert f"{DEMO}value" in paths

    def test_a_sample_produced_under_a_shape_satisfies_that_shape(self):
        assert validate_shape_pair(CM, CM) == []

    def test_an_unparseable_shape_yields_no_sample(self):
        assert sample_for_shape("this is not turtle", None) is None


class TestShapePairValidation:
    def test_matching_shapes_produce_no_violations(self):
        assert validate_shape_pair(CM, CM) == []

    def test_a_unit_mismatch_is_reported(self):
        violations = validate_shape_pair(MM, CM)
        assert violations, "an mm producer must not satisfy a cm consumer"

    def test_a_unit_mismatch_names_the_expected_and_actual_value(self):
        violations = validate_shape_pair(MM, CM)
        assert "cm" in " ".join(str(v.expected) for v in violations)
        assert "mm" in " ".join(str(v.actual) for v in violations)

    def test_a_unit_mismatch_names_the_property(self):
        violations = validate_shape_pair(MM, CM)
        assert any(v.path and "unit" in v.path for v in violations)

    def test_a_missing_property_is_reported_as_a_cardinality_violation(self):
        violations = validate_shape_pair(CM, STRICT)
        assert any("sensorId" in (v.path or "") for v in violations)

    def test_the_message_is_human_readable(self):
        violations = validate_shape_pair(MM, CM)
        assert all(v.message and not v.message.startswith("http") for v in violations)

    def test_an_absent_producer_shape_is_a_warning_not_a_failure(self):
        violations = validate_shape_pair(None, CM)
        assert [v.constraint for v in violations] == ["missingShape"]
        assert violations[0].severity == "warning"

    def test_an_absent_consumer_shape_is_a_warning_not_a_failure(self):
        violations = validate_shape_pair(CM, None)
        assert [v.constraint for v in violations] == ["missingShape"]
        assert violations[0].severity == "warning"

    def test_a_shape_payload_without_turtle_is_treated_as_missing(self):
        violations = validate_shape_pair({"iri": CM_SHAPE, "ttl": ""}, CM)
        assert [v.constraint for v in violations] == ["missingShape"]


class TestViolationShape:
    """The structured violation list the brief specifies."""

    def test_a_violation_serializes_the_documented_keys(self):
        violation = Violation(
            source="a|output",
            target="b|input",
            constraint="hasValue",
            expected="cm",
            actual="mm",
            message="unit mismatch",
        )
        payload = violation.to_dict()
        for key in ("from", "to", "constraint", "expected", "actual", "message"):
            assert key in payload

    def test_from_and_to_are_port_references(self):
        pipeline = make_pipeline(
            [
                processor_relation("poller-mm"),
                connected("monitor-cm", "input", "poller-mm|output"),
            ]
        )
        report = validate_pipeline(pipeline, COMPONENTS)
        assert all(v.to_dict()["from"] == "poller-mm|output" for v in report.violations)
        assert all(v.to_dict()["to"] == "monitor-cm|input" for v in report.violations)


class TestCompatibleChain:
    def test_a_matching_chain_reports_no_violations(self):
        pipeline = make_pipeline(
            [
                processor_relation("poller-cm"),
                connected("monitor-cm", "input", "poller-cm|output"),
            ]
        )
        report = validate_pipeline(pipeline, COMPONENTS)
        assert report.violations == []
        assert report.is_valid

    def test_a_matching_connection_is_marked_valid(self):
        pipeline = make_pipeline(
            [
                processor_relation("poller-cm"),
                connected("monitor-cm", "input", "poller-cm|output"),
            ]
        )
        report = validate_pipeline(pipeline, COMPONENTS)
        assert [c.state for c in report.connections] == [STATE_VALID]

    def test_a_pipeline_without_connections_is_valid(self):
        pipeline = make_pipeline(
            [processor_relation("poller-cm"), processor_relation("monitor-cm")]
        )
        report = validate_pipeline(pipeline, COMPONENTS)
        assert report.is_valid
        assert report.connections == []


class TestUnitMismatch:
    """The demo scenario: changing the source unit cm->mm breaks the chain."""

    @pytest.fixture
    def report(self):
        pipeline = make_pipeline(
            [
                processor_relation("poller-mm"),
                connected("monitor-cm", "input", "poller-mm|output"),
            ]
        )
        return validate_pipeline(pipeline, COMPONENTS)

    def test_the_mismatch_is_flagged(self, report):
        assert not report.is_valid
        assert report.violations

    def test_the_mismatch_is_located_on_the_offending_connection(self, report):
        violation = report.violations[0].to_dict()
        assert violation["from"] == "poller-mm|output"
        assert violation["to"] == "monitor-cm|input"

    def test_the_message_names_both_components(self, report):
        message = report.violations[0].message
        assert "Poller mm" in message
        assert "Monitor cm" in message

    def test_the_connection_is_marked_invalid(self, report):
        assert [c.state for c in report.connections] == [STATE_INVALID]

    def test_the_connection_carries_the_reason(self, report):
        assert "unit" in report.connections[0].state_message

    def test_swapping_the_source_back_to_cm_validates_clean(self):
        pipeline = make_pipeline(
            [
                processor_relation("poller-cm"),
                connected("monitor-cm", "input", "poller-cm|output"),
            ]
        )
        assert validate_pipeline(pipeline, COMPONENTS).is_valid


class TestMissingShape:
    @pytest.fixture
    def report(self):
        pipeline = make_pipeline(
            [
                processor_relation("untyped-poller"),
                connected("monitor-cm", "input", "untyped-poller|output"),
            ]
        )
        return validate_pipeline(pipeline, COMPONENTS)

    def test_an_untyped_producer_is_reported(self, report):
        assert [v.constraint for v in report.violations] == ["missingShape"]

    def test_an_untyped_producer_does_not_block_export(self, report):
        assert report.is_valid

    def test_an_untyped_connection_state_is_unknown(self, report):
        assert [c.state for c in report.connections] == [STATE_UNKNOWN]


class TestMultiHopChain:
    """poller -> monitor -> sink: every hop is validated, not just the first."""

    def test_a_compatible_three_stage_chain_is_clean(self):
        pipeline = make_pipeline(
            [
                processor_relation("poller-cm"),
                connected("monitor-cm", "input", "poller-cm|output"),
                connected("sink-cm", "input", "monitor-cm|output"),
            ]
        )
        report = validate_pipeline(pipeline, COMPONENTS)
        assert report.is_valid
        assert len(report.connections) == 2

    def test_a_break_in_the_second_hop_is_found(self):
        pipeline = make_pipeline(
            [
                processor_relation("poller-cm"),
                connected("monitor-cm", "input", "poller-cm|output"),
                connected("strict-sink", "input", "monitor-cm|output"),
            ]
        )
        report = validate_pipeline(pipeline, COMPONENTS)
        assert not report.is_valid
        assert all(v.to_dict()["to"] == "strict-sink|input" for v in report.violations)

    def test_a_break_in_the_first_hop_leaves_the_second_hop_valid(self):
        pipeline = make_pipeline(
            [
                processor_relation("poller-mm"),
                connected("monitor-cm", "input", "poller-mm|output"),
                connected("sink-cm", "input", "monitor-cm|output"),
            ]
        )
        report = validate_pipeline(pipeline, COMPONENTS)
        states = {c.id: c.state for c in report.connections}
        assert states["poller-mm|output->monitor-cm|input"] == STATE_INVALID
        assert states["monitor-cm|output->sink-cm|input"] == STATE_VALID


class TestReportSerialization:
    def test_the_report_lists_violations_and_connections(self):
        pipeline = make_pipeline(
            [
                processor_relation("poller-mm"),
                connected("monitor-cm", "input", "poller-mm|output"),
            ]
        )
        payload = validate_pipeline(pipeline, COMPONENTS).to_dict()
        assert payload["isValid"] is False
        assert payload["violations"]
        assert payload["connections"]
        assert payload["summary"]

    def test_a_clean_report_summarizes_as_valid(self):
        pipeline = make_pipeline(
            [
                processor_relation("poller-cm"),
                connected("monitor-cm", "input", "poller-cm|output"),
            ]
        )
        payload = validate_pipeline(pipeline, COMPONENTS).to_dict()
        assert payload["isValid"] is True
        assert payload["violations"] == []


class TestStatePersistence:
    """The verdict is written back onto the relation the connection lives on.

    That is what makes it visible in the UI: the processor list renders the
    hasProcessor relation's metadata, so `connections.<port>.state` shows up
    next to the producer it was validated against.
    """

    def test_the_state_is_stamped_onto_the_consumers_relation(self):
        pipeline = make_pipeline(
            [
                processor_relation("poller-mm"),
                connected("monitor-cm", "input", "poller-mm|output"),
            ]
        )
        stamped = apply_validation_state(pipeline, COMPONENTS)
        relation = next(
            r for r in stamped["relations"] if r["key"] == "monitor-cm"
        )
        values = {m["key"]: m["value"] for m in relation["metadata"]}
        assert values["connections.input.state"] == STATE_INVALID
        assert "unit" in values["connections.input.stateMessage"]

    def test_a_clean_connection_is_stamped_valid(self):
        pipeline = make_pipeline(
            [
                processor_relation("poller-cm"),
                connected("monitor-cm", "input", "poller-cm|output"),
            ]
        )
        stamped = apply_validation_state(pipeline, COMPONENTS)
        relation = next(
            r for r in stamped["relations"] if r["key"] == "monitor-cm"
        )
        values = {m["key"]: m["value"] for m in relation["metadata"]}
        assert values["connections.input.state"] == STATE_VALID
        assert values["connections.input.stateMessage"] == ""

    def test_a_stale_state_is_replaced_rather_than_duplicated(self):
        pipeline = make_pipeline(
            [
                processor_relation("poller-cm"),
                processor_relation(
                    "monitor-cm",
                    connection_metadata("input", "poller-cm|output")
                    + [
                        {"key": "connections.input.state", "value": STATE_INVALID},
                        {"key": "connections.input.stateMessage", "value": "stale"},
                    ],
                ),
            ]
        )
        stamped = apply_validation_state(pipeline, COMPONENTS)
        relation = next(
            r for r in stamped["relations"] if r["key"] == "monitor-cm"
        )
        states = [
            m for m in relation["metadata"] if m["key"] == "connections.input.state"
        ]
        assert len(states) == 1
        assert states[0]["value"] == STATE_VALID

    def test_a_dropped_connection_leaves_no_orphan_state(self):
        pipeline = make_pipeline(
            [
                processor_relation("poller-cm"),
                processor_relation(
                    "monitor-cm",
                    [
                        {"key": "connections.input.from", "value": ""},
                        {"key": "connections.input.state", "value": STATE_INVALID},
                        {"key": "connections.input.stateMessage", "value": "stale"},
                    ],
                ),
            ]
        )
        stamped = apply_validation_state(pipeline, COMPONENTS)
        relation = next(
            r for r in stamped["relations"] if r["key"] == "monitor-cm"
        )
        values = {m["key"]: m["value"] for m in relation["metadata"]}
        assert values.get("connections.input.state", "") == ""

    def test_other_relation_metadata_is_untouched(self):
        pipeline = make_pipeline(
            [
                processor_relation("poller-cm"),
                processor_relation(
                    "monitor-cm",
                    connection_metadata("input", "poller-cm|output")
                    + [{"key": "threshold", "value": "12.5"}],
                ),
            ]
        )
        stamped = apply_validation_state(pipeline, COMPONENTS)
        relation = next(
            r for r in stamped["relations"] if r["key"] == "monitor-cm"
        )
        values = {m["key"]: m["value"] for m in relation["metadata"]}
        assert values["threshold"] == "12.5"

    def test_stamping_does_not_mutate_the_input_document(self):
        pipeline = make_pipeline(
            [
                processor_relation("poller-mm"),
                connected("monitor-cm", "input", "poller-mm|output"),
            ]
        )
        apply_validation_state(pipeline, COMPONENTS)
        relation = next(
            r for r in pipeline["relations"] if r["key"] == "monitor-cm"
        )
        keys = {m["key"] for m in relation["metadata"]}
        assert "connections.input.state" not in keys


class TestBundledDemoComponents:
    """The demo scenario end to end, on the real interim catalog."""

    @pytest.fixture
    def components(self):
        return {d["_id"]: d for d in LocalComponentSource().list_documents()}

    def cm_chain(self):
        return make_pipeline(
            [
                processor_relation("local--http-poller-cm"),
                connected(
                    "local--threshold-monitor-cm",
                    "input",
                    "local--http-poller-cm|output",
                ),
            ]
        )

    def mm_chain(self):
        return make_pipeline(
            [
                processor_relation("local--http-poller-mm"),
                connected(
                    "local--threshold-monitor-cm",
                    "input",
                    "local--http-poller-mm|output",
                ),
            ]
        )

    def test_the_matching_chain_validates_clean(self, components):
        report = validate_pipeline(self.cm_chain(), components)
        assert report.is_valid, report.to_dict()["summary"]

    def test_changing_the_source_unit_to_mm_breaks_it(self, components):
        report = validate_pipeline(self.mm_chain(), components)
        assert not report.is_valid

    def test_the_mm_violation_is_located_and_readable(self, components):
        violation = validate_pipeline(self.mm_chain(), components).violations[0]
        payload = violation.to_dict()
        # located by step, which is what the user sees on the canvas
        assert payload["from"] == "http-poller-mm|output"
        assert payload["to"] == "threshold-monitor-cm|input"
        assert "HTTP poller (mm)" in payload["message"]
        assert "Threshold monitor (cm)" in payload["message"]

    def test_the_dataset_source_feeds_a_matching_consumer(self, components):
        pipeline = make_pipeline(
            [
                processor_relation("local--sensor-feed-cm"),
                connected(
                    "local--threshold-monitor-cm",
                    "input",
                    "local--sensor-feed-cm|output",
                ),
            ]
        )
        assert validate_pipeline(pipeline, components).is_valid

    def test_the_dataset_source_does_not_feed_an_mm_consumer(self, components):
        pipeline = make_pipeline(
            [
                processor_relation("local--sensor-feed-cm"),
                connected(
                    "local--threshold-monitor-mm",
                    "input",
                    "local--sensor-feed-cm|output",
                ),
            ]
        )
        assert not validate_pipeline(pipeline, components).is_valid

    def test_the_connections_read_back_carry_the_verdict(self, components):
        pipeline = apply_validation_state(self.mm_chain(), components)
        connections = connections_for_pipeline(pipeline, components)
        assert [c.state for c in connections] == [STATE_INVALID]


class TestForcedExportWarning:
    """A pipeline exported despite failing validation says so in the file."""

    def report(self):
        pipeline = make_pipeline(
            [
                processor_relation("poller-mm"),
                connected("monitor-cm", "input", "poller-mm|output"),
            ]
        )
        return validate_pipeline(pipeline, COMPONENTS)

    def test_the_warning_is_a_turtle_comment_block(self):
        comments = self.report().as_turtle_comments()
        assert all(
            line.startswith("#") for line in comments.strip().splitlines()
        )

    def test_the_warning_repeats_every_violation(self):
        report = self.report()
        comments = report.as_turtle_comments()
        for violation in report.violations:
            assert violation.message in comments

    def test_the_warning_ends_clear_of_the_turtle_it_precedes(self):
        assert self.report().as_turtle_comments().endswith("\n\n")
