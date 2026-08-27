"""Deriving a component from its SHACL must not be paid for twice.

A listing describes every processor of every repository on the page, and every
description walks the same shapes again: the config form, the properties, the
ports. Warm -- every GitHub response served from cache -- a page of 52
components still spent over a second in `rdflib`, nearly all of it re-deriving
answers it had already computed for the identical document.

Two things caused it, and these tests pin both:

* `_uri_to_prefixed` re-materialised the graph's namespace list on every URI it
  shortened -- three per property, thousands per page.
* the derivations themselves were uncached, so the same (document, class) pair
  was walked once per component of the repository and once more per request.
"""

from rdflib import Graph

from apps.dishacled.shacl.form import shacl_to_form_fields
from apps.dishacled.shacl.graphs import parsed_graph
from apps.dishacled.shacl.parser import ShaclParser


def _ttl(class_name="Proc", property_count=25):
    properties = "\n".join(
        f'        [ sh:path rdfc:p{i}; sh:name "p{i}"; sh:datatype xsd:string ],'
        for i in range(property_count)
    ).rstrip(",")
    return f"""\
@prefix rdfc: <https://w3id.org/rdf-connect#>.
@prefix sh: <http://www.w3.org/ns/shacl#>.
@prefix xsd: <http://www.w3.org/2001/XMLSchema#>.

rdfc:{class_name} rdfc:jsImplementationOf rdfc:Processor.

[ ] a sh:NodeShape;
    sh:targetClass rdfc:{class_name};
    sh:property
{properties}
    .
"""


class TestPrefixLookupIsNotPerUri:
    def test_shortening_uris_does_not_rescan_the_namespaces_each_time(
        self, monkeypatch
    ):
        ttl = _ttl(property_count=25)
        calls = []
        original = Graph.namespaces

        def counting(self):
            calls.append(1)
            return original(self)

        monkeypatch.setattr(Graph, "namespaces", counting)

        properties = ShaclParser().parse_main_processor_properties(
            ttl, "https://w3id.org/rdf-connect#Proc"
        )

        assert len(properties) == 25
        # one materialisation for the document, not one per URI per property
        assert len(calls) <= 1


class TestDerivationsAreCachedPerDocument:
    def test_the_same_shape_is_walked_once(self, monkeypatch):
        ttl = _ttl(class_name="Once")
        target = "https://w3id.org/rdf-connect#Once"
        # prime: the graph parse itself is already shared, so only the walk is
        # under test here
        parsed_graph(ttl)

        calls = []
        original = ShaclParser._parse_property

        def counting(self, graph, node):
            calls.append(1)
            return original(self, graph, node)

        monkeypatch.setattr(ShaclParser, "_parse_property", counting)

        first = ShaclParser().parse_main_processor_properties(ttl, target)
        walked = len(calls)
        second = ShaclParser().parse_main_processor_properties(ttl, target)

        assert walked > 0
        assert len(calls) == walked
        assert [p.name for p in second] == [p.name for p in first]

    def test_a_cached_property_list_cannot_be_emptied_by_a_caller(self):
        ttl = _ttl(class_name="Owned")
        target = "https://w3id.org/rdf-connect#Owned"

        first = ShaclParser().parse_main_processor_properties(ttl, target)
        first.clear()

        assert ShaclParser().parse_main_processor_properties(ttl, target)

    def test_the_same_form_is_built_once(self, monkeypatch):
        ttl = _ttl(class_name="Formed")
        target = "https://w3id.org/rdf-connect#Formed"

        from apps.dishacled.shacl import shui

        calls = []
        original = shui.ShuiFormBuilder.build_tree

        def counting(self):
            calls.append(1)
            return original(self)

        monkeypatch.setattr(shui.ShuiFormBuilder, "build_tree", counting)

        first = shacl_to_form_fields(ttl, target_class=target)
        second = shacl_to_form_fields(ttl, target_class=target)

        assert first == second
        assert len(calls) == 1

    def test_a_cached_form_cannot_be_edited_by_a_caller(self):
        """The document's data dict is handed to serializers that add to it."""
        ttl = _ttl(class_name="Editable")
        target = "https://w3id.org/rdf-connect#Editable"

        first = shacl_to_form_fields(ttl, target_class=target)
        first.clear()
        second = shacl_to_form_fields(ttl, target_class=target)

        assert second
        assert second is not first

    def test_channel_options_are_not_shared_between_callers(self):
        """Options are an argument, so two callers must get their own answer."""
        ttl = """\
@prefix rdfc: <https://w3id.org/rdf-connect#>.
@prefix sh: <http://www.w3.org/ns/shacl#>.

rdfc:Chan rdfc:jsImplementationOf rdfc:Processor.

[ ] a sh:NodeShape;
    sh:targetClass rdfc:Chan;
    sh:property [ sh:path rdfc:writer; sh:name "writer"; sh:class rdfc:Writer ].
"""
        target = "https://w3id.org/rdf-connect#Chan"

        without = shacl_to_form_fields(ttl, target_class=target)
        with_options = shacl_to_form_fields(
            ttl, channel_options=["a", "b"], target_class=target
        )

        assert without["writer"]["inputField"]["options"] == []
        assert [
            option["value"]
            for option in with_options["writer"]["inputField"]["options"]
        ] == ["a", "b"]
