import re
from dataclasses import dataclass, field
from apps.dishacled.shacl.parser import ShaclProperty


def _camel_to_kebab(name: str) -> str:
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1-\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", s1).lower()


def _camel_to_snake(name: str) -> str:
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def _camel_to_human(name: str) -> str:
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1 \2", name)
    result = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s1).lower()
    return result[0].upper() + result[1:]


def _kebab_to_label(name: str) -> str:
    return name.replace("-", " ").capitalize()


@dataclass
class CodegenConfig:
    type_name: str  # camelCase, e.g. "ldesClientProcessor"
    runner_type: str  # e.g. "jsRunner"
    pill_label: str  # e.g. "LDES"

    @property
    def pascal_name(self) -> str:
        return self.type_name[0].upper() + self.type_name[1:]

    @property
    def snake_name(self) -> str:
        return _camel_to_snake(self.type_name)

    @property
    def kebab_name(self) -> str:
        return _camel_to_kebab(self.type_name)

    @property
    def human_name(self) -> str:
        return _camel_to_human(self.type_name)


class CodeGenerator:
    def __init__(self, config: CodegenConfig, properties: list[ShaclProperty]):
        self.config = config
        self.properties = properties

    def generate_all(self) -> dict:
        return {
            f"{self.config.type_name}.queries.ts": self.generate_queries(),
            f"{self.config.snake_name}_configuration.py": self.generate_python_config(),
            "patches": {
                "dishacledSchema.schema.ts": self.generate_schema_patch(),
                "dishacledResolver.ts": self.generate_resolver_patch(),
                "dishacled.queries.ts": self.generate_dishacled_queries_patch(),
                "processor.queries.ts": self.generate_processor_queries_patch(),
                "typePillLabelMapping.ts": self.generate_pill_label_entry(),
                "dishacledPermissions.ts": self.generate_permission_entry(),
                "en.json": self.generate_translations(),
                "mappers.py": self.generate_mappers_patch(),
            },
        }

    def generate_queries(self) -> str:
        c = self.config
        parts = [
            f'import {{ gql }} from "graphql-modules";',
            "",
            f"export const {c.type_name}Queries = gql`",
            self._gen_minimal_fragment(),
            "",
            self._gen_full_fragment(),
            "",
            self._gen_sort_options_fragment(),
            "",
            self._gen_filters_fragment(),
            "",
            self._gen_bulk_operations_fragment(),
            "",
            self._gen_create_form_query(),
            "",
            self._gen_runner_bulk_operations_query(),
            "",
            self._gen_filter_query(),
            "`;",
        ]
        return "\n".join(parts) + "\n"

    def _gen_minimal_fragment(self) -> str:
        c = self.config
        return f"""  fragment minimal{c.pascal_name} on {c.pascal_name} {{
    intialValues {{
      ...typePillsIntialValues
      name: keyValue(key: "name", source: metadata)
      isProcessorFor: keyValue(
        key: "isProcessorFor"
        source: relations
        metadataKeyAsLabel: "name"
        formatter: "pill"
      )
    }}
    relationValues
    allowedViewModes {{
      viewModes(
        input: [{{ viewMode: ViewModesList }}, {{ viewMode: ViewModesGrid }}]
      ) {{
        ...viewModes
      }}
    }}
    teaserMetadata {{
      ...typePillsTeaserMetadata
      name: metaData {{
        label(input: "metadata.labels.name")
        key(input: "name")
      }}
      isProcessorFor: metaData {{
        label(input: "metadata.labels.runner")
        key(input: "isProcessorFor")
      }}
    }}
    ...minimalBaseEntity
  }}"""

    def _gen_full_fragment(self) -> str:
        c = self.config
        initial_values = self._gen_initial_values()
        panels = self._gen_panels()
        runner_types = [c.runner_type]

        return f"""  fragment full{c.pascal_name} on {c.pascal_name} {{
    intialValues {{
      name: keyValue(key: "name", source: metadata)
{initial_values}
    }}
    relationValues
    entityView {{
      column {{
        size(size: seventy)
        elements {{
          runners: entityListElement {{
            label(input: "element-labels.runner-element")
            isCollapsed(input: false)
            entityTypes(input: [{c.runner_type}])
            relationType: label(input: "hasRunner")
            customQuery(input: "GetEntities")
            customQueryFilters(input: "GetRunnerRelatedToProcessorFilter")
            searchInputType(input: "AdvancedInputType")
            customBulkOperations(
              input: "GetRunnerOn{c.pascal_name}BulkOperations"
            )
          }}
        }}
      }}
      column2: column {{
        size(size: thirty)
        elements {{
          windowElement {{
            label(input: "window-element-labels.info-window")
            expandButtonOptions {{
              shown(input: true)
            }}
{panels}
          }}
        }}
      }}
    }}
  }}"""

    def _gen_initial_values(self) -> str:
        lines = []
        for prop in self.properties:
            if prop.input_field_type == "hasWriterField":
                lines.append(
                    f'      {prop.name}: keyValue(key: "{prop.name}", source: relations, metadataKeyAsLabel: "name", relationEntityType: "writer")'
                )
            elif prop.input_field_type == "channelRelationField":
                lines.append(
                    f'      {prop.name}: keyValue(key: "{prop.name}", source: relations, metadataKeyAsLabel: "name", relationEntityType: "channel")'
                )
            else:
                lines.append(
                    f'      {prop.name}: keyValue(key: "{prop.name}", source: metadata)'
                )
        return "\n".join(lines)

    def _gen_panels(self) -> str:
        panel_lines = []
        if self.properties:
            panel_lines.append(f"            properties: panels {{")
            panel_lines.append(
                f'              label(input: "panel-labels.{self.config.kebab_name}-properties")'
            )
            panel_lines.append(f"              panelType(input: metadata)")
            panel_lines.append(f"              isCollapsed(input: false)")
            panel_lines.append(f"              isEditable(input: true)")

            for prop in self.properties:
                panel_lines.append(self._gen_panel_field(prop))

            panel_lines.append(f"            }}")

        return "\n".join(panel_lines)

    def _gen_panel_field(self, prop: ShaclProperty) -> str:
        kebab_name = _camel_to_kebab(prop.name)
        lines = [
            f'              {prop.name}: metaData {{',
            f'                label(input: "metadata.labels.{kebab_name}")',
            f'                key(input: "{prop.name}")',
        ]

        if prop.input_field_type == "hasWriterField":
            lines.append(f"                inputField(type: hasWriterField){{")
            lines.append(f"                  ...inputfield")
            lines.append(f"                }}")
        elif prop.input_field_type == "channelRelationField":
            lines.append(f"                inputField(type: dropdown){{")
            lines.append(f"                  ...inputfield")
            lines.append(f"                }}")
        elif prop.input_field_type != "baseTextField" or prop.is_required:
            lines.append(
                f"                inputField(type: {prop.input_field_type}) {{"
            )
            lines.append(f"                  ...inputfield")
            if prop.is_required:
                lines.append(
                    f"                  validation(input: {{ value: required }}) {{"
                )
                lines.append(f"                    ...validation")
                lines.append(f"                  }}")
            lines.append(f"                }}")
        else:
            lines.append(f"                inputField(type: baseTextField) {{")
            lines.append(f"                  ...inputfield")
            lines.append(f"                }}")

        lines.append(f"              }}")
        return "\n".join(lines)

    def _gen_sort_options_fragment(self) -> str:
        c = self.config
        return f"""  fragment {c.type_name}SortOptions on {c.pascal_name} {{
    sortOptions {{
      options(
        input: [{{ icon: NoIcon, label: "metadata.labels.name", value: "name" }}]
      ) {{
        icon
        label
        value
      }}
    }}
  }}"""

    def _gen_filters_fragment(self) -> str:
        c = self.config
        return f"""  fragment filtersFor{c.pascal_name} on {c.pascal_name} {{
    advancedFilters {{
      type: advancedFilter(type: type) {{
        type
        defaultValue(value: "{c.type_name}")
        hidden(value: true)
      }}
      name: advancedFilter(
        type: text
        key: ["elody:1|metadata.name.value"]
        label: "metadata.labels.name"
        isDisplayedByDefault: true
      ) {{
        type
        key
        label
        isDisplayedByDefault
        tooltip(value: true)
      }}
    }}
  }}"""

    def _gen_bulk_operations_fragment(self) -> str:
        c = self.config
        return f"""  fragment {c.type_name}BulkOperations on {c.pascal_name} {{
    bulkOperationOptions {{
      options(
        input: [
          {{
            icon: Create
            label: "bulk-operations.create-{c.kebab_name}"
            value: "createEntity"
            primary: true
            actionContext: {{
              activeViewMode: readMode
              entitiesSelectionType: noneSelected
              labelForTooltip: "tooltip.bulkOperationsActionBar.readmode-noneselected"
            }}
            bulkOperationModal: {{
              typeModal: DynamicForm
              formQuery: "Get{c.pascal_name}CreateForm"
              formRelationType: "is{c.pascal_name}For"
              askForCloseConfirmation: true
              neededPermission: cancreate
            }}
          }}
        ]
      ) {{
        icon
        label
        value
        primary
        can
        actionContext {{
          ...actionContext
        }}
        bulkOperationModal {{
          ...bulkOperationModal
        }}
      }}
    }}
  }}"""

    def _gen_create_form_query(self) -> str:
        c = self.config
        return f"""  query Get{c.pascal_name}CreateForm {{
    GetDynamicForm {{
      label(input: "navigation.create-{c.kebab_name}")
      name: formTab {{
        formFields {{
          name: metaData {{
            label(input: "metadata.labels.name")
            key(input: "name")
            inputField(type: baseTextField) {{
              ...inputfield
              validation(input: {{ value: required }}) {{
                ...validation
              }}
            }}
          }}
          createAction: action {{
            label(input: "actions.labels.create")
            icon(input: Create)
            actionType(input: submit)
            actionQuery(input: "CreateEntity")
            creationType(input: {c.type_name})
            showsFormErrors(input: true)
          }}
        }}
      }}
    }}
  }}"""

    def _gen_runner_bulk_operations_query(self) -> str:
        c = self.config
        runner_pascal = c.runner_type[0].upper() + c.runner_type[1:]
        return f"""  query GetRunnerOn{c.pascal_name}BulkOperations {{
    CustomBulkOperations {{
      bulkOperationOptions {{
        options(
          input: [
            {{
              icon: PlusCircle
              label: "bulk-operations.create-{_camel_to_kebab(c.runner_type).replace('-', '')}"
              value: "createEntity"
              can: ["update:{c.type_name}:has-runner"]
              actionContext: {{
                activeViewMode: readMode
                entitiesSelectionType: noneSelected
                labelForTooltip: "tooltip.bulkOperationsActionBar.readmode-noneselected"
              }}
              bulkOperationModal: {{
                typeModal: DynamicForm
                formQuery: "Get{runner_pascal}CreateForm"
                formRelationType: "isRunnerFor"
                askForCloseConfirmation: true
                neededPermission: cancreate
              }}
            }}
            {{
              icon: PlusCircle
              label: "bulk-operations.existing-runner"
              value: "addRelation"
              can: ["update:{c.type_name}:has-runner"]
              actionContext: {{
                activeViewMode: readMode
                entitiesSelectionType: noneSelected
                labelForTooltip: "tooltip.bulkOperationsActionBar.readmode-noneselected"
              }}
              bulkOperationModal: {{
                typeModal: DynamicForm
                formQuery: "GetEntityPickerForm"
                askForCloseConfirmation: true
                neededPermission: canupdate
              }}
            }}
            {{
              label: "bulk-operations.delete-selected"
              value: "deleteEntities"
              primary: false
              can: ["update:{c.type_name}:has-runner"]
              bulkOperationModal: {{
                typeModal: BulkOperationsDeleteEntities
                formQuery: "GetBulkRemovingMediafilesInDetailForm"
                askForCloseConfirmation: false
              }}
              actionContext: {{
                activeViewMode: readMode
                entitiesSelectionType: someSelected
                labelForTooltip: "tooltip.bulkOperationsActionBar.readmode-someselected"
              }}
            }}
          ]
        ) {{
          icon
          label
          value
          primary
          can
          actionContext {{
            ...actionContext
          }}
          bulkOperationModal {{
            ...bulkOperationModal
          }}
        }}
      }}
    }}
  }}"""

    def _gen_filter_query(self) -> str:
        c = self.config
        return f"""  query Get{c.pascal_name}Filter($entityType: String!) {{
    EntityTypeFilters(type: $entityType) {{
      advancedFilters {{
        type: advancedFilter(type: type) {{
          type
          defaultValue(value: "{c.type_name}")
          hidden(value: true)
        }}
        relation: advancedFilter(
          type: selection
          key: ["elody:1|identifiers"]
        ) {{
          type
          key
          defaultValue(value: "$entity.relationValues.hasProcessor.key")
          hidden(value: true)
        }}
      }}
    }}
  }}"""

    def generate_schema_patch(self) -> dict:
        c = self.config
        type_def = f"""  type {c.pascal_name} implements Entity {{
    id: String!
    uuid: String!
    type: String!
    teaserMetadata: teaserMetadata
    intialValues: IntialValues!
    allowedViewModes: AllowedViewModes
    relationValues: JSON
    entityView: ColumnList!
    advancedFilters: AdvancedFilters
    sortOptions: SortOptions
    bulkOperationOptions: BulkOperationOptions
    previewComponent: PreviewComponent
    deleteQueryOptions: DeleteQueryOptions
    mapElement: MapElement
  }}"""
        return {
            "enum_entry": f"    {c.type_name}",
            "type_definition": type_def,
        }

    def generate_resolver_patch(self) -> dict:
        c = self.config
        return {
            "resolve_type": f'      else if (type === "{c.type_name.lower()}") return "{c.pascal_name}";',
            "resolver_entry": f"  {c.pascal_name}: {{\n    ...baseSetOffResolvers,\n  }},",
        }

    def generate_dishacled_queries_patch(self) -> dict:
        c = self.config
        return {
            "full_entity_spread": f"    ... on {c.pascal_name} {{\n      ...full{c.pascal_name}\n    }}",
            "get_entities_spread": f"        ... on {c.pascal_name} {{\n          ...minimal{c.pascal_name}\n        }}",
            "get_advanced_filters_spread": f"      ... on {c.pascal_name} {{\n        ...filtersFor{c.pascal_name}\n      }}",
        }

    def generate_processor_queries_patch(self) -> dict:
        c = self.config
        return {
            "get_all_processor_entities_spread": f"        ... on {c.pascal_name} {{\n          ...minimal{c.pascal_name}\n        }}",
            "get_all_processor_filters_default_value": c.type_name,
            "get_all_processor_bulk_operations": {
                "icon": "Create",
                "label": f"bulk-operations.create-{c.kebab_name}",
                "formQuery": f"Get{c.pascal_name}CreateForm",
                "formRelationType": "isProcessorFor",
            },
            "get_related_processor_filter_value": c.type_name,
            "pipeline_bulk_operations_entry": {
                "icon": "PlusCircle",
                "label": f"bulk-operations.create-{c.kebab_name}",
                "formQuery": f"Get{c.pascal_name}CreateForm",
                "formRelationType": "isProcessorFor",
                "can": "update:pipeline:has-processor",
            },
            "pipeline_entity_picker_list_spread": f"        ... on {c.pascal_name} {{\n          ...minimal{c.pascal_name}\n        }}",
            "pipeline_entity_picker_filters_value": c.type_name,
        }

    def generate_pill_label_entry(self) -> tuple:
        return (self.config.type_name, [self.config.pill_label])

    def generate_permission_entry(self) -> dict:
        c = self.config
        return {
            f"update:{c.type_name}:has-runner": {
                "datasource": "CollectionAPI",
                "crud": "patch",
                "uri": "/entities/$parentEntityId",
                "body": {
                    "relations": [{"key": "", "type": "hasRunner"}],
                    "type": c.type_name,
                },
            }
        }

    def generate_translations(self) -> dict:
        c = self.config
        translations = {
            "bulk-operations": {
                f"create-{c.kebab_name}": f"Create {c.human_name.lower()}",
            },
            "entity-translations": {
                "plural": {
                    c.type_name: f"{c.human_name}s",
                }
            },
            "metadata": {
                "labels": {},
            },
            "navigation": {
                f"create-{c.kebab_name}": f"Create {c.human_name.lower()}",
            },
            "panel-labels": {
                f"{c.kebab_name}-properties": f"{c.human_name} properties",
            },
        }

        for prop in self.properties:
            kebab = _camel_to_kebab(prop.name)
            translations["metadata"]["labels"][kebab] = _kebab_to_label(kebab)

        return translations

    def generate_python_config(self) -> str:
        c = self.config
        class_name = f"{c.pascal_name}Configuration"
        return f'''from elody.object_configurations.elody_configuration import (
    ElodyConfiguration,
)


class {class_name}(ElodyConfiguration):
    SCHEMA_TYPE = "elody"
    SCHEMA_VERSION = 1

    def crud(self):
        crud = {{
            "collection": "entities",
        }}
        return {{**super().crud(), **crud}}

    def document_info(self):
        return super().document_info()

    def logging(self, item):
        return super().logging(item)

    def migration(self):
        return super().migration()

    def serialization(self, from_format, to_format):
        return super().serialization(from_format, to_format)

    def validation(self):
        return super().validation()
'''

    def generate_mappers_patch(self) -> dict:
        c = self.config
        class_name = f"{c.pascal_name}Configuration"
        return {
            "import_line": f"from apps.dishacled.object_configurations.{c.snake_name}_configuration import {class_name}",
            "config_entry": f'    "{c.type_name}": {class_name},',
        }

