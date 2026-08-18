"""Deriving the alert view from `lblodsh:ErrorShape`.

A2 renders alerts from the shape rather than from a hand-written per-field form.
That only holds if one shape drives *both* halves: the metadata keys the
serializer emits and the form fields the UI renders. These tests pin that.

The shape itself is copied verbatim from the threshold-monitor processor and a
gated test asserts it stays isomorphic to the published file, so it cannot be
edited to suit us -- notably it carries no `sh:order`, which is why the display
order lives here instead. See docs/alert-rendering.md.
"""

import pytest

from apps.dishacled.shacl.alert_shape import (
    ALERT_FIELD_ORDER,
    alert_field_names,
    alert_form_fields,
    error_shape_ttl,
)
from apps.dishacled.serializers.alert_serializer import AlertSerializer

OSLC = "http://open-services.net/ns/core#"
MU = "http://mu.semte.ch/vocabularies/core/"
DCT = "http://purl.org/dc/terms/"


class TestTheShapeItself:
    def test_the_error_shape_is_found_in_the_catalog(self):
        ttl = error_shape_ttl()
        assert "ErrorShape" in ttl
        assert "oslc:Error" in ttl or "open-services.net/ns/core#Error" in ttl

    def test_the_shape_keeps_its_own_namespaces(self):
        # Not rdfc: -- the alert vocabulary is oslc/dct/mu.
        ttl = error_shape_ttl()
        assert "open-services.net/ns/core#" in ttl
        assert "purl.org/dc/terms/" in ttl
        assert "mu.semte.ch/vocabularies/core/" in ttl


class TestFieldNames:
    """predicate -> the `sh:name` the shape gives it."""

    def test_every_shape_property_is_mapped(self):
        assert alert_field_names() == {
            f"{MU}uuid": "uuid",
            f"{DCT}subject": "subject",
            f"{OSLC}message": "message",
            f"{DCT}created": "created",
            f"{DCT}creator": "creator",
            f"{OSLC}largePreview": "detail",
            f"{DCT}references": "references",
        }

    def test_the_two_renamed_properties_follow_the_shape(self):
        # These are the ones where the predicate's local name and the field name
        # differ, so they are what a hand-maintained mapping would get wrong.
        names = alert_field_names()
        assert names[f"{OSLC}largePreview"] == "detail"
        assert names[f"{DCT}references"] == "references"


class TestFormFields:
    def test_it_covers_every_property_in_the_shape(self):
        fields = alert_form_fields()
        assert set(fields) == set(alert_field_names().values())

    def test_the_fields_are_in_the_declared_display_order(self):
        # The shape carries no sh:order and RDF property sets are unordered, so
        # without this the order is whatever rdflib happens to yield.
        assert list(alert_form_fields()) == list(ALERT_FIELD_ORDER)

    def test_the_order_is_stable_across_calls(self):
        assert list(alert_form_fields()) == list(alert_form_fields())

    def test_each_field_is_shaped_the_way_the_pwa_expects(self):
        # getMetadataFields in the PWA picks entries out by __typename.
        for key, field in alert_form_fields().items():
            assert field["__typename"] == "PanelMetaData"
            assert field["key"] == key
            assert field["label"]
            assert "inputField" in field

    def test_the_timestamp_is_not_rendered_as_a_plain_string(self):
        assert alert_form_fields()["created"]["inputField"]["type"] == "date"

    def test_labels_are_human_readable_not_translation_keys(self):
        labels = [f["label"] for f in alert_form_fields().values()]
        assert all(not label.startswith("metadata.labels.") for label in labels)


class TestTheSerializerAndTheViewAgree:
    """The invariant that makes the view shape-driven rather than coincidental.

    If these two drift, the UI renders a field the data never fills, or the data
    carries a field the UI never shows -- silently, in both directions.
    """

    def test_the_serializer_emits_exactly_the_fields_the_shape_declares(self):
        subject = {
            "iri": "https://dishacled.github.io/demo/alerts/x",
            "properties": {
                predicate: [f"value for {name}"]
                for predicate, name in alert_field_names().items()
            },
        }
        entity = AlertSerializer().from_sparql_to_elody(subject)
        emitted = {item["key"] for item in entity["metadata"]}
        assert emitted == set(alert_form_fields())

    def test_the_serializer_reads_the_shape_rather_than_a_private_copy(self):
        # A hardcoded mapping would not follow a change to the shape.
        assert AlertSerializer().field_names() == alert_field_names()
