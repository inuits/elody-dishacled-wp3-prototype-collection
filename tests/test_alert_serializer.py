"""Mapping an `oslc:Error` resource onto an Elody entity.

The SPARQL storage engine knows nothing about alerts: it groups whatever the
endpoint returns by subject and hands each one here as

    {"iri": ..., "properties": {predicate: [value, ...]}}

so this module is where the alert vocabulary is interpreted. The predicates are
the ones `lblodsh:ErrorShape` declares in the contract catalog -- see
docs/alert-fixture.md for the shape and its provenance.
"""

import pytest

from apps.dishacled.serializers.alert_serializer import AlertSerializer

OSLC = "http://open-services.net/ns/core#"
MU = "http://mu.semte.ch/vocabularies/core/"
DCT = "http://purl.org/dc/terms/"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"

UUID = "2f8c1d94-5a3b-4e7f-9c21-6b0d8e4a1f37"
IRI = f"https://dishacled.github.io/demo/alerts/{UUID}"
MEMBER = "https://dishacled.github.io/demo/members/m-0417"
AGENT = "https://dishacled.github.io/demo/agents#threshold-monitor"


def subject(**overrides):
    """A complete alert, as the engine hands it over."""
    properties = {
        RDF_TYPE: [f"{OSLC}Error"],
        f"{MU}uuid": [UUID],
        f"{DCT}subject": ["threshold-monitor"],
        f"{OSLC}message": ["Measurement above maximum threshold"],
        f"{DCT}created": ["2026-07-15T09:21:45.725000+00:00"],
        f"{DCT}creator": [AGENT],
        f"{OSLC}largePreview": [f"Member <{MEMBER}> has value 412.5 cm, above 300 cm."],
        f"{DCT}references": [MEMBER],
    }
    properties.update(overrides)
    return {"iri": IRI, "properties": properties}


def metadata(entity):
    return {item["key"]: item["value"] for item in entity["metadata"]}


@pytest.fixture
def serializer():
    return AlertSerializer()


class TestFromSparqlToElody:
    def test_the_uuid_becomes_the_identity(self, serializer):
        entity = serializer.from_sparql_to_elody(subject())
        assert entity["_id"] == UUID
        # The IRI is an identifier too, so an alert can also be resolved by the
        # address the pipeline knows it under.
        assert entity["identifiers"] == [UUID, IRI]

    def test_it_is_typed_as_an_alert(self, serializer):
        assert serializer.from_sparql_to_elody(subject())["type"] == "alert"

    def test_every_shape_property_reaches_the_metadata(self, serializer):
        values = metadata(serializer.from_sparql_to_elody(subject()))
        assert values["subject"] == "threshold-monitor"
        assert values["message"] == "Measurement above maximum threshold"
        assert values["created"] == "2026-07-15T09:21:45.725000+00:00"
        assert values["creator"] == AGENT
        # "references", not "reference": the key is the shape's own sh:name.
        assert values["references"] == MEMBER
        assert values["detail"].startswith("Member ")
        assert values["uuid"] == UUID

    def test_the_detail_is_kept_as_one_raw_string(self, serializer):
        # The processor emits member, value and bound as a single sentence.
        # Splitting it is a change we want upstream, not a guess made here.
        preview = "Member <x> has value 412.5 cm, above the configured maximum of 300 cm."
        entity = serializer.from_sparql_to_elody(
            subject(**{f"{OSLC}largePreview": [preview]})
        )
        assert metadata(entity)["detail"] == preview

    def test_absent_optional_properties_are_omitted_not_blanked(self, serializer):
        # Alert 4 of the fixture has neither. A consumer must be able to tell
        # "no detail" from "empty detail".
        bare = subject()
        del bare["properties"][f"{OSLC}largePreview"]
        del bare["properties"][f"{DCT}references"]
        values = metadata(serializer.from_sparql_to_elody(bare))
        assert "detail" not in values
        assert "references" not in values
        assert values["message"] == "Measurement above maximum threshold"

    def test_unknown_predicates_are_ignored(self, serializer):
        entity = serializer.from_sparql_to_elody(
            subject(**{"http://example.org/nonsense": ["boom"]})
        )
        assert "boom" not in str(metadata(entity).values())

    def test_a_repeated_property_keeps_the_first_value(self, serializer):
        # The shape says maxCount 1; if an endpoint disagrees, pick one rather
        # than emit a list into a field the UI renders as a string.
        entity = serializer.from_sparql_to_elody(
            subject(**{f"{OSLC}message": ["first", "second"]})
        )
        assert metadata(entity)["message"] == "first"

    def test_relations_are_empty(self, serializer):
        # dct:references points at an SDS member Elody does not hold, so it is
        # metadata rather than a relation to a local entity.
        assert serializer.from_sparql_to_elody(subject())["relations"] == []

    def test_a_subject_without_a_uuid_is_refused(self, serializer):
        anonymous = subject()
        del anonymous["properties"][f"{MU}uuid"]
        assert serializer.from_sparql_to_elody(anonymous) == {}

    def test_it_tolerates_the_serializer_kwargs_the_framework_passes(self, serializer):
        entity = serializer.from_sparql_to_elody(
            subject(),
            document_type="alerts",
            original_document=None,
            accept_header="application/json",
        )
        assert entity["_id"] == UUID


class TestFromElodyFilterToSparqlFilter:
    def test_a_type_filter_alone_places_no_restriction(self, serializer):
        assert serializer.from_elody_filter_to_sparql_filter(
            [{"type": "type", "value": "alert"}]
        ) == {}

    def test_an_identifiers_selection_restricts_to_those_ids(self, serializer):
        filters = serializer.from_elody_filter_to_sparql_filter(
            [
                {"type": "type", "value": "alert"},
                {"type": "selection", "key": ["identifiers"], "value": [UUID, "other"]},
            ]
        )
        assert filters == {"ids": [UUID, "other"]}

    def test_a_single_identifier_is_wrapped(self, serializer):
        filters = serializer.from_elody_filter_to_sparql_filter(
            [{"type": "selection", "key": "identifiers", "value": UUID}]
        )
        assert filters == {"ids": [UUID]}

    def test_an_empty_selection_means_no_matches_not_no_restriction(self, serializer):
        # Same rule as the github filter serializer: the key is always set once
        # a selection is present, so an empty list cannot widen the result.
        filters = serializer.from_elody_filter_to_sparql_filter(
            [{"type": "selection", "key": ["identifiers"], "value": None}]
        )
        assert filters == {"ids": []}
