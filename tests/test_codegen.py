import pytest
from apps.dishacled.shacl.parser import ShaclParser, ShaclProperty
from apps.dishacled.shacl.codegen import CodeGenerator, CodegenConfig


EXAMPLE_TTL = """\
@prefix rdfc: <https://w3id.org/rdf-connect#>.
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#>.
@prefix sh: <http://www.w3.org/ns/shacl#>.
@prefix xsd: <http://www.w3.org/2001/XMLSchema#>.

rdfc:LdesClient rdfc:jsImplementationOf rdfc:Processor;
  rdfs:label "ldes client";
  rdfs:comment "An LDES client that can read a stream of members from an LDES.";
  rdfc:file <./dist/lib/rdfc-processor.js>;
  rdfc:class "LDESClientProcessor";
  rdfc:entrypoint <./>.

[ ] a sh:NodeShape;
  sh:targetClass rdfc:LdesClient;
  sh:property [
    sh:datatype xsd:string;
    sh:path rdfc:url;
    sh:name "url";
    sh:maxCount 1;
    sh:minCount 1;
  ], [
    sh:class rdfc:Writer;
    sh:path rdfc:output;
    sh:name "output";
    sh:maxCount 1;
    sh:minCount 1;
  ], [
    sh:datatype xsd:boolean;
    sh:path rdfc:follow;
    sh:name "follow";
    sh:maxCount 1;
  ], [
    sh:datatype xsd:integer;
    sh:path rdfc:interval;
    sh:name "pollInterval";
    sh:maxCount 1;
  ].
"""


@pytest.fixture
def config():
    return CodegenConfig(
        type_name="ldesClientProcessor",
        runner_type="jsRunner",
        pill_label="LDES",
    )


@pytest.fixture
def properties():
    parser = ShaclParser()
    shapes = parser.parse(EXAMPLE_TTL)
    return shapes["LdesClient"]


@pytest.fixture
def generator(config, properties):
    return CodeGenerator(config, properties)


class TestCodegenConfig:
    def test_pascal_name(self, config):
        assert config.pascal_name == "LdesClientProcessor"

    def test_snake_name(self, config):
        assert config.snake_name == "ldes_client_processor"

    def test_kebab_name(self, config):
        assert config.kebab_name == "ldes-client-processor"

    def test_human_name(self, config):
        assert config.human_name == "Ldes client processor"


class TestQueryGeneration:
    def test_generates_queries_file(self, generator):
        content = generator.generate_queries()
        assert 'import { gql } from "graphql-modules"' in content
        assert "fragment minimalLdesClientProcessor on LdesClientProcessor" in content
        assert "fragment fullLdesClientProcessor on LdesClientProcessor" in content
        assert "fragment ldesClientProcessorSortOptions on LdesClientProcessor" in content
        assert "fragment filtersForLdesClientProcessor on LdesClientProcessor" in content
        assert "fragment ldesClientProcessorBulkOperations on LdesClientProcessor" in content

    def test_minimal_fragment_has_type_pills(self, generator):
        content = generator.generate_queries()
        assert "...typePillsIntialValues" in content
        assert "...typePillsTeaserMetadata" in content

    def test_minimal_fragment_has_name_and_runner(self, generator):
        content = generator.generate_queries()
        assert 'name: keyValue(key: "name", source: metadata)' in content
        assert 'isProcessorFor: keyValue(' in content

    def test_full_fragment_has_property_fields(self, generator):
        content = generator.generate_queries()
        assert 'url: keyValue(key: "url", source: metadata)' in content
        assert 'follow: keyValue(key: "follow", source: metadata)' in content
        assert 'pollInterval: keyValue(key: "pollInterval", source: metadata)' in content

    def test_full_fragment_has_writer_field(self, generator):
        content = generator.generate_queries()
        assert 'output: keyValue(key: "output", source: relations' in content

    def test_full_fragment_has_entity_view(self, generator):
        content = generator.generate_queries()
        assert "entityView {" in content
        assert "size(size: seventy)" in content
        assert "size(size: thirty)" in content

    def test_full_fragment_has_panels(self, generator):
        content = generator.generate_queries()
        assert "panels {" in content
        assert "panelType(input: metadata)" in content

    def test_full_fragment_url_required_validation(self, generator):
        content = generator.generate_queries()
        assert "validation(input: { value: required })" in content

    def test_full_fragment_checkbox_for_boolean(self, generator):
        content = generator.generate_queries()
        assert "inputField(type: baseCheckbox)" in content

    def test_full_fragment_number_field_for_integer(self, generator):
        content = generator.generate_queries()
        assert "inputField(type: baseNumberField)" in content

    def test_full_fragment_writer_field(self, generator):
        content = generator.generate_queries()
        assert "inputField(type: hasWriterField)" in content

    def test_filters_fragment(self, generator):
        content = generator.generate_queries()
        assert 'defaultValue(value: "ldesClientProcessor")' in content
        assert 'key: ["elody:1|metadata.name.value"]' in content

    def test_bulk_operations_fragment(self, generator):
        content = generator.generate_queries()
        assert 'label: "bulk-operations.create-ldes-client-processor"' in content
        assert 'formQuery: "GetLdesClientProcessorCreateForm"' in content
        assert 'formRelationType: "isLdesClientProcessorFor"' in content

    def test_create_form_query(self, generator):
        content = generator.generate_queries()
        assert "query GetLdesClientProcessorCreateForm" in content
        assert 'label(input: "navigation.create-ldes-client-processor")' in content
        assert "creationType(input: ldesClientProcessor)" in content

    def test_runner_bulk_operations_query(self, generator):
        content = generator.generate_queries()
        assert "query GetRunnerOnLdesClientProcessorBulkOperations" in content
        assert 'can: ["update:ldesClientProcessor:has-runner"]' in content
        assert 'formQuery: "GetJsRunnerCreateForm"' in content

    def test_filter_query(self, generator):
        content = generator.generate_queries()
        assert "query GetLdesClientProcessorFilter" in content


class TestSchemaPatches:
    def test_schema_patch(self, generator):
        patch = generator.generate_schema_patch()
        assert "ldesClientProcessor" in patch["enum_entry"]
        assert "LdesClientProcessor implements Entity" in patch["type_definition"]

    def test_resolver_patch(self, generator):
        patch = generator.generate_resolver_patch()
        assert 'type === "ldesclientprocessor"' in patch["resolve_type"]
        assert "LdesClientProcessor" in patch["resolver_entry"]


class TestTranslationPatch:
    def test_translation_keys(self, generator):
        translations = generator.generate_translations()
        assert "create-ldes-client-processor" in translations["bulk-operations"]
        assert "ldesClientProcessor" in translations["entity-translations"]["plural"]
        assert "url" in translations["metadata"]["labels"]
        assert "follow" in translations["metadata"]["labels"]


class TestPillLabelPatch:
    def test_pill_label(self, generator):
        entry = generator.generate_pill_label_entry()
        assert entry == ('ldesClientProcessor', ['LDES'])


class TestPermissionPatch:
    def test_permission_entry(self, generator):
        entry = generator.generate_permission_entry()
        assert "update:ldesClientProcessor:has-runner" in entry


class TestProcessorQueriesPatch:
    def test_has_pipeline_bulk_operations_entry(self, generator):
        patch = generator.generate_processor_queries_patch()
        entry = patch["pipeline_bulk_operations_entry"]
        assert entry["icon"] == "PlusCircle"
        assert entry["label"] == "bulk-operations.create-ldes-client-processor"
        assert entry["formQuery"] == "GetLdesClientProcessorCreateForm"
        assert entry["formRelationType"] == "isProcessorFor"
        assert entry["can"] == "update:pipeline:has-processor"

    def test_has_pipeline_entity_picker_list_spread(self, generator):
        patch = generator.generate_processor_queries_patch()
        spread = patch["pipeline_entity_picker_list_spread"]
        assert "... on LdesClientProcessor" in spread
        assert "...minimalLdesClientProcessor" in spread

    def test_has_pipeline_entity_picker_filters_value(self, generator):
        patch = generator.generate_processor_queries_patch()
        assert patch["pipeline_entity_picker_filters_value"] == "ldesClientProcessor"

    def test_has_all_processor_filters_value(self, generator):
        patch = generator.generate_processor_queries_patch()
        assert patch["get_all_processor_filters_default_value"] == "ldesClientProcessor"


class TestPythonConfig:
    def test_generates_config(self, generator):
        content = generator.generate_python_config()
        assert "class LdesClientProcessorConfiguration" in content
        assert "ElodyConfiguration" in content
        assert '"collection": "entities"' in content

    def test_generates_mappers_patch(self, generator):
        patch = generator.generate_mappers_patch()
        assert "ldesClientProcessor" in patch["config_entry"]
        assert "LdesClientProcessorConfiguration" in patch["import_line"]


class TestAllFiles:
    def test_generate_all_returns_dict(self, generator):
        files = generator.generate_all()
        assert "ldesClientProcessor.queries.ts" in files
        assert "ldes_client_processor_configuration.py" in files
        assert "patches" in files

    def test_patches_have_expected_keys(self, generator):
        files = generator.generate_all()
        patches = files["patches"]
        assert "dishacledSchema.schema.ts" in patches
        assert "dishacledResolver.ts" in patches
        assert "dishacled.queries.ts" in patches
        assert "processor.queries.ts" in patches
        assert "typePillLabelMapping.ts" in patches
        assert "dishacledPermissions.ts" in patches
        assert "en.json" in patches
        assert "mappers.py" in patches
