"""Tests for publishing Elody's component descriptions into the store.

A pipeline definition names its components; something has to say what those
names are. Until now that was a fragment carried inside every definition Elody
exported, which describes the same component once per pipeline and leaves a
discovery service reading the store with no components to find. So the
descriptions are published as well, in the catalog graph.

Three decisions are asserted here rather than only documented:

* **one named graph per component**, replaced with a Graph Store Protocol PUT,
  which is what makes a publish idempotent -- publishing twice leaves the store
  exactly as publishing once does.
* **Elody does not own the catalog.** A component the toolchain catalog already
  describes is not described again, and a description Elody published before the
  toolchain caught up is withdrawn.
* **the fragment and the published graph are the same description**, built by
  the same serializer, so a definition without a fragment loses nothing that
  the catalog graph does not supply.
"""

from urllib.parse import quote

import pytest
import requests
from rdflib import Graph, Namespace, RDF, RDFS, URIRef

from apps.dishacled.pipeline import catalog, publication
from apps.dishacled.serializers.pipeline_definition_serializer import (
    PipelineDefinitionSerializer,
)

from tests.test_pipeline_publication import (
    COMPONENTS,
    POLLER_CM,
    SINK_CM,
    UNTYPED,
    VALID,
    Call,
)
from tests.test_validation import DEMO, make_pipeline


TCS = Namespace("https://w3id.org/toolchain#")
RDFC = Namespace("https://w3id.org/rdf-connect#")
DCAT = Namespace("http://www.w3.org/ns/dcat#")
DCT = Namespace("http://purl.org/dc/terms/")
OWL = Namespace("http://www.w3.org/2002/07/owl#")

ENDPOINT = "http://triplestore.local:3030/store/data"
QUERY_ENDPOINT = "http://triplestore.local:3030/store/sparql"
CATALOG_GRAPH = "http://mu.semte.ch/graphs/catalog"
BASE_URI = "https://elody.local"

POLLER_IRI = URIRef(f"{DEMO}PollerCm")
POLLER_GRAPH = f"{CATALOG_GRAPH}/{quote(str(POLLER_IRI), safe='')}"


class FakeStore:
    """The store's Graph Store Protocol and query endpoints.

    `ask` is what the ownership check gets back: the store either does or does
    not already describe a component outside Elody's graphs.
    """

    def __init__(self, status=204, error=None, ask=False):
        self.status = status
        self.error = error
        self.ask = ask
        self.calls: list[Call] = []
        self.queries: list[str] = []
        self.exceptions = requests.exceptions

    def _record(
        self, method, url, params=None, data=None, headers=None, auth=None, timeout=None
    ):
        if url == QUERY_ENDPOINT:
            self.queries.append((data or {}).get("query", ""))
            if self.error:
                raise self.error
            return self._response(200, {"boolean": self.ask})

        self.calls.append(Call(method, url, params, data, headers, auth))
        if self.error:
            raise self.error
        return self._response(self.status, {})

    def _response(self, status, payload):
        class Response:
            status_code = status
            text = ""

            def json(self):
                return payload

        return Response()

    def put(self, url, **kwargs):
        return self._record("PUT", url, **kwargs)

    def post(self, url, **kwargs):
        return self._record("POST", url, **kwargs)

    def delete(self, url, **kwargs):
        return self._record("DELETE", url, **kwargs)

    def of(self, method) -> list[Call]:
        return [call for call in self.calls if call.method == method]


@pytest.fixture
def store(monkeypatch):
    fake = FakeStore()
    monkeypatch.setattr(catalog, "requests", fake)
    monkeypatch.setenv("CATALOG_GSP_ENDPOINT", ENDPOINT)
    monkeypatch.setenv("CATALOG_SPARQL_ENDPOINT", QUERY_ENDPOINT)
    monkeypatch.setenv("CATALOG_GRAPH", CATALOG_GRAPH)
    for name in (
        "CATALOG_STORE_USER",
        "CATALOG_STORE_PASSWORD",
        "PIPELINE_STORE_USER",
        "PIPELINE_STORE_PASSWORD",
    ):
        # the suite also runs inside the container, where these are configured
        monkeypatch.delenv(name, raising=False)
    return fake


def published(store, graph=POLLER_GRAPH) -> Graph:
    call = next(c for c in store.of("PUT") if c.graph == graph)
    return call.parsed()


class TestOneGraphPerComponent:
    def test_a_component_is_published_into_its_own_graph(self, store):
        assert catalog.publish_component(POLLER_CM) == catalog.PUBLISHED

        call = store.of("PUT")[0]
        assert (call.method, call.url) == ("PUT", ENDPOINT)
        assert call.graph == POLLER_GRAPH
        assert call.headers["Content-Type"].startswith("text/turtle")

    def test_the_graph_is_named_after_the_component_iri(self, store):
        # the only identifier every component has: a repository and a contract
        # entry are joined on it, and it survives a repository being renamed
        assert POLLER_GRAPH.startswith(f"{CATALOG_GRAPH}/")
        assert quote(str(POLLER_IRI), safe="") in POLLER_GRAPH
        assert " " not in POLLER_GRAPH and "#" not in POLLER_GRAPH

    def test_republishing_replaces_rather_than_appends(self, store):
        catalog.publish_component(POLLER_CM)
        first = published(store)

        catalog.publish_component(POLLER_CM)
        second = store.of("PUT")[1]

        assert second.graph == POLLER_GRAPH
        assert first.isomorphic(second.parsed())

    def test_publishing_a_set_publishes_each_component_once(self, store):
        outcomes = catalog.publish_components(list(COMPONENTS.values()) + [POLLER_CM])

        assert outcomes == {catalog.PUBLISHED: len(COMPONENTS)}
        assert len({call.graph for call in store.of("PUT")}) == len(COMPONENTS)

    def test_a_component_can_be_withdrawn(self, store):
        assert catalog.unpublish_component(POLLER_IRI) is True

        call = store.of("DELETE")[0]
        assert call.graph == POLLER_GRAPH


class TestWhatIsPublished:
    def test_the_component_is_described_in_discovery_vocabulary(self, store):
        catalog.publish_component(POLLER_CM)
        graph = published(store)

        assert (POLLER_IRI, RDF.type, TCS.PipelineComponent) in graph
        assert (POLLER_IRI, RDF.type, DCAT.Resource) in graph
        assert graph.value(POLLER_IRI, RDFS.label) is not None

    def test_it_carries_the_elody_id_it_is_addressed_by(self, store):
        catalog.publish_component(POLLER_CM)

        assert str(published(store).value(POLLER_IRI, DCT.identifier)) == "poller-cm"

    def test_it_carries_the_implementation_join_key(self, store):
        # `rdfc:jsImplementationOf rdfc:Processor` is how a repository and its
        # catalog entry are matched; a consumer reading the graph on its own has
        # no processor file to read it off
        catalog.publish_component(POLLER_CM)

        assert (POLLER_IRI, RDFC.jsImplementationOf, RDFC.Processor) in published(store)

    def test_it_carries_the_shapes_in_their_roles(self, store):
        catalog.publish_component(POLLER_CM)
        graph = published(store)

        roles = {
            graph.value(relation, DCAT.hadRole)
            for relation in graph.objects(POLLER_IRI, DCAT.qualifiedRelation)
        }
        assert TCS.outputShape in roles
        assert TCS.configShape in roles

    def test_a_shape_travels_as_a_self_contained_sub_graph(self, store):
        catalog.publish_component(POLLER_CM)
        graph = published(store)

        shape = next(
            graph.value(relation, DCT.relation)
            for relation in graph.objects(POLLER_IRI, DCAT.qualifiedRelation)
            if graph.value(relation, DCAT.hadRole) == TCS.outputShape
        )
        assert len(list(graph.predicate_objects(shape))) > 0

    def test_it_says_which_runner_it_needs_and_what_that_needs(self, store):
        catalog.publish_component(POLLER_CM)
        graph = published(store)

        assert (POLLER_IRI, DCT.requires, RDFC.NodeRunner) in graph
        assert (RDFC.NodeRunner, DCT.requires, RDFC.Orchestrator) in graph

    def test_a_relative_import_is_resolved_the_way_the_generator_resolves_it(
        self, store
    ):
        """A store cannot hold `<./node_modules/...>`.

        Left to itself it would resolve the reference against the endpoint's
        own URL, which names nothing -- the same reason a published definition
        is resolved first (`publication.IMPORT_BASE`), and it has to be the
        same reading, or the catalog and a definition name two different files.
        """
        catalog.publish_component(SINK_CM)
        graph = published(store, f"{CATALOG_GRAPH}/{quote(f'{DEMO}SinkCm', safe='')}")

        imports = [str(o) for o in graph.objects(None, OWL.imports)]
        assert imports == [
            "file:///workspace/pipeline/node_modules/@demo/sink/processors.ttl"
        ]

    def test_the_import_base_is_configuration(self, store, monkeypatch):
        monkeypatch.setenv("PIPELINE_IMPORT_BASE", "file:///elsewhere/")
        catalog.publish_component(SINK_CM)
        graph = published(store, f"{CATALOG_GRAPH}/{quote(f'{DEMO}SinkCm', safe='')}")

        assert [str(o) for o in graph.objects(None, OWL.imports)] == [
            "file:///elsewhere/node_modules/@demo/sink/processors.ttl"
        ]

    def test_a_dataset_is_published_as_a_dataset(self, store):
        # a source of data, not a deployable component: calling it a
        # tcs:PipelineComponent would offer the generator a step it cannot start
        dataset = {
            "_id": "local--sensor-feed",
            "type": "githubProcessor",
            "metadata": [{"key": "name", "value": "Sensor feed"}],
            "data": {
                "componentIri": f"{DEMO}SensorFeedCm",
                "componentKind": "dataset",
                "outputShape": COMPONENTS["poller-cm"]["data"]["outputShape"],
            },
        }
        assert catalog.publish_component(dataset) == catalog.PUBLISHED

        iri = URIRef(f"{DEMO}SensorFeedCm")
        graph = published(store, f"{CATALOG_GRAPH}/{quote(str(iri), safe='')}")
        assert (iri, RDF.type, DCAT.Dataset) in graph
        assert (iri, RDF.type, TCS.PipelineComponent) not in graph
        assert str(graph.value(iri, DCT.identifier)) == "local--sensor-feed"
        assert RDFC.NodeRunner not in set(graph.objects(iri, DCT.requires))
        assert TCS.outputShape in {
            graph.value(relation, DCAT.hadRole)
            for relation in graph.objects(iri, DCAT.qualifiedRelation)
        }

    def test_a_component_without_an_iri_is_not_published(self, store):
        assert catalog.publish_component({"_id": "x", "data": {}}) == catalog.SKIPPED
        assert store.calls == []


class TestOwnership:
    """Elody adds what the toolchain catalog lacks, and nothing else."""

    def test_a_component_the_toolchain_catalog_describes_is_left_alone(self, store):
        store.ask = True

        assert catalog.publish_component(POLLER_CM) == catalog.WITHDRAWN
        assert store.of("PUT") == []

    def test_and_an_earlier_elody_description_of_it_is_withdrawn(self, store):
        store.ask = True
        catalog.publish_component(POLLER_CM)

        assert store.of("DELETE")[0].graph == POLLER_GRAPH

    def test_the_check_ignores_elody_s_own_graphs(self, store):
        catalog.publish_component(POLLER_CM)

        query = store.queries[0]
        assert "ASK" in query
        assert f'STRSTARTS(STR(?g), "{CATALOG_GRAPH}/")' in query
        assert str(POLLER_IRI) in query
        # a dataset is described as a dcat:Dataset, and the question is whether
        # anything outside Elody's graphs describes this thing at all
        assert "tcs:PipelineComponent dcat:Dataset" in query

    def test_without_a_query_endpoint_the_component_is_published(
        self, store, monkeypatch
    ):
        # a duplicate in a graph of its own is recoverable; a component the
        # catalog does not describe is a pipeline that does not compile
        monkeypatch.delenv("CATALOG_SPARQL_ENDPOINT", raising=False)
        monkeypatch.delenv("SPARQL_ENDPOINT", raising=False)

        assert catalog.publish_component(POLLER_CM) == catalog.PUBLISHED
        assert store.queries == []

    def test_a_store_that_cannot_answer_the_check_does_not_block_publishing(
        self, monkeypatch
    ):
        fake = FakeStore(error=requests.exceptions.ConnectionError("down"))
        monkeypatch.setattr(catalog, "requests", fake)
        monkeypatch.setenv("CATALOG_GSP_ENDPOINT", ENDPOINT)
        monkeypatch.setenv("CATALOG_SPARQL_ENDPOINT", QUERY_ENDPOINT)
        monkeypatch.setenv("CATALOG_GRAPH", CATALOG_GRAPH)

        assert catalog.publish_component(POLLER_CM) == catalog.FAILED


class TestConfiguration:
    def test_nothing_is_published_without_a_catalog_graph(self, store, monkeypatch):
        monkeypatch.delenv("CATALOG_GRAPH", raising=False)

        assert catalog.is_configured() is False
        assert catalog.publish_component(POLLER_CM) == catalog.SKIPPED
        assert store.calls == []

    def test_nothing_is_published_without_an_endpoint(self, store, monkeypatch):
        monkeypatch.delenv("CATALOG_GSP_ENDPOINT", raising=False)
        monkeypatch.delenv("PIPELINE_GSP_ENDPOINT", raising=False)

        assert catalog.publish_component(POLLER_CM) == catalog.SKIPPED
        assert store.calls == []

    def test_the_pipeline_store_configuration_is_the_default(self, store, monkeypatch):
        # one store, usually one dataset: the catalog does not need its own
        # endpoint unless it is somewhere else
        monkeypatch.delenv("CATALOG_GSP_ENDPOINT", raising=False)
        monkeypatch.setenv("PIPELINE_GSP_ENDPOINT", ENDPOINT)

        assert catalog.publish_component(POLLER_CM) == catalog.PUBLISHED
        assert store.of("PUT")[0].url == ENDPOINT

    def test_the_write_is_anonymous_until_credentials_are_configured(self, store):
        catalog.publish_component(POLLER_CM)

        assert store.of("PUT")[0].auth is None

    def test_a_protected_store_is_written_to_as_the_configured_user(
        self, store, monkeypatch
    ):
        monkeypatch.setenv("CATALOG_STORE_USER", "admin")
        monkeypatch.setenv("CATALOG_STORE_PASSWORD", "secret")

        catalog.publish_component(POLLER_CM)

        assert store.of("PUT")[0].auth == ("admin", "secret")

    def test_a_refusing_store_reports_rather_than_raises(self, monkeypatch):
        fake = FakeStore(status=403)
        monkeypatch.setattr(catalog, "requests", fake)
        monkeypatch.setenv("CATALOG_GSP_ENDPOINT", ENDPOINT)
        monkeypatch.setenv("CATALOG_GRAPH", CATALOG_GRAPH)
        monkeypatch.delenv("CATALOG_SPARQL_ENDPOINT", raising=False)
        monkeypatch.delenv("SPARQL_ENDPOINT", raising=False)

        assert catalog.publish_component(POLLER_CM) == catalog.FAILED

    def test_an_unreachable_store_reports_rather_than_raises(self, monkeypatch):
        fake = FakeStore(error=requests.exceptions.ConnectionError("down"))
        monkeypatch.setattr(catalog, "requests", fake)
        monkeypatch.setenv("CATALOG_GSP_ENDPOINT", ENDPOINT)
        monkeypatch.setenv("CATALOG_GRAPH", CATALOG_GRAPH)
        monkeypatch.delenv("CATALOG_SPARQL_ENDPOINT", raising=False)
        monkeypatch.delenv("SPARQL_ENDPOINT", raising=False)

        assert catalog.publish_component(POLLER_CM) == catalog.FAILED


class TestPublishedWithAPipeline:
    """Saving a pipeline publishes the components it names.

    That is what makes its definition resolvable: a step names a component by
    IRI, and until the store describes that IRI the reference is dangling.
    """

    @pytest.fixture
    def pipeline_store(self, store, monkeypatch):
        pipeline_fake = FakeStore()
        monkeypatch.setattr(publication, "requests", pipeline_fake)
        monkeypatch.setenv("PIPELINE_GSP_ENDPOINT", ENDPOINT)
        monkeypatch.setenv("PIPELINE_GRAPH", "http://mu.semte.ch/graphs/pipelines")
        monkeypatch.setenv("PIPELINE_EXPORT_BASE_URI", BASE_URI)
        monkeypatch.delenv("PIPELINE_PUBLISH_INVALID", raising=False)
        return pipeline_fake

    def test_saving_a_pipeline_publishes_its_components(self, store, pipeline_store):
        assert (
            publication.publish_pipeline(make_pipeline(VALID), components=COMPONENTS)
            is True
        )

        graphs = {call.graph for call in store.of("PUT")}
        assert POLLER_GRAPH in graphs
        assert f"{CATALOG_GRAPH}/{quote(f'{DEMO}SinkCm', safe='')}" in graphs

    def test_a_catalog_store_that_is_down_does_not_break_the_save(
        self, monkeypatch, pipeline_store
    ):
        monkeypatch.setattr(
            catalog, "requests", FakeStore(error=requests.exceptions.ConnectionError())
        )
        monkeypatch.setenv("CATALOG_GSP_ENDPOINT", ENDPOINT)
        monkeypatch.setenv("CATALOG_GRAPH", CATALOG_GRAPH)

        assert (
            publication.publish_pipeline(make_pipeline(VALID), components=COMPONENTS)
            is True
        )

    def test_nothing_is_published_when_no_catalog_graph_is_configured(
        self, monkeypatch, pipeline_store
    ):
        fake = FakeStore()
        monkeypatch.setattr(catalog, "requests", fake)
        monkeypatch.delenv("CATALOG_GRAPH", raising=False)

        publication.publish_pipeline(make_pipeline(VALID), components=COMPONENTS)

        assert fake.calls == []


class TestTheFragmentAndTheGraphAgree:
    """The definition fragment and the catalog graph are one description.

    They are built by the same serializer, which is what lets the fragment go
    away: a definition that names components by IRI alone plus the catalog
    graphs holds exactly what the definition-with-fragment held.

    Both are read with the base the generator reads them with, which is also
    the base the store holds the catalog graph at -- a downloaded definition
    keeps its `owl:imports` relative, and comparing the two under any other
    base compares two different files.
    """

    IMPORT_BASE = "file:///workspace/pipeline/"

    def _fragment(self, include_catalog):
        ttl = PipelineDefinitionSerializer(
            base_uri=f"{BASE_URI}/pipelines/pipeline-1/",
            include_catalog=include_catalog,
        ).serialize(make_pipeline(VALID), COMPONENTS)
        graph = Graph()
        graph.parse(data=ttl, format="turtle", publicID=self.IMPORT_BASE)
        return graph

    def _catalog_graphs(self, documents):
        graph = Graph()
        for document in documents:
            graph.parse(
                data=catalog.component_turtle(document),
                format="turtle",
                publicID=self.IMPORT_BASE,
            )
        return graph

    def test_a_definition_without_the_fragment_names_components_by_iri_only(self):
        graph = self._fragment(include_catalog=False)

        assert (POLLER_IRI, None, None) not in graph
        assert POLLER_IRI in set(graph.objects(None, None))

    def test_the_two_together_hold_what_the_fragment_held(self):
        with_fragment = self._fragment(include_catalog=True)
        without = self._fragment(include_catalog=False)
        used = [COMPONENTS["poller-cm"], COMPONENTS["sink-cm"]]

        recomposed = without + self._catalog_graphs(used)

        assert recomposed.isomorphic(with_fragment)

    def test_a_component_nobody_used_can_still_be_published(self):
        # the fragment can only describe a component a pipeline names; a
        # discovery service wants the ones nobody has used yet as well
        graph = self._catalog_graphs([UNTYPED])

        assert (URIRef(f"{DEMO}UntypedPoller"), RDF.type, TCS.PipelineComponent) in graph


class TestDiscoverySweep:
    """What one catalog sweep covers.

    A component nobody has used in a pipeline yet is still a component the
    catalog should list, so the sweep publishes what discovery returns rather
    than what the pipelines name. Discovery is a paged GitHub search, which is
    what the rest of this is about.
    """

    class FakeHttpStore:
        def __init__(self, pages, error_at=None):
            self.pages = pages
            self.error_at = error_at
            self.asked = []

        def get_items_from_collection(self, collection, skip=0, limit=20, **kwargs):
            page = skip // limit
            self.asked.append(page)
            if page == self.error_at:
                raise RuntimeError("GitHub is having a day")
            return {"results": self.pages[page] if page < len(self.pages) else []}

    @pytest.fixture
    def discovery(self, monkeypatch):
        def install(pages, error_at=None):
            fake = self.FakeHttpStore(pages, error_at)
            monkeypatch.setattr(
                catalog, "get_storage_mapper", lambda: {"http": lambda: fake}
            )
            return fake

        return install

    @staticmethod
    def _page(count, offset=0):
        return [
            {
                "_id": f"component-{index}",
                "data": {"componentIri": f"{DEMO}Component{index}"},
            }
            for index in range(offset, offset + count)
        ]

    def test_a_full_page_is_followed_by_the_next_one(self, discovery):
        fake = discovery([self._page(20), self._page(3, offset=20)])

        assert len(catalog.discovered_components()) == 23
        assert fake.asked == [0, 1]

    def test_a_short_page_ends_the_sweep(self, discovery):
        fake = discovery([self._page(2)])

        assert len(catalog.discovered_components()) == 2
        assert fake.asked == [0]

    def test_the_number_of_pages_is_configuration(self, discovery, monkeypatch):
        monkeypatch.setenv("CATALOG_PUBLISH_PAGES", "2")
        fake = discovery(
            [
                self._page(20),
                self._page(20, offset=20),
                self._page(20, offset=40),
            ]
        )

        assert len(catalog.discovered_components()) == 40
        assert fake.asked == [0, 1]

    def test_one_component_reached_two_ways_is_listed_once(self, discovery):
        # the same processor arrives as the repository it lives in and as the
        # contract entry that adds shapes to it
        repository = {"_id": "org--proc", "data": {"componentIri": f"{DEMO}Proc"}}
        contract = {"_id": "local--proc", "data": {"componentIri": f"{DEMO}Proc"}}
        discovery([[repository, contract]])

        assert [d["_id"] for d in catalog.discovered_components()] == ["org--proc"]

    def test_discovery_failing_part_way_keeps_what_it_has(self, discovery):
        discovery([self._page(20), self._page(20, offset=20)], error_at=1)

        assert len(catalog.discovered_components()) == 20
