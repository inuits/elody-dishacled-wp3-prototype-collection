"""Tests for the SHACL -> SHACL 1.2 UI form builder.

Reference: https://w3c.github.io/data-shapes/shacl12-ui/
The builder derives a UI tree from a processor's SHACL shapes by selecting a
`shui:` editor per property (from its constraints) and recursing into nested
node shapes as `shui:DetailsEditor`s.
"""

from apps.dishacled.shacl.shui import SH, ShuiFormBuilder, UiNode, SHUI


# Mirrors the real http-utils-processor-ts structure: a 3-level nesting
# HttpFetch -> HttpFetchOptions -> HttpFetchAuth.
HTTP_UTILS_TTL = """\
@prefix rdfc: <https://w3id.org/rdf-connect#>.
@prefix sh: <http://www.w3.org/ns/shacl#>.
@prefix xsd: <http://www.w3.org/2001/XMLSchema#>.

rdfc:HttpFetch rdfc:jsImplementationOf rdfc:Processor.

[ ] a sh:NodeShape;
  sh:targetClass rdfc:HttpFetch;
  sh:property [
    sh:datatype xsd:string; sh:path rdfc:url; sh:name "url";
    sh:minCount 1; sh:maxCount 1; sh:order 0;
  ], [
    sh:class rdfc:Writer; sh:path rdfc:writer; sh:name "writer";
    sh:minCount 1; sh:maxCount 1; sh:order 1;
  ], [
    sh:class rdfc:HttpFetchOptions; sh:path rdfc:options; sh:name "options";
    sh:maxCount 1; sh:order 2;
  ].

[ ] a sh:NodeShape;
  sh:targetClass rdfc:HttpFetchOptions;
  sh:property [
    sh:datatype xsd:string; sh:path rdfc:method; sh:name "method"; sh:maxCount 1;
  ], [
    sh:datatype xsd:integer; sh:path rdfc:timeout; sh:name "timeOutMilliseconds";
    sh:maxCount 1;
  ], [
    sh:datatype xsd:boolean; sh:path rdfc:closeOnEnd; sh:name "closeOnEnd";
    sh:maxCount 1;
  ], [
    sh:datatype xsd:string; sh:path rdfc:format; sh:name "format";
    sh:in ("json" "xml"); sh:maxCount 1;
  ], [
    sh:class rdfc:HttpFetchAuth; sh:path rdfc:auth; sh:name "auth"; sh:maxCount 1;
  ].

[ ] a sh:NodeShape;
  sh:targetClass rdfc:HttpFetchAuth;
  sh:property [
    sh:datatype xsd:string; sh:path rdfc:type; sh:name "type";
    sh:minCount 1; sh:maxCount 1;
  ].
"""


def _child(node: UiNode, name: str) -> UiNode:
    return next(c for c in node.children if c.name == name)


class TestEditorSelection:
    def test_string_maps_to_text_field_editor(self):
        root = ShuiFormBuilder(HTTP_UTILS_TTL).build_tree()
        assert _child(root, "url").editor == "TextFieldEditor"

    def test_writer_class_maps_to_instances_select_editor(self):
        root = ShuiFormBuilder(HTTP_UTILS_TTL).build_tree()
        assert _child(root, "writer").editor == "InstancesSelectEditor"

    def test_integer_maps_to_number_field_editor(self):
        root = ShuiFormBuilder(HTTP_UTILS_TTL).build_tree()
        options = _child(root, "options")
        assert _child(options, "timeOutMilliseconds").editor == "NumberFieldEditor"

    def test_boolean_maps_to_boolean_editor(self):
        root = ShuiFormBuilder(HTTP_UTILS_TTL).build_tree()
        options = _child(root, "options")
        assert _child(options, "closeOnEnd").editor == "BooleanEditor"

    def test_sh_in_maps_to_enum_select_editor(self):
        root = ShuiFormBuilder(HTTP_UTILS_TTL).build_tree()
        options = _child(root, "options")
        assert _child(options, "format").editor == "EnumSelectEditor"


class TestNesting:
    def test_main_shape_has_top_level_properties(self):
        root = ShuiFormBuilder(HTTP_UTILS_TTL).build_tree()
        assert {c.name for c in root.children} == {"url", "writer", "options"}

    def test_nested_shape_becomes_details_editor(self):
        root = ShuiFormBuilder(HTTP_UTILS_TTL).build_tree()
        assert _child(root, "options").editor == "DetailsEditor"

    def test_details_editor_has_children(self):
        root = ShuiFormBuilder(HTTP_UTILS_TTL).build_tree()
        options = _child(root, "options")
        names = {c.name for c in options.children}
        assert {"method", "timeOutMilliseconds", "closeOnEnd", "auth"} <= names

    def test_three_level_nesting(self):
        root = ShuiFormBuilder(HTTP_UTILS_TTL).build_tree()
        auth = _child(_child(root, "options"), "auth")
        assert auth.editor == "DetailsEditor"
        assert _child(auth, "type").is_required is True

    def test_order_is_captured(self):
        root = ShuiFormBuilder(HTTP_UTILS_TTL).build_tree()
        ordered = [c.name for c in sorted(root.children, key=lambda c: c.order or 0)]
        assert ordered == ["url", "writer", "options"]


class TestShuiTtl:
    def test_emits_shui_namespace(self):
        ttl = ShuiFormBuilder(HTTP_UTILS_TTL).to_ttl()
        assert str(SHUI) == "http://www.w3.org/ns/shacl-ui#"
        assert "shacl-ui#" in ttl

    def test_annotates_text_field_editor(self):
        ttl = ShuiFormBuilder(HTTP_UTILS_TTL).to_ttl()
        assert "TextFieldEditor" in ttl

    def test_annotates_details_editor_for_nested_shape(self):
        ttl = ShuiFormBuilder(HTTP_UTILS_TTL).to_ttl()
        assert "DetailsEditor" in ttl

    def test_parses_as_valid_turtle(self):
        from rdflib import Graph

        ttl = ShuiFormBuilder(HTTP_UTILS_TTL).to_ttl()
        g = Graph()
        g.parse(data=ttl, format="turtle")  # raises if invalid
        # at least one shui:editor triple present
        assert any(p == SHUI.editor for _, p, _ in g)


ALERT_SHAPE_TTL = """\
@prefix sh:   <http://www.w3.org/ns/shacl#>.
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#>.
@prefix oslc: <http://open-services.net/ns/core#>.
@prefix dct:  <http://purl.org/dc/terms/>.
@prefix mu:   <http://mu.semte.ch/vocabularies/core/>.

<http://lblod.data.gift/shapes/ErrorShape> a sh:NodeShape;
  sh:targetClass oslc:Error;
  sh:property [ sh:path mu:uuid;     sh:name "uuid";    sh:datatype xsd:string; ],
              [ sh:path oslc:message; sh:name "message"; sh:datatype xsd:string; ],
              [ sh:path dct:created;  sh:name "created"; sh:datatype xsd:dateTime; ].
"""


class TestVocabularyIsPreserved:
    """A shape that is not rdf-connect's keeps its own namespaces.

    The emitted `sh:path` and `sh:targetClass` used to be rebuilt in the `rdfc:`
    namespace from a bare local name, so an alert shape came out claiming
    `rdfc:message` and `rdfc:Error` -- IRIs that do not exist. The generated
    document has to name the same properties the source shape does, or it is not
    a description of that shape.
    """

    def _graph(self, ttl):
        from rdflib import Graph

        g = Graph()
        g.parse(data=ShuiFormBuilder(ttl).to_ttl(), format="turtle")
        return g

    def test_the_target_class_is_the_one_the_shape_declares(self):
        from rdflib import URIRef

        g = self._graph(ALERT_SHAPE_TTL)
        classes = set(g.objects(None, SH.targetClass))
        assert classes == {URIRef("http://open-services.net/ns/core#Error")}

    def test_property_paths_keep_their_own_vocabularies(self):
        from rdflib import URIRef

        g = self._graph(ALERT_SHAPE_TTL)
        assert set(g.objects(None, SH.path)) == {
            URIRef("http://mu.semte.ch/vocabularies/core/uuid"),
            URIRef("http://open-services.net/ns/core#message"),
            URIRef("http://purl.org/dc/terms/created"),
        }

    def test_no_rdf_connect_iri_is_invented(self):
        ttl = ShuiFormBuilder(ALERT_SHAPE_TTL).to_ttl()
        assert "w3id.org/rdf-connect" not in ttl

    def test_processor_shapes_are_unaffected(self):
        from rdflib import URIRef

        g = self._graph(HTTP_UTILS_TTL)
        paths = set(g.objects(None, SH.path))
        assert URIRef("https://w3id.org/rdf-connect#url") in paths
        assert URIRef("https://w3id.org/rdf-connect#HttpFetch") in set(
            g.objects(None, SH.targetClass)
        )


class TestFallback:
    def test_no_processor_class_uses_first_shape(self):
        ttl = """\
@prefix rdfc: <https://w3id.org/rdf-connect#>.
@prefix sh: <http://www.w3.org/ns/shacl#>.
@prefix xsd: <http://www.w3.org/2001/XMLSchema#>.

[ ] a sh:NodeShape;
  sh:targetClass rdfc:Foo;
  sh:property [ sh:datatype xsd:string; sh:path rdfc:a; sh:name "a"; ].
"""
        root = ShuiFormBuilder(ttl).build_tree()
        assert {c.name for c in root.children} == {"a"}
