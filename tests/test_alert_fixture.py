"""Tests for the local `oslc:Error` alert fixture.

The fixture stands in for redpencil's error graph so alert ingestion (A1) and
visualisation (A2) can be built with no external dependency, and it doubles as
the contract handed to redpencil and IDLab: "this is the shape and the endpoint
Elody expects".

Two things are worth checking rather than assuming:

* the sample data really conforms to the shape the threshold-monitor publishes
  -- proved with pyshacl, plus a negative control so a vacuous pass is visible;
* the queries in `docs/alert-fixture.md` really return what the docs claim.

Shape provenance: https://github.com/rdf-connect/threshhold-monitor-processor
(branch `master`), `processor.ttl`, where `ex:` is bound to
<http://lblod.data.gift/shapes/>.
"""

import os
from pathlib import Path

import pytest
from pyshacl import validate
from rdflib import BNode, Dataset, Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, XSD

from apps.dishacled.shacl.contracts import (
    DEFAULT_ALERTS_PATH,
    DEFAULT_CONTRACTS_PATH,
    ContractCatalog,
    extract_shape_graph,
)

OSLC = Namespace("http://open-services.net/ns/core#")
MU = Namespace("http://mu.semte.ch/vocabularies/core/")
DCT = Namespace("http://purl.org/dc/terms/")

ERROR_SHAPE = URIRef("http://lblod.data.gift/shapes/ErrorShape")
THRESHOLD_MONITOR = "https://w3id.org/rdf-connect/threshold-monitor#ThresholdMonitorJs"
SPARQL_INGEST = "https://w3id.org/rdf-connect#SPARQLIngest"
ALERT_STORE = "https://dishacled.github.io/demo#AlertStore"

ALERT_GRAPH = "http://mu.semte.ch/graphs/errors"


@pytest.fixture(scope="module")
def catalog() -> ContractCatalog:
    # from_file rather than default(): default() is lru_cached process-wide
    return ContractCatalog.from_file(DEFAULT_CONTRACTS_PATH)


# The two graph fixtures below are deliberately function-scoped. `pyshacl.validate`
# writes back into the graphs it is handed -- it adds two triples to the shapes
# graph -- so a shared instance would carry those into later tests and make the
# isomorphism check against upstream fail. Re-parsing per test costs milliseconds.


@pytest.fixture()
def alerts() -> Graph:
    graph = Graph()
    graph.parse(DEFAULT_ALERTS_PATH, format="turtle")
    return graph


@pytest.fixture()
def shape_graph() -> Graph:
    """The ErrorShape as a self-contained graph, taken from the catalog.

    Deliberately not re-authored here: the whole point of the contract living
    in contracts.ttl is that the fixture is validated against that one copy.
    """
    source = Graph()
    source.parse(DEFAULT_CONTRACTS_PATH, format="turtle")
    return extract_shape_graph(source, ERROR_SHAPE)


def _subjects(graph: Graph) -> list:
    return sorted(graph.subjects(RDF.type, OSLC.Error), key=str)


class TestAlertContract:
    """The shape is declared once, in the catalog, and wired to components."""

    def test_the_shape_is_in_the_catalog_under_its_published_iri(self, shape_graph):
        assert len(shape_graph) > 0
        assert (ERROR_SHAPE, RDF.type, URIRef(
            "http://www.w3.org/ns/shacl#NodeShape")) in shape_graph

    def test_the_shape_targets_oslc_error(self, shape_graph):
        target = shape_graph.value(
            ERROR_SHAPE, URIRef("http://www.w3.org/ns/shacl#targetClass")
        )
        assert target == OSLC.Error

    def test_the_shape_declares_all_seven_upstream_properties(self, shape_graph):
        paths = {
            str(o)
            for o in shape_graph.objects(
                predicate=URIRef("http://www.w3.org/ns/shacl#path")
            )
        }
        assert paths == {
            str(MU.uuid),
            str(DCT.subject),
            str(OSLC.message),
            str(DCT.created),
            str(DCT.creator),
            str(OSLC.largePreview),
            str(DCT.references),
        }

    def test_the_threshold_monitor_produces_alerts(self, catalog):
        contract = catalog.get(THRESHOLD_MONITOR)
        assert contract is not None
        assert contract.output_shape is not None
        assert contract.output_shape.iri == str(ERROR_SHAPE)

    def test_sparql_ingest_consumes_alerts(self, catalog):
        contract = catalog.get(SPARQL_INGEST)
        assert contract is not None
        assert contract.input_shape is not None
        assert contract.input_shape.iri == str(ERROR_SHAPE)

    def test_producer_and_consumer_agree(self, catalog):
        """The chain threshold-monitor -> sparql-ingest is compatible."""
        producer = catalog.get(THRESHOLD_MONITOR)
        consumer = catalog.get(SPARQL_INGEST)
        assert producer.output_shape.iri == consumer.input_shape.iri

    def test_a_measurement_producer_does_not_satisfy_the_alert_consumer(self, catalog):
        """...and feeding it measurements instead does not."""
        measurements = catalog.get("https://dishacled.github.io/demo#ThresholdMonitorCm")
        consumer = catalog.get(SPARQL_INGEST)
        assert measurements.output_shape.iri != consumer.input_shape.iri

    def test_the_alert_store_is_an_output_only_dataset(self, catalog):
        store = catalog.get(ALERT_STORE)
        assert store is not None
        assert store.kind == "dataset"
        assert store.output_shape.iri == str(ERROR_SHAPE)
        assert store.input_shape is None
        assert store.config_shape is None

    def test_overlaid_components_declare_a_landing_page(self, catalog):
        """The two real repositories point at themselves; the store does not."""
        assert catalog.get(THRESHOLD_MONITOR).landing_page == (
            "https://github.com/rdf-connect/threshhold-monitor-processor"
        )
        assert catalog.get(SPARQL_INGEST).landing_page == (
            "https://github.com/rdf-connect/sparql-ingest-processor-ts"
        )
        assert catalog.get(ALERT_STORE).landing_page is None

    def test_overlaid_components_carry_no_deployment_coordinates(self, catalog):
        """An empty deployment is what lets the repository's own manifest win."""
        for iri in (THRESHOLD_MONITOR, SPARQL_INGEST):
            deployment = catalog.get(iri).deployment
            assert deployment.packages == ()
            assert deployment.imports == ()


class TestAlertData:
    def test_the_fixture_parses(self, alerts):
        assert len(alerts) > 0

    def test_it_holds_four_alerts(self, alerts):
        assert len(_subjects(alerts)) == 4

    def test_every_alert_is_addressable(self, alerts):
        """IRIs, not blank nodes -- see the fixture header and open question 5."""
        for subject in _subjects(alerts):
            assert isinstance(subject, URIRef), f"{subject} is not addressable"
            assert not isinstance(subject, BNode)

    def test_every_alert_has_a_distinct_uuid(self, alerts):
        uuids = [str(o) for o in alerts.objects(predicate=MU.uuid)]
        assert len(uuids) == 4
        assert len(set(uuids)) == 4

    def test_the_uuid_matches_the_alert_iri(self, alerts):
        """So a consumer can go from a uuid to a resource without a lookup."""
        for subject in _subjects(alerts):
            uuid = alerts.value(subject, MU.uuid)
            assert str(subject).endswith(str(uuid))

    def test_every_alert_is_attributed_to_the_threshold_monitor(self, alerts):
        for subject in _subjects(alerts):
            assert alerts.value(subject, DCT.subject) == Literal("threshold-monitor")

    def test_every_timestamp_is_a_typed_datetime(self, alerts):
        for subject in _subjects(alerts):
            created = alerts.value(subject, DCT.created)
            assert created.datatype == XSD.dateTime
            # raises if the lexical form is not a valid xsd:dateTime
            assert created.toPython().year == 2026

    def test_every_creator_is_an_iri(self, alerts):
        for subject in _subjects(alerts):
            assert isinstance(alerts.value(subject, DCT.creator), URIRef)

    def test_one_alert_omits_both_optional_properties(self, alerts):
        """A consumer that assumes they are always present must fail here."""
        bare = [
            s
            for s in _subjects(alerts)
            if alerts.value(s, OSLC.largePreview) is None
            and alerts.value(s, DCT.references) is None
        ]
        assert len(bare) == 1

    def test_both_units_of_the_demo_story_appear(self, alerts):
        details = " ".join(str(o) for o in alerts.objects(predicate=OSLC.largePreview))
        assert " cm," in details
        assert " mm," in details


class TestShaclConformance:
    def test_the_sample_alerts_conform_to_the_shape(self, alerts, shape_graph):
        conforms, _, report = validate(
            alerts, shacl_graph=shape_graph, advanced=True, inference="none"
        )
        assert conforms, report

    def test_an_alert_missing_a_required_property_does_not_conform(
        self, alerts, shape_graph
    ):
        """Negative control: proves the check above is not passing vacuously."""
        broken = Graph()
        for triple in alerts:
            broken.add(triple)
        subject = _subjects(broken)[0]
        broken.remove((subject, OSLC.message, None))

        conforms, _, report = validate(
            broken, shacl_graph=shape_graph, advanced=True, inference="none"
        )
        assert not conforms
        assert "message" in report

    def test_a_blank_node_alert_would_still_conform(self, shape_graph):
        """The IRI choice is ours, not the shape's -- worth being explicit.

        `ex:ErrorShape` constrains properties, not node kind, so it cannot be
        what stops the processor emitting blank nodes. That is why the
        blank-node/IRI mismatch is reported upstream rather than validated for.
        """
        blank = Graph()
        node = BNode()
        blank.add((node, RDF.type, OSLC.Error))
        blank.add((node, MU.uuid, Literal("no-iri")))
        blank.add((node, DCT.subject, Literal("threshold-monitor")))
        blank.add((node, OSLC.message, Literal("m")))
        blank.add((node, DCT.created, Literal("2026-01-01T00:00:00Z",
                                              datatype=XSD.dateTime)))
        blank.add((node, DCT.creator, URIRef("https://example.org/a")))

        conforms, _, _ = validate(blank, shacl_graph=shape_graph, advanced=True)
        assert conforms


# --------------------------------------------------------------------------
# The queries documented in docs/alert-fixture.md, verbatim.
#
# These strings are the single source of truth: the docs quote them, the tests
# below run them against the fixture file, and TestLiveEndpoint sends these
# same strings to the running triplestore. They are scoped with
# `GRAPH <...>` exactly as documented, which is why the tests query a Dataset
# with the alerts loaded into that named graph rather than a plain Graph --
# otherwise the documented text and the tested text would quietly diverge.
# --------------------------------------------------------------------------

CONSTRUCT_ALL = """
PREFIX oslc: <http://open-services.net/ns/core#>

CONSTRUCT { ?alert ?p ?o }
WHERE {
  GRAPH <http://mu.semte.ch/graphs/errors> {
    ?alert a oslc:Error ; ?p ?o .
  }
}
"""

SELECT_BY_UUID = """
PREFIX oslc: <http://open-services.net/ns/core#>
PREFIX mu:   <http://mu.semte.ch/vocabularies/core/>
PREFIX dct:  <http://purl.org/dc/terms/>

SELECT ?alert ?subject ?message ?created ?creator ?detail ?reference
WHERE {
  GRAPH <http://mu.semte.ch/graphs/errors> {
    ?alert a oslc:Error ;
           mu:uuid %(uuid)s ;
           dct:subject ?subject ;
           oslc:message ?message ;
           dct:created ?created ;
           dct:creator ?creator .
    OPTIONAL { ?alert oslc:largePreview ?detail }
    OPTIONAL { ?alert dct:references ?reference }
  }
}
"""

SELECT_RECENT_FIRST = """
PREFIX oslc: <http://open-services.net/ns/core#>
PREFIX dct:  <http://purl.org/dc/terms/>

SELECT ?alert ?message ?created
WHERE {
  GRAPH <http://mu.semte.ch/graphs/errors> {
    ?alert a oslc:Error ; oslc:message ?message ; dct:created ?created .
  }
}
ORDER BY DESC(?created)
"""

COUNT_ALERTS = """
SELECT (COUNT(*) AS ?n)
FROM <http://mu.semte.ch/graphs/errors>
WHERE { ?s a <http://open-services.net/ns/core#Error> }
"""

KNOWN_UUID = "2f8c1d94-5a3b-4e7f-9c21-6b0d8e4a1f37"
BARE_UUID = "47d9b0e5-6a28-4c31-8f5b-92e0a7c46d13"


@pytest.fixture()
def dataset(alerts) -> Dataset:
    """The fixture loaded into the named graph the endpoint serves it from."""
    store = Dataset()
    named = store.graph(URIRef(ALERT_GRAPH))
    for triple in alerts:
        named.add(triple)
    return store


class TestDocumentedQueries:
    """The queries in docs/alert-fixture.md, run for real.

    Keeps the documentation from drifting away from the data it describes.
    """

    def test_construct_returns_every_alert(self, dataset):
        constructed = Graph()
        for triple in dataset.query(CONSTRUCT_ALL):
            constructed.add(triple)
        assert len(_subjects(constructed)) == 4

    def test_construct_returns_the_whole_graph(self, dataset, alerts):
        """"Full graph" in the acceptance criteria means nothing is left behind."""
        constructed = Graph()
        for triple in dataset.query(CONSTRUCT_ALL):
            constructed.add(triple)
        assert set(constructed) == set(alerts)

    def test_count_matches(self, dataset):
        rows = list(dataset.query(COUNT_ALERTS))
        assert int(rows[0][0]) == 4

    def test_select_by_uuid_returns_exactly_one_alert(self, dataset):
        rows = list(dataset.query(SELECT_BY_UUID % {"uuid": f'"{KNOWN_UUID}"'}))
        assert len(rows) == 1
        assert str(rows[0].subject) == "threshold-monitor"
        assert str(rows[0].alert).endswith(KNOWN_UUID)
        assert rows[0].detail is not None
        assert rows[0].reference is not None

    def test_select_by_uuid_still_returns_an_alert_without_optionals(self, dataset):
        rows = list(dataset.query(SELECT_BY_UUID % {"uuid": f'"{BARE_UUID}"'}))
        assert len(rows) == 1
        assert rows[0].detail is None
        assert rows[0].reference is None

    def test_an_unknown_uuid_returns_nothing(self, dataset):
        rows = list(dataset.query(SELECT_BY_UUID % {"uuid": '"no-such-uuid"'}))
        assert rows == []

    def test_recent_first_orders_newest_to_oldest(self, dataset):
        rows = list(dataset.query(SELECT_RECENT_FIRST))
        assert len(rows) == 4
        timestamps = [r.created.toPython() for r in rows]
        assert timestamps == sorted(timestamps, reverse=True)


@pytest.mark.skipif(
    not os.getenv("ALERT_SPARQL_ENDPOINT"),
    reason="set ALERT_SPARQL_ENDPOINT to check the running triplestore",
)
class TestLiveEndpoint:
    """Gated: runs against the triplestore started by docker compose.

    Skipped by default so the suite keeps working offline, the same way the
    toolchain-generator tests are gated on TOOLCHAIN_SPECIFICATION_PATH.
    """

    @pytest.fixture()
    def endpoint(self) -> str:
        return os.environ["ALERT_SPARQL_ENDPOINT"]

    def _query(self, endpoint: str, query: str, accept: str):
        import requests

        response = requests.get(
            endpoint, params={"query": query}, headers={"Accept": accept}, timeout=30
        )
        response.raise_for_status()
        return response

    def _served_graph(self, endpoint: str) -> Graph:
        response = self._query(endpoint, CONSTRUCT_ALL, "text/turtle")
        served = Graph()
        served.parse(data=response.text, format="turtle")
        return served

    def test_the_endpoint_answers_without_credentials(self, endpoint):
        response = self._query(
            endpoint, COUNT_ALERTS, "application/sparql-results+json"
        )
        assert response.status_code == 200

    def test_the_named_graph_holds_the_sample_alerts(self, endpoint, alerts):
        response = self._query(
            endpoint, COUNT_ALERTS, "application/sparql-results+json"
        )
        count = int(response.json()["results"]["bindings"][0]["n"]["value"])
        assert count == len(_subjects(alerts))

    def test_the_served_graph_matches_the_fixture_file(self, endpoint, alerts):
        assert set(self._served_graph(endpoint)) == set(alerts)

    def test_the_served_alerts_conform_to_the_shape(self, endpoint, shape_graph):
        served = self._served_graph(endpoint)
        conforms, _, report = validate(served, shacl_graph=shape_graph, advanced=True)
        assert conforms, report

    def test_lookup_by_uuid_works_over_http(self, endpoint):
        """The acceptance criterion's "queryable by id", against the real endpoint."""
        response = self._query(
            endpoint,
            SELECT_BY_UUID % {"uuid": f'"{KNOWN_UUID}"'},
            "application/sparql-results+json",
        )
        bindings = response.json()["results"]["bindings"]
        assert len(bindings) == 1
        assert bindings[0]["alert"]["value"].endswith(KNOWN_UUID)


@pytest.mark.skipif(
    not os.getenv("THRESHOLD_MONITOR_PROCESSOR_TTL"),
    reason="set THRESHOLD_MONITOR_PROCESSOR_TTL to check the shape against upstream",
)
class TestUpstreamShapeMatches:
    """Gated: the catalog's copy of ErrorShape against the published one.

    Point THRESHOLD_MONITOR_PROCESSOR_TTL at a checkout or download of
    https://github.com/rdf-connect/threshhold-monitor-processor/blob/master/processor.ttl
    to detect drift. Deliberately not a network call in the default suite.
    """

    def test_our_copy_is_the_published_shape(self, shape_graph):
        upstream_source = Graph()
        upstream_source.parse(
            Path(os.environ["THRESHOLD_MONITOR_PROCESSOR_TTL"]), format="turtle"
        )
        upstream = extract_shape_graph(upstream_source, ERROR_SHAPE)

        assert len(upstream) > 0, "no ErrorShape found in the upstream file"
        assert upstream.isomorphic(shape_graph)
