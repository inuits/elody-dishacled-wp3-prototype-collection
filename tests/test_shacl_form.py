"""Tests for mapping a parsed SHACL shape to an Elody dynamic-form field
definition (the `modalFormFields` shape consumed by DynamicForm.vue).

This is the SHACL-1.2-UI layer: shape constraints -> form widgets.
"""

from apps.dishacled.shacl.parser import ShaclProperty
from apps.dishacled.shacl.form import shacl_properties_to_form_fields


def _field(fields, key):
    return next(f for f in fields.values() if isinstance(f, dict) and f.get("key") == key)


class TestWidgetMapping:
    def test_string_maps_to_text_field(self):
        props = [ShaclProperty(name="url", path="rdfc:url", datatype="xsd:string", input_field_type="baseTextField")]
        fields = shacl_properties_to_form_fields(props)
        f = _field(fields, "url")
        assert f["__typename"] == "PanelMetaData"
        assert f["inputField"]["type"] == "baseTextField"
        assert f["inputField"]["__typename"] == "InputField"

    def test_integer_maps_to_number_field(self):
        props = [ShaclProperty(name="pollInterval", path="rdfc:pollInterval", datatype="xsd:integer", input_field_type="baseNumberField")]
        fields = shacl_properties_to_form_fields(props)
        assert _field(fields, "pollInterval")["inputField"]["type"] == "baseNumberField"

    def test_boolean_maps_to_checkbox(self):
        props = [ShaclProperty(name="materialize", path="rdfc:materialize", datatype="xsd:boolean", input_field_type="baseCheckbox")]
        fields = shacl_properties_to_form_fields(props)
        assert _field(fields, "materialize")["inputField"]["type"] == "baseCheckbox"

    def test_sh_in_maps_to_dropdown_with_options(self):
        props = [ShaclProperty(name="level", path="rdfc:level", in_values=["info", "warn", "error"], input_field_type="baseSelectField")]
        fields = shacl_properties_to_form_fields(props)
        f = _field(fields, "level")
        assert f["inputField"]["type"] == "dropdown"
        opts = f["inputField"]["options"]
        assert [o["value"] for o in opts] == ["info", "warn", "error"]
        assert all(o["__typename"] == "DropdownOption" for o in opts)


class TestChannelFields:
    def test_writer_channel_maps_to_dropdown_with_channel_options(self):
        props = [ShaclProperty(name="writer", path="rdfc:writer", class_ref="rdfc:Writer", input_field_type="hasWriterField")]
        fields = shacl_properties_to_form_fields(props, channel_options=["json", "rdf"])
        f = _field(fields, "writer")
        assert f["inputField"]["type"] == "dropdown"
        assert [o["value"] for o in f["inputField"]["options"]] == ["json", "rdf"]

    def test_reader_channel_maps_to_dropdown(self):
        props = [ShaclProperty(name="reader", path="rdfc:reader", class_ref="rdfc:Reader", input_field_type="channelRelationField")]
        fields = shacl_properties_to_form_fields(props, channel_options=["json"])
        assert _field(fields, "reader")["inputField"]["type"] == "dropdown"

    def test_channel_field_without_options_is_empty_dropdown(self):
        props = [ShaclProperty(name="writer", path="rdfc:writer", class_ref="rdfc:Writer", input_field_type="hasWriterField")]
        fields = shacl_properties_to_form_fields(props)
        assert _field(fields, "writer")["inputField"]["options"] == []

    def test_channel_field_is_marked_for_graphql_injection(self):
        props = [ShaclProperty(name="writer", path="rdfc:writer", class_ref="rdfc:Writer", input_field_type="hasWriterField")]
        fields = shacl_properties_to_form_fields(props)
        assert _field(fields, "writer")["inputField"]["channelField"] is True

    def test_non_channel_field_is_not_marked(self):
        props = [ShaclProperty(name="url", path="rdfc:url", datatype="xsd:string", input_field_type="baseTextField")]
        fields = shacl_properties_to_form_fields(props)
        assert "channelField" not in _field(fields, "url")["inputField"]


class TestValidation:
    def test_required_field_gets_required_validation(self):
        props = [ShaclProperty(name="url", path="rdfc:url", datatype="xsd:string", min_count=1, input_field_type="baseTextField")]
        fields = shacl_properties_to_form_fields(props)
        validation = _field(fields, "url")["inputField"]["validation"]
        assert validation is not None
        assert "required" in validation["value"]

    def test_optional_field_has_no_validation(self):
        props = [ShaclProperty(name="level", path="rdfc:level", datatype="xsd:string", min_count=0, input_field_type="baseTextField")]
        fields = shacl_properties_to_form_fields(props)
        assert _field(fields, "level")["inputField"]["validation"] is None


class TestLabelsAndKeys:
    def test_key_is_property_name(self):
        props = [ShaclProperty(name="pollInterval", path="rdfc:pollInterval", datatype="xsd:integer", input_field_type="baseNumberField")]
        fields = shacl_properties_to_form_fields(props)
        assert _field(fields, "pollInterval")["key"] == "pollInterval"

    def test_label_is_translation_key_from_kebab_name(self):
        props = [ShaclProperty(name="pollInterval", path="rdfc:pollInterval", datatype="xsd:integer", input_field_type="baseNumberField")]
        fields = shacl_properties_to_form_fields(props)
        assert _field(fields, "pollInterval")["label"] == "metadata.labels.poll-interval"


class TestFormStructure:
    def test_fields_keyed_by_name_for_modal_form_fields(self):
        props = [
            ShaclProperty(name="url", path="rdfc:url", datatype="xsd:string", min_count=1, input_field_type="baseTextField"),
            ShaclProperty(name="writer", path="rdfc:writer", class_ref="rdfc:Writer", input_field_type="hasWriterField"),
        ]
        fields = shacl_properties_to_form_fields(props, channel_options=["json"])
        # modalFormFields is an object whose values are field dicts
        assert "url" in fields and "writer" in fields
        assert fields["url"]["key"] == "url"

    def test_empty_properties_yields_empty_fields(self):
        assert shacl_properties_to_form_fields([]) == {}

    def test_ordering_preserved(self):
        props = [
            ShaclProperty(name="a", path="rdfc:a", datatype="xsd:string", input_field_type="baseTextField"),
            ShaclProperty(name="b", path="rdfc:b", datatype="xsd:string", input_field_type="baseTextField"),
            ShaclProperty(name="c", path="rdfc:c", datatype="xsd:string", input_field_type="baseTextField"),
        ]
        fields = shacl_properties_to_form_fields(props)
        assert list(fields.keys()) == ["a", "b", "c"]
