import os
import pytest
from apps.dishacled.shacl.parser import ShaclParser, ShaclProperty


EXAMPLE_TTL_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "..",
    "..",
    "clients",
    "vlacc-dams",
    "client-frontend",
    "inuits-dams-graphql-service",
    "shacl",
    "import-demo",
    "example-external-shape.ttl",
)

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
  ], [
    sh:datatype xsd:dateTime;
    sh:path rdfc:before;
    sh:name "before";
    sh:maxCount 1;
  ].
"""

SHACL_WITH_IN_VALUES = """\
@prefix rdfc: <https://w3id.org/rdf-connect#>.
@prefix sh: <http://www.w3.org/ns/shacl#>.
@prefix xsd: <http://www.w3.org/2001/XMLSchema#>.

[ ] a sh:NodeShape;
  sh:targetClass rdfc:TestProcessor;
  sh:property [
    sh:datatype xsd:string;
    sh:path rdfc:format;
    sh:name "format";
    sh:in ("json" "xml" "csv");
    sh:maxCount 1;
  ], [
    sh:datatype xsd:decimal;
    sh:path rdfc:rate;
    sh:name "rate";
    sh:maxCount 1;
  ], [
    sh:datatype xsd:anyURI;
    sh:path rdfc:endpoint;
    sh:name "endpoint";
    sh:maxCount 1;
    sh:minCount 1;
  ].
"""


class TestShaclParser:
    def test_parse_returns_dict_of_shapes(self):
        parser = ShaclParser()
        shapes = parser.parse(EXAMPLE_TTL)
        assert isinstance(shapes, dict)
        assert "LdesClient" in shapes

    def test_parse_extracts_properties(self):
        parser = ShaclParser()
        shapes = parser.parse(EXAMPLE_TTL)
        props = shapes["LdesClient"]
        prop_names = [p.name for p in props]
        assert "url" in prop_names
        assert "output" in prop_names
        assert "follow" in prop_names
        assert "pollInterval" in prop_names
        assert "before" in prop_names

    def test_parse_property_datatypes(self):
        parser = ShaclParser()
        shapes = parser.parse(EXAMPLE_TTL)
        props = {p.name: p for p in shapes["LdesClient"]}

        assert props["url"].datatype == "xsd:string"
        assert props["follow"].datatype == "xsd:boolean"
        assert props["pollInterval"].datatype == "xsd:integer"
        assert props["before"].datatype == "xsd:dateTime"

    def test_parse_class_ref(self):
        parser = ShaclParser()
        shapes = parser.parse(EXAMPLE_TTL)
        props = {p.name: p for p in shapes["LdesClient"]}

        assert props["output"].class_ref == "rdfc:Writer"
        assert props["url"].class_ref is None

    def test_parse_cardinality(self):
        parser = ShaclParser()
        shapes = parser.parse(EXAMPLE_TTL)
        props = {p.name: p for p in shapes["LdesClient"]}

        assert props["url"].min_count == 1
        assert props["url"].max_count == 1
        assert props["url"].is_required is True

        assert props["follow"].min_count == 0
        assert props["follow"].max_count == 1
        assert props["follow"].is_required is False

    def test_parse_input_field_type_mapping(self):
        parser = ShaclParser()
        shapes = parser.parse(EXAMPLE_TTL)
        props = {p.name: p for p in shapes["LdesClient"]}

        assert props["url"].input_field_type == "baseTextField"
        assert props["follow"].input_field_type == "baseCheckbox"
        assert props["pollInterval"].input_field_type == "baseNumberField"
        assert props["output"].input_field_type == "hasWriterField"

    def test_parse_in_values(self):
        parser = ShaclParser()
        shapes = parser.parse(SHACL_WITH_IN_VALUES)
        props = {p.name: p for p in shapes["TestProcessor"]}

        assert props["format"].in_values == ["json", "xml", "csv"]
        assert props["format"].input_field_type == "baseSelectField"

    def test_parse_decimal_maps_to_number_field(self):
        parser = ShaclParser()
        shapes = parser.parse(SHACL_WITH_IN_VALUES)
        props = {p.name: p for p in shapes["TestProcessor"]}

        assert props["rate"].input_field_type == "baseNumberField"

    def test_parse_anyuri_maps_to_text_field(self):
        parser = ShaclParser()
        shapes = parser.parse(SHACL_WITH_IN_VALUES)
        props = {p.name: p for p in shapes["TestProcessor"]}

        assert props["endpoint"].input_field_type == "baseTextField"
        assert props["endpoint"].is_required is True

    def test_parse_multiple_shapes(self):
        parser = ShaclParser()
        shapes = parser.parse(
            open(EXAMPLE_TTL_PATH).read()
            if os.path.exists(EXAMPLE_TTL_PATH)
            else EXAMPLE_TTL
        )
        assert len(shapes) >= 1

    def test_parse_example_file_ldes_client(self):
        if not os.path.exists(EXAMPLE_TTL_PATH):
            pytest.skip("Example TTL file not found")
        parser = ShaclParser()
        shapes = parser.parse(open(EXAMPLE_TTL_PATH).read())
        assert "LdesClient" in shapes
        props = {p.name: p for p in shapes["LdesClient"]}
        assert "url" in props
        assert props["url"].is_required is True
        assert "output" in props
        assert props["output"].class_ref == "rdfc:Writer"

    def test_parse_processor_metadata(self):
        parser = ShaclParser()
        shapes = parser.parse(EXAMPLE_TTL)
        meta = parser.parse_processor_metadata(EXAMPLE_TTL)
        assert meta["label"] == "ldes client"
        assert meta["comment"] == "An LDES client that can read a stream of members from an LDES."
        assert meta["class"] == "LDESClientProcessor"

    def test_shacl_property_dataclass(self):
        prop = ShaclProperty(
            name="test",
            path="rdfc:test",
            datatype="xsd:string",
            class_ref=None,
            min_count=0,
            max_count=1,
            in_values=[],
            input_field_type="baseTextField",
        )
        assert prop.is_required is False
        assert prop.name == "test"

    def test_channel_class_maps_to_writer_field(self):
        ttl = """\
@prefix rdfc: <https://w3id.org/rdf-connect#>.
@prefix sh: <http://www.w3.org/ns/shacl#>.

[ ] a sh:NodeShape;
  sh:targetClass rdfc:TestProc;
  sh:property [
    sh:class rdfc:Channel;
    sh:path rdfc:out;
    sh:name "out";
    sh:maxCount 1;
  ].
"""
        parser = ShaclParser()
        shapes = parser.parse(ttl)
        props = {p.name: p for p in shapes["TestProc"]}
        assert props["out"].input_field_type == "hasWriterField"
