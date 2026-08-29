"""Round-trip regression: connections must survive the read from the store.

The failing case in the field: a component from the shared demonstrator
catalog (loket-error-alert-service) declares *two* `tcs:configShape`
qualified relations -- an empty placeholder (`genericSWConfigShape`) next to
the anonymous shape that actually carries its reader/writer port properties,
and that useful shape has no `sh:targetClass`. The definition export wrote
the connection completely (`tcs:readsFrom` plus the embedded port binding),
but the read path could not resolve the port name -- `_config_shape` might
pick the empty shape, and a shape without a target class produced no shape
index at all -- so the connection silently vanished from the entity while
still standing in the store.
"""

from rdflib import Graph

from apps.dishacled.serializers.pipeline_serializer import PipelineSerializer


DEFINITION = """
@prefix tcs:   <https://w3id.org/toolchain#> .
@prefix pplan: <http://purl.org/net/p-plan#> .
@prefix prov:  <http://www.w3.org/ns/prov#> .
@prefix dct:   <http://purl.org/dc/terms/> .
@prefix dcat:  <http://www.w3.org/ns/dcat#> .
@prefix sh:    <http://www.w3.org/ns/shacl#> .
@prefix rdfc:  <https://w3id.org/rdf-connect#> .
@prefix rdfs:  <http://www.w3.org/2000/01/rdf-schema#> .

<http://x/pipelines/p1> a tcs:PipelineDefinition ;
  rdfs:label "p1" ;
  dct:identifier "p1" .

# -- producer: the healthy kind, config shape with a target class ----------
<http://x/comp/monitor> a tcs:PipelineComponent ;
  dct:identifier "monitor" ;
  dcat:qualifiedRelation [
    dcat:hadRole tcs:configShape ;
    dct:relation <http://x/shape/MonitorShape>
  ] .
<http://x/shape/MonitorShape> a sh:NodeShape ;
  sh:targetClass <http://x/class/Monitor> ;
  sh:property [
    sh:path rdfc:output ; sh:name "output" ; sh:class rdfc:Writer
  ] .

# -- consumer: two configShape relations, the useful one anonymous and
# -- without sh:targetClass (the loket-error-alert-service pattern) --------
<http://x/comp/errsvc> a tcs:PipelineComponent ;
  dct:identifier "errsvc" ;
  dcat:qualifiedRelation [
    dcat:hadRole tcs:configShape ;
    dct:relation <http://x/shape/EmptyShape>
  ] , [
    dcat:hadRole tcs:configShape ;
    dct:relation _:portShape
  ] .
<http://x/shape/EmptyShape> a sh:NodeShape .
_:portShape a sh:NodeShape ;
  sh:property [
    sh:path rdfc:alerts ; sh:name "alerts" ; sh:class rdfc:Reader
  ] .

# -- the steps, exactly as the definition export writes them ---------------
<http://x/pipelines/p1/step/monitor> a tcs:InstancePipelineComponent ;
  pplan:isStepOfPlan <http://x/pipelines/p1> ;
  prov:specializationOf <http://x/comp/monitor> ;
  rdfs:label "Monitor" ;
  tcs:writesTo <http://x/pipelines/p1/monitor-output-channel> ;
  pplan:hasInputVar [
    a tcs:PipelineConfig ;
    tcs:embedded [ rdfc:output <http://x/pipelines/p1/monitor-output-channel> ]
  ] .

<http://x/pipelines/p1/step/errsvc> a tcs:InstancePipelineComponent ;
  pplan:isStepOfPlan <http://x/pipelines/p1> ;
  prov:specializationOf <http://x/comp/errsvc> ;
  rdfs:label "Error alert service" ;
  tcs:readsFrom <http://x/pipelines/p1/monitor-output-channel> ;
  pplan:hasInputVar [
    a tcs:PipelineConfig ;
    tcs:embedded [ rdfc:alerts <http://x/pipelines/p1/monitor-output-channel> ]
  ] .

<http://x/pipelines/p1/monitor-output-channel> a tcs:Channel .
"""


def _read():
    graph = Graph()
    graph.parse(data=DEFINITION, format="turtle")
    return PipelineSerializer().from_sparql_to_elody({"graph": graph})


def _relation(entity, key):
    return next(r for r in entity["relations"] if r["key"] == key)


def test_component_shape_is_found_without_target_class():
    graph = Graph()
    graph.parse(data=DEFINITION, format="turtle")
    components = PipelineSerializer()._components(graph)
    errsvc = next(c for iri, c in components.items() if str(iri).endswith("errsvc"))

    assert errsvc.shape is not None
    assert "alerts" in errsvc.shape.properties


def test_connection_survives_the_read():
    entity = _read()

    consumer = _relation(entity, "errsvc")
    assert {
        "key": "connections.alerts.from",
        "value": "monitor|output",
    } in consumer["metadata"]


def test_producer_side_still_reads_as_before():
    entity = _read()

    keys = sorted(r["key"] for r in entity["relations"])
    assert keys == ["errsvc", "monitor"]
