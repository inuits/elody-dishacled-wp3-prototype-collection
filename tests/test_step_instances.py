"""A pipeline is instances of components, not components.

The tutorial's pipeline runs two `rdfc:LogProcessorJs` -- one on the report
channel at `warn`, one on the output channel at `info`. Elody keyed everything
by the component's document id, so the two collapsed into one stage carrying
both configurations:

    <logprocessorjs> a rdfc:LogProcessorJs ;
        rdfc:label "output", "report" ;
        rdfc:level "info", "warn" .

which violates the processor's own shape (`sh:maxCount 1` on each) and is the
exact failure the toolchain's application-profile shapes warn about: "two
configs on the same step silently merge their predicates (e.g. duplicate
rdfc:level values)". The toolchain's own reference definition has two
`LogProcessorJs` steps and two `Sdsify` steps, so this is the modelling the
vocabulary already assumes -- `tcs:InstancePipelineComponent` is a
`p-plan:Step`, and steps are the many side of `prov:specializationOf`.

So each `hasProcessor` relation is a step with its own identity. Nothing about
the shapes changes: one shape per class, targeting every instance of it.
"""

import pyshacl
import pytest
from rdflib import Graph

from apps.dishacled.pipeline.connections import (
    connection_metadata,
    connections_for_pipeline,
    instances_of,
)
from apps.dishacled.serializers.pipeline_definition_serializer import (
    PipelineDefinitionSerializer,
)
from apps.dishacled.serializers.pipeline_serializer import PipelineSerializer
from apps.dishacled.serializers.pipeline_ttl_serializer import (
    PipelineTtlSerializer,
)


def _step_slugs(ttl: str) -> set[str]:
    """The step IRIs a definition declares, by their last path segment.

    Read off the parsed graph rather than the text: a step compacts to
    `step:<slug>` now that the document binds a prefix for the step namespace
    (the generator interpolates compacted IRIs into SPARQL, so they have to be
    legal CURIEs), and what these tests are about is which steps exist.
    """
    from rdflib import Graph, URIRef
    from rdflib.namespace import RDF

    graph = Graph()
    graph.parse(data=ttl, format="turtle")
    return {
        str(step).rsplit("/", 1)[-1]
        for step in graph.subjects(
            RDF.type, URIRef("https://w3id.org/toolchain#InstancePipelineComponent")
        )
    }


LOGGER_TTL = """\
@prefix rdfc: <https://w3id.org/rdf-connect#>.
@prefix sh:   <http://www.w3.org/ns/shacl#>.
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#>.

rdfc:LogProcessorJs rdfc:jsImplementationOf rdfc:Processor.

[] a sh:NodeShape;
  sh:targetClass rdfc:LogProcessorJs;
  sh:property [
    sh:class rdfc:Reader; sh:path rdfc:reader; sh:name "reader";
    sh:minCount 1; sh:maxCount 1;
  ], [
    sh:class rdfc:Writer; sh:path rdfc:writer; sh:name "writer"; sh:maxCount 1;
  ], [
    sh:datatype xsd:string; sh:path rdfc:label; sh:name "label"; sh:maxCount 1;
  ], [
    sh:datatype xsd:string; sh:path rdfc:level; sh:name "level"; sh:maxCount 1;
  ].
"""

SOURCE_TTL = """\
@prefix rdfc: <https://w3id.org/rdf-connect#>.
@prefix sh:   <http://www.w3.org/ns/shacl#>.
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#>.

rdfc:Ticker rdfc:jsImplementationOf rdfc:Processor.

[] a sh:NodeShape;
  sh:targetClass rdfc:Ticker;
  sh:property [
    sh:class rdfc:Writer; sh:path rdfc:writer; sh:name "writer";
    sh:minCount 1; sh:maxCount 1;
  ].
"""

LOGGER = "acme--log--LogProcessorJs"
TICKER = "acme--tick--Ticker"


def component(identifier, cls, ttl, properties):
    return {
        "_id": identifier,
        "type": "githubProcessor",
        "metadata": [
            {"key": "name", "value": cls},
            {"key": "runtime", "value": "ts"},
        ],
        "data": {
            "rawTtl": ttl,
            "componentIri": f"https://w3id.org/rdf-connect#{cls}",
            "componentName": cls,
            "properties": properties,
            "deployment": {"imports": [], "packages": []},
        },
    }


def prop(name, class_ref=None, required=False):
    return {
        "name": name,
        "inputFieldType": "baseTextField",
        "isRequired": required,
        "inValues": [],
        "classRef": class_ref,
    }


COMPONENTS = {
    LOGGER: component(
        LOGGER, "LogProcessorJs", LOGGER_TTL,
        [prop("reader", "rdfc:Reader", True), prop("writer", "rdfc:Writer"),
         prop("label"), prop("level")],
    ),
    TICKER: component(
        TICKER, "Ticker", SOURCE_TTL, [prop("writer", "rdfc:Writer", True)]
    ),
}


def two_loggers(source_reference="acme--tick--Ticker|writer"):
    """The tutorial's shape: one source, two loggers configured differently."""
    return {
        "_id": "pipeline-1",
        "type": "pipeline",
        "identifiers": ["pipeline-1"],
        "metadata": [{"key": "name", "value": "Two loggers"}],
        "relations": [
            {"key": TICKER, "type": "hasProcessor", "metadata": []},
            {
                "key": LOGGER,
                "type": "hasProcessor",
                "metadata": [
                    {"key": "label", "value": "report"},
                    {"key": "level", "value": "warn"},
                    *connection_metadata("reader", source_reference),
                ],
            },
            {
                "key": LOGGER,
                "type": "hasProcessor",
                "metadata": [
                    {"key": "label", "value": "output"},
                    {"key": "level", "value": "info"},
                    *connection_metadata("reader", source_reference),
                ],
            },
        ],
    }


class TestInstances:
    def test_two_relations_on_one_component_are_two_instances(self):
        instances = instances_of(two_loggers(), COMPONENTS)
        assert len(instances) == 3

    def test_each_gets_its_own_id(self):
        ids = [i.id for i in instances_of(two_loggers(), COMPONENTS)]
        assert len(set(ids)) == 3
        assert ids == ["ticker", "logprocessorjs", "logprocessorjs-2"]

    def test_a_single_instance_keeps_the_plain_name(self):
        single = {
            "relations": [{"key": TICKER, "type": "hasProcessor", "metadata": []}]
        }
        assert [i.id for i in instances_of(single, COMPONENTS)] == ["ticker"]

    def test_an_explicit_instance_id_is_kept(self):
        # what a pipeline read back out of the store carries: the step slug it
        # was stored under, so a saved connection keeps pointing at the same step
        pipeline = two_loggers()
        pipeline["relations"][1]["metadata"].append(
            {"key": "instance", "value": "reporter"}
        )
        ids = [i.id for i in instances_of(pipeline, COMPONENTS)]
        assert "reporter" in ids

    def test_every_instance_knows_its_component(self):
        instances = instances_of(two_loggers(), COMPONENTS)
        assert [i.key for i in instances] == [TICKER, LOGGER, LOGGER]

    def test_the_order_is_the_relation_order(self):
        instances = instances_of(two_loggers(), COMPONENTS)
        assert instances[0].key == TICKER


class TestConnectionsAddressInstances:
    def test_a_reference_by_instance_id_resolves(self):
        pipeline = two_loggers(source_reference="ticker|writer")
        connections = connections_for_pipeline(pipeline, COMPONENTS)
        assert len(connections) == 2
        assert {c.target for c in connections} == {
            "logprocessorjs",
            "logprocessorjs-2",
        }

    def test_a_legacy_component_reference_still_resolves(self):
        # pipelines saved before steps had identity reference the component
        connections = connections_for_pipeline(two_loggers(), COMPONENTS)
        assert len(connections) == 2
        assert {c.source for c in connections} == {"ticker"}

    def test_each_instance_carries_its_own_connection(self):
        pipeline = two_loggers(source_reference="ticker|writer")
        by_target = {
            c.target: c for c in connections_for_pipeline(pipeline, COMPONENTS)
        }
        assert by_target["logprocessorjs"].target_port == "reader"
        assert by_target["logprocessorjs-2"].target_port == "reader"


class TestTheRunnableExport:
    def _ttl(self):
        return PipelineTtlSerializer().serialize(
            two_loggers("ticker|writer"), COMPONENTS
        )

    def test_both_loggers_are_stages(self):
        ttl = self._ttl()
        assert "<logprocessorjs>" in ttl and "<logprocessorjs-2>" in ttl

    def test_each_keeps_its_own_configuration(self):
        graph = Graph()
        graph.parse(data=self._ttl(), format="turtle", publicID="file:///p/")
        from rdflib import Namespace, URIRef

        RDFC = Namespace("https://w3id.org/rdf-connect#")
        labels = {
            str(graph.value(URIRef(f"file:///p/{step}"), RDFC.label))
            for step in ("logprocessorjs", "logprocessorjs-2")
        }
        assert labels == {"report", "output"}

    def test_both_are_handed_to_the_runner(self):
        ttl = self._ttl()
        # one runner group, both processors in it
        assert ttl.count("rdfc:instantiates rdfc:NodeRunner") == 1
        assert "rdfc:processor" in ttl

    def test_it_conforms_to_the_processor_shape(self):
        """The point of the whole change.

        Before, one node carried `rdfc:label "output", "report"` against a
        shape saying `sh:maxCount 1`.
        """
        data = Graph()
        data.parse(data=self._ttl(), format="turtle", publicID="file:///p/")
        shapes = Graph()
        shapes.parse(data=LOGGER_TTL, format="turtle")
        shapes.parse(data=SOURCE_TTL, format="turtle")
        conforms, _, text = pyshacl.validate(
            data, shacl_graph=shapes, advanced=True
        )
        assert conforms, text


class TestTheDefinitionExport:
    def _ttl(self):
        return PipelineDefinitionSerializer(
            base_uri="http://elody.local/pipelines/pipeline-1/"
        ).serialize(two_loggers("ticker|writer"), COMPONENTS)

    def test_both_loggers_are_steps(self):
        steps = _step_slugs(self._ttl())
        assert {"logprocessorjs", "logprocessorjs-2"} <= steps

    def test_both_specialize_the_same_component(self):
        # the toolchain's own demo does exactly this
        assert self._ttl().count("prov:specializationOf") >= 3

    def test_each_step_carries_its_own_config(self):
        ttl = self._ttl()
        assert '"report"' in ttl and '"output"' in ttl


class TestReadingItBack:
    def _round_trip(self):
        ttl = PipelineDefinitionSerializer(
            base_uri="http://elody.local/pipelines/pipeline-1/"
        ).serialize(two_loggers("ticker|writer"), COMPONENTS)
        graph = Graph()
        graph.parse(data=ttl, format="turtle")
        return PipelineSerializer().from_sparql_to_elody({"graph": graph})

    def test_both_relations_come_back(self):
        entity = self._round_trip()
        keys = [
            r["key"]
            for r in entity["relations"]
            if r["type"] == "hasProcessor" and r["key"].startswith(LOGGER)
        ]
        # a key each, so the UI can show and configure them separately
        assert len(keys) == 2 and len(set(keys)) == 2

    def test_each_carries_its_instance_id(self):
        entity = self._round_trip()
        instances = {
            next(
                (m["value"] for m in r["metadata"] if m["key"] == "instance"), None
            )
            for r in entity["relations"]
            if r["key"].startswith(LOGGER)
        }
        assert instances == {"logprocessorjs", "logprocessorjs-2"}

    def test_the_configurations_did_not_swap_or_merge(self):
        entity = self._round_trip()  # noqa: F841 - read below by instance
        by_instance = {}
        for relation in entity["relations"]:
            values = {m["key"]: m["value"] for m in relation["metadata"]}
            if "instance" in values:
                by_instance[values["instance"]] = values
        assert by_instance["logprocessorjs"]["label"] == "report"
        assert by_instance["logprocessorjs"]["level"] == "warn"
        assert by_instance["logprocessorjs-2"]["label"] == "output"
        assert by_instance["logprocessorjs-2"]["level"] == "info"

    def test_a_second_generation_export_is_stable(self):
        from apps.dishacled.pipeline.connections import split_component_key

        entity = self._round_trip()
        # the relations now name steps, so the components they resolve to are
        # looked up the way `load_pipeline_components` does it: by relation key
        components = {
            r["key"]: COMPONENTS[split_component_key(r["key"])[0]]
            for r in entity["relations"]
        }
        again = PipelineDefinitionSerializer(
            base_uri="http://elody.local/pipelines/pipeline-1/"
        ).serialize(entity, components)
        assert {"logprocessorjs", "logprocessorjs-2"} <= _step_slugs(again)
        assert '"report"' in again and '"output"' in again


class TestAProducerUsedTwice:
    """The other direction: which of two identical producers feeds a consumer.

    A reference by component is only unambiguous while the component is used
    once. Reading a definition back has the step in hand, so it says which.
    """

    def _pipeline(self):
        return {
            "_id": "pipeline-1",
            "type": "pipeline",
            "identifiers": ["pipeline-1"],
            "metadata": [{"key": "name", "value": "Two tickers"}],
            "relations": [
                {"key": TICKER, "type": "hasProcessor", "metadata": []},
                {"key": TICKER, "type": "hasProcessor", "metadata": []},
                {
                    "key": LOGGER,
                    "type": "hasProcessor",
                    "metadata": [
                        {"key": "label", "value": "second"},
                        *connection_metadata("reader", "ticker-2|writer"),
                    ],
                },
            ],
        }

    def test_the_connection_names_the_second_ticker(self):
        connections = connections_for_pipeline(self._pipeline(), COMPONENTS)
        assert len(connections) == 1
        assert connections[0].source == "ticker-2"

    def test_the_runnable_export_wires_the_second_one(self):
        from rdflib import Namespace, URIRef

        RDFC = Namespace("https://w3id.org/rdf-connect#")
        ttl = PipelineTtlSerializer().serialize(self._pipeline(), COMPONENTS)
        graph = Graph()
        graph.parse(data=ttl, format="turtle", publicID="file:///p/")
        channel = graph.value(URIRef("file:///p/logprocessorjs"), RDFC.reader)
        assert graph.value(URIRef("file:///p/ticker-2"), RDFC.writer) == channel
        assert graph.value(URIRef("file:///p/ticker"), RDFC.writer) != channel

    def test_reading_it_back_still_names_the_second_one(self):
        ttl = PipelineDefinitionSerializer(
            base_uri="http://elody.local/pipelines/pipeline-1/"
        ).serialize(self._pipeline(), COMPONENTS)
        graph = Graph()
        graph.parse(data=ttl, format="turtle")
        entity = PipelineSerializer().from_sparql_to_elody({"graph": graph})

        references = [
            m["value"]
            for r in entity["relations"]
            for m in r["metadata"]
            if m["key"] == "connections.reader.from"
        ]
        assert references == ["ticker-2|writer"]
