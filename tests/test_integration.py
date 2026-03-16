"""End-to-end integration test: TTL → parse → codegen → verify all outputs."""

import pytest
from apps.dishacled.shacl.parser import ShaclParser
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
def generated_files():
    parser = ShaclParser()
    shapes = parser.parse(EXAMPLE_TTL)
    properties = shapes["LdesClient"]
    config = CodegenConfig(
        type_name="ldesClientProcessor",
        runner_type="jsRunner",
        pill_label="LDES",
    )
    generator = CodeGenerator(config, properties)
    return generator.generate_all()


class TestIntegrationEndToEnd:
    def test_generates_queries_file(self, generated_files):
        queries = generated_files["ldesClientProcessor.queries.ts"]
        assert "fragment minimalLdesClientProcessor" in queries
        assert "fragment fullLdesClientProcessor" in queries
        assert "query GetLdesClientProcessorCreateForm" in queries

    def test_generates_python_config(self, generated_files):
        config = generated_files["ldes_client_processor_configuration.py"]
        assert "class LdesClientProcessorConfiguration" in config

    def test_patches_contain_all_keys(self, generated_files):
        patches = generated_files["patches"]
        expected_keys = [
            "dishacledSchema.schema.ts",
            "dishacledResolver.ts",
            "dishacled.queries.ts",
            "processor.queries.ts",
            "typePillLabelMapping.ts",
            "dishacledPermissions.ts",
            "en.json",
            "mappers.py",
        ]
        for key in expected_keys:
            assert key in patches, f"Missing patch key: {key}"

    def test_processor_queries_patch_has_pipeline_entries(self, generated_files):
        patch = generated_files["patches"]["processor.queries.ts"]
        assert "pipeline_bulk_operations_entry" in patch
        assert "pipeline_entity_picker_list_spread" in patch
        assert "pipeline_entity_picker_filters_value" in patch

    def test_processor_queries_patch_has_all_processor_entries(self, generated_files):
        patch = generated_files["patches"]["processor.queries.ts"]
        assert "get_all_processor_entities_spread" in patch
        assert "get_all_processor_filters_default_value" in patch
        assert "get_all_processor_bulk_operations" in patch
        assert "get_related_processor_filter_value" in patch

    def test_pipeline_bulk_operations_entry_structure(self, generated_files):
        entry = generated_files["patches"]["processor.queries.ts"][
            "pipeline_bulk_operations_entry"
        ]
        assert entry["icon"] == "PlusCircle"
        assert "ldes-client-processor" in entry["label"]
        assert entry["formQuery"] == "GetLdesClientProcessorCreateForm"
        assert entry["formRelationType"] == "isProcessorFor"
        assert entry["can"] == "update:pipeline:has-processor"

    def test_pipeline_entity_picker_list_spread_content(self, generated_files):
        spread = generated_files["patches"]["processor.queries.ts"][
            "pipeline_entity_picker_list_spread"
        ]
        assert "... on LdesClientProcessor" in spread
        assert "...minimalLdesClientProcessor" in spread

    def test_pipeline_entity_picker_filters_value_content(self, generated_files):
        value = generated_files["patches"]["processor.queries.ts"][
            "pipeline_entity_picker_filters_value"
        ]
        assert value == "ldesClientProcessor"

    def test_schema_patch_has_type_definition(self, generated_files):
        schema = generated_files["patches"]["dishacledSchema.schema.ts"]
        assert "LdesClientProcessor implements Entity" in schema["type_definition"]

    def test_translations_have_all_sections(self, generated_files):
        translations = generated_files["patches"]["en.json"]
        assert "bulk-operations" in translations
        assert "entity-translations" in translations
        assert "metadata" in translations
        assert "navigation" in translations
        assert "panel-labels" in translations
