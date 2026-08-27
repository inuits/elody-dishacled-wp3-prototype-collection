"""Map a parsed SHACL shape to an Elody dynamic-form field definition.

The output matches the `modalFormFields` object consumed by the PWA's
DynamicForm.vue: a dict keyed by property name, where each value is a
PanelMetaData field with an InputField (type, validation, options).

This is the SHACL-1.2-UI mapping layer: shape constraints (datatype,
sh:class, sh:in, sh:minCount) select form widgets and validation.
"""

import re
from copy import deepcopy
from functools import lru_cache

from apps.dishacled.shacl.parser import ShaclProperty
from apps.dishacled.shacl.shui import ShuiFormBuilder, UiNode


# shui:Editor (local name) -> Elody InputFieldTypes enum value.
# This is the SHACL 1.2 UI -> Elody widget mapping. DetailsEditor maps to
# Elody's recursive inputFieldWithSubFields (its native nested-form widget).
_EDITOR_TO_ELODY = {
    "TextFieldEditor": "text",
    "NumberFieldEditor": "number",
    "BooleanEditor": "checkbox",
    "DatePickerEditor": "date",
    "DateTimePickerEditor": "date",
    "EnumSelectEditor": "dropdown",
    "InstancesSelectEditor": "dropdown",
    "AutoCompleteEditor": "dropdown",
    "DetailsEditor": "inputFieldWithSubFields",
}


# ShaclProperty.input_field_type -> Elody InputFieldTypes enum value.
# These must be the literal enum values the PWA expects (InputFieldTypes in
# generated-types), because EntityElementMetadataEdit passes the type straight
# to the <input type="..."> attribute. An unknown value (e.g. "baseTextField")
# renders as text but misses the @tailwindcss/forms base padding, collapsing
# the input to line-height. Use the real HTML input types.
_FIELD_TYPE_MAP = {
    "baseTextField": "text",
    "baseNumberField": "number",
    "baseCheckbox": "checkbox",
}

# input_field_types that resolve to a channel dropdown (rdfc:Reader/Writer/Channel)
_CHANNEL_FIELD_TYPES = {"hasWriterField", "channelRelationField"}


# One entry per (document, channel list, class) asked about. The channel list is
# what makes this bigger than the number of documents in play: the same form is
# derived once with the live channels and once without.
_FORM_CACHE_SIZE = 512


# Words rendered in uppercase when humanizing parameter names into labels.
_ACRONYMS = {"url", "iri", "id", "db", "api", "http", "mime"}


def _humanize(name: str) -> str:
    """Turn a camelCase parameter name into a human-readable label.

    Labels are emitted as plain text (not translation keys) so every
    SHACL-described processor gets readable field labels without requiring
    per-processor translation maintenance. vue-i18n's t() passes unknown
    plain-text keys through unchanged.
    """
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1 \2", name)
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s1).split()
    out = []
    for i, word in enumerate(words):
        lower = word.lower()
        if lower in _ACRONYMS:
            out.append(word.upper())
        elif i == 0:
            out.append(word.capitalize())
        else:
            out.append(lower)
    return " ".join(out)


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
        field_type = _FIELD_TYPE_MAP.get(prop.input_field_type, "text")
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


def _ui_node_to_input_field(
    node: UiNode, channel_options: list, full_key: str
) -> dict:
    """Map a SHACL 1.2 UI node to an Elody InputField (recursive for nesting).

    `full_key` is this node's dotted path (e.g. "options" or "options.auth").
    Nested children get dotted keys (`options.method`) so they bind to a nested
    vee-validate path and round-trip to flat dotted relation metadata.
    """
    elody_type = _EDITOR_TO_ELODY.get(node.editor, "text")

    validation = None
    if node.is_required:
        validation = {"value": ["required"], "__typename": "Validation"}

    input_field = {
        "type": elody_type,
        "__typename": "InputField",
        "validation": validation,
    }

    if node.editor == "EnumSelectEditor":
        input_field["options"] = _dropdown_options(node.in_values)
    elif node.editor == "InstancesSelectEditor":
        input_field["options"] = _dropdown_options(channel_options)
        # let the GraphQL layer inject live channel options
        input_field["channelField"] = True
    elif node.editor == "DetailsEditor":
        # marker so the PWA renders a nested sub-form (shui:DetailsEditor)
        # instead of the default inputFieldWithSubFields table widget
        input_field["isDetailsEditor"] = True
        input_field["subFields"] = [
            {
                "__typename": "SubField",
                "key": f"{full_key}.{child.name}",
                "label": _humanize(child.name),
                "inputField": _ui_node_to_input_field(
                    child, channel_options, f"{full_key}.{child.name}"
                ),
            }
            for child in node.children
        ]

    return input_field


def shacl_to_form_fields(
    ttl_string: str,
    channel_options: list | None = None,
    target_class=None,
) -> dict:
    """Derive Elody nested form fields from a processor's SHACL via SHACL 1.2 UI.

    SHACL -> SHACL 1.2 UI tree (ShuiFormBuilder) -> Elody modalFormFields. The
    main processor shape's properties become top-level fields; nested node
    shapes become inputFieldWithSubFields (shui:DetailsEditor).

    Cached on its arguments, because a listing derives the same form once per
    processor of a repository and again on every request, and building it means
    walking the shape graph. A deep copy is handed out: the result goes onto a
    component document that serializers and the export then add to.
    """
    return deepcopy(
        _form_fields(ttl_string, tuple(channel_options or []), target_class)
    )


@lru_cache(maxsize=_FORM_CACHE_SIZE)
def _form_fields(
    ttl_string: str,
    channel_options: tuple,
    target_class=None,
) -> dict:
    channel_options = list(channel_options)
    root = ShuiFormBuilder(ttl_string, target_class).build_tree()
    ordered = sorted(
        root.children,
        key=lambda n: n.order if n.order is not None else float("inf"),
    )
    fields: dict = {}
    for node in ordered:
        fields[node.name] = {
            "key": node.name,
            "label": _humanize(node.name),
            "__typename": "PanelMetaData",
            "inputField": _ui_node_to_input_field(
                node, channel_options, node.name
            ),
        }
    return fields


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
            "label": _humanize(prop.name),
            "__typename": "PanelMetaData",
            "inputField": _build_input_field(prop, channel_options),
        }
    return fields
