"""Map a parsed SHACL shape to an Elody dynamic-form field definition.

The output matches the `modalFormFields` object consumed by the PWA's
DynamicForm.vue: a dict keyed by property name, where each value is a
PanelMetaData field with an InputField (type, validation, options).

This is the SHACL-1.2-UI mapping layer: shape constraints (datatype,
sh:class, sh:in, sh:minCount) select form widgets and validation.
"""

import re

from apps.dishacled.shacl.parser import ShaclProperty


# ShaclProperty.input_field_type -> Elody BaseFieldType
_FIELD_TYPE_MAP = {
    "baseTextField": "baseTextField",
    "baseNumberField": "baseNumberField",
    "baseCheckbox": "baseCheckbox",
}

# input_field_types that resolve to a channel dropdown (rdfc:Reader/Writer/Channel)
_CHANNEL_FIELD_TYPES = {"hasWriterField", "channelRelationField"}


def _camel_to_kebab(name: str) -> str:
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1-\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", s1).lower()


def _dropdown_options(values: list) -> list:
    return [
        {
            "icon": "NoIcon",
            "label": str(v),
            "value": str(v),
            "__typename": "DropdownOption",
        }
        for v in values
    ]


def _build_input_field(prop: ShaclProperty, channel_options: list) -> dict:
    is_channel = prop.input_field_type in _CHANNEL_FIELD_TYPES
    is_enum = prop.input_field_type == "baseSelectField" or bool(prop.in_values)

    if is_channel:
        field_type = "dropdown"
        options = _dropdown_options(channel_options)
        # marked so the GraphQL layer can inject live channel options
    elif is_enum:
        field_type = "dropdown"
        options = _dropdown_options(prop.in_values)
    else:
        field_type = _FIELD_TYPE_MAP.get(prop.input_field_type, "baseTextField")
        options = None

    validation = None
    if prop.is_required:
        validation = {"value": ["required"], "__typename": "Validation"}

    input_field = {
        "type": field_type,
        "__typename": "InputField",
        "validation": validation,
    }
    if options is not None:
        input_field["options"] = options
    if is_channel:
        # let the GraphQL layer recognise and inject live channel options
        input_field["channelField"] = True
    return input_field


def shacl_properties_to_form_fields(
    properties: list[ShaclProperty],
    channel_options: list | None = None,
) -> dict:
    """Return a modalFormFields object for the given SHACL properties.

    channel_options: available channel names, injected as dropdown options for
    rdfc:Reader/Writer/Channel-typed properties.
    """
    channel_options = channel_options or []
    fields: dict = {}
    for prop in properties:
        fields[prop.name] = {
            "key": prop.name,
            "label": f"metadata.labels.{_camel_to_kebab(prop.name)}",
            "__typename": "PanelMetaData",
            "inputField": _build_input_field(prop, channel_options),
        }
    return fields
