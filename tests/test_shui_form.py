"""Tests for converting a SHACL 1.2 UI tree into Elody nested form fields."""

from apps.dishacled.shacl.form import shacl_to_form_fields


HTTP_UTILS_TTL = """\
@prefix rdfc: <https://w3id.org/rdf-connect#>.
@prefix sh: <http://www.w3.org/ns/shacl#>.
@prefix xsd: <http://www.w3.org/2001/XMLSchema#>.

rdfc:HttpFetch rdfc:jsImplementationOf rdfc:Processor.

[ ] a sh:NodeShape;
  sh:targetClass rdfc:HttpFetch;
  sh:property [
    sh:datatype xsd:string; sh:path rdfc:url; sh:name "url";
    sh:minCount 1; sh:order 0;
  ], [
    sh:class rdfc:Writer; sh:path rdfc:writer; sh:name "writer"; sh:order 1;
  ], [
    sh:class rdfc:HttpFetchOptions; sh:path rdfc:options; sh:name "options"; sh:order 2;
  ].

[ ] a sh:NodeShape;
  sh:targetClass rdfc:HttpFetchOptions;
  sh:property [
    sh:datatype xsd:string; sh:path rdfc:method; sh:name "method";
  ], [
    sh:datatype xsd:integer; sh:path rdfc:timeout; sh:name "timeout";
  ], [
    sh:class rdfc:HttpFetchAuth; sh:path rdfc:auth; sh:name "auth";
  ].

[ ] a sh:NodeShape;
  sh:targetClass rdfc:HttpFetchAuth;
  sh:property [
    sh:datatype xsd:string; sh:path rdfc:type; sh:name "type"; sh:minCount 1;
  ].
"""


class TestShaclToFormFields:
    def test_top_level_fields(self):
        fields = shacl_to_form_fields(HTTP_UTILS_TTL)
        assert set(fields.keys()) == {"url", "writer", "options"}

    def test_url_is_text(self):
        fields = shacl_to_form_fields(HTTP_UTILS_TTL)
        assert fields["url"]["inputField"]["type"] == "text"
        assert fields["url"]["inputField"]["validation"]["value"] == ["required"]

    def test_writer_is_channel_dropdown(self):
        fields = shacl_to_form_fields(HTTP_UTILS_TTL, ["json", "rdf"])
        wf = fields["writer"]["inputField"]
        assert wf["type"] == "dropdown"
        assert wf["channelField"] is True
        assert [o["value"] for o in wf["options"]] == ["json", "rdf"]

    def test_options_is_nested_subfields(self):
        fields = shacl_to_form_fields(HTTP_UTILS_TTL)
        of = fields["options"]["inputField"]
        assert of["type"] == "inputFieldWithSubFields"
        assert of["isDetailsEditor"] is True
        keys = {sf["key"] for sf in of["subFields"]}
        assert {"options.method", "options.timeout", "options.auth"} <= keys

    def test_nested_field_types(self):
        fields = shacl_to_form_fields(HTTP_UTILS_TTL)
        sub = {sf["key"]: sf for sf in fields["options"]["inputField"]["subFields"]}
        assert sub["options.method"]["inputField"]["type"] == "text"
        assert sub["options.timeout"]["inputField"]["type"] == "number"
        assert sub["options.auth"]["inputField"]["type"] == "inputFieldWithSubFields"

    def test_three_level_nesting_dotted_keys(self):
        fields = shacl_to_form_fields(HTTP_UTILS_TTL)
        options = fields["options"]["inputField"]
        auth = next(sf for sf in options["subFields"] if sf["key"] == "options.auth")
        type_field = auth["inputField"]["subFields"][0]
        assert type_field["key"] == "options.auth.type"
        assert type_field["inputField"]["type"] == "text"

    def test_subfield_label_and_typename(self):
        fields = shacl_to_form_fields(HTTP_UTILS_TTL)
        sf = fields["options"]["inputField"]["subFields"][0]
        assert sf["__typename"] == "SubField"
        assert sf["label"].startswith("metadata.labels.")

    def test_ordering_preserved(self):
        fields = shacl_to_form_fields(HTTP_UTILS_TTL)
        assert list(fields.keys()) == ["url", "writer", "options"]
