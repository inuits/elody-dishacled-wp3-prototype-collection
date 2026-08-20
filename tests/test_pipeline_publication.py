"""Tests for publishing a pipeline definition into the central triple store.

Saving a pipeline in Elody has to make its `tcs:PipelineDefinition` readable by
the toolchain services without them calling any Elody endpoint, so the store --
not the download route -- is the hand-off. Mongo stays the working store for
editing; the graph is a projection of it.

Two decisions are asserted here rather than only documented:

* **one named graph per pipeline**, replaced with a Graph Store Protocol PUT.
  Republishing is then an atomic replace and deletion is exact, neither of
  which holds for a shared graph: a definition hangs configuration, packages
  and shapes off blank nodes, so "delete the triples of pipeline X" has no
  reliable SPARQL spelling.
* **an invalid chain is not published**, and any earlier version of it is
  removed. That mirrors the 409 the exports answer with: a broken chain does
  not leave the system, and a consumer must not be able to pick up the last
  version that happened to validate.

`publish_pipeline` is now the migration script's entry point and the
publish-once path; the editor's own saves go through the SPARQL storage engine,
which calls the same `definition_for_store` (see `test_pipeline_serializer.py`
and `docs/pipeline-storage.md`).
"""

import pytest
import requests
from rdflib import Graph, Namespace, RDFS, URIRef
from rdflib.namespace import OWL

from apps.dishacled.pipeline import publication
from apps.dishacled.serializers.pipeline_definition_serializer import (
    PipelineDefinitionSerializer,
)

from tests.test_validation import (
    CM,
    DEMO,
    MM,
    connected,
    make_component,
    make_pipeline,
    processor_relation,
    prop,
)


TCS = Namespace("https://w3id.org/toolchain#")

ENDPOINT = "http://triplestore.local:3030/store/data"
GRAPH_BASE = "http://mu.semte.ch/graphs/pipeline-definitions"
BASE_URI = "https://elody.local"

PIPELINE_GRAPH = f"{GRAPH_BASE}/pipeline-1"
PIPELINE_URI = URIRef(f"{BASE_URI}/pipelines/pipeline-1")


# The chain-validation fixtures describe contracts but carry no processor file,
# and a stage the serializer cannot read a config shape off is not a step. So
# the same components are given one here: the published graph is asserted to
# hold steps and channels, not just a bare plan node.
PROCESSOR_TTL = """\
@prefix rdfc: <https://w3id.org/rdf-connect#>.
@prefix sh:   <http://www.w3.org/ns/shacl#>.
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#>.
@prefix demo: <{demo}>.

demo:{cls} rdfc:jsImplementationOf rdfc:Processor.

[] a sh:NodeShape;
  sh:targetClass demo:{cls};
  sh:property [
    sh:class rdfc:Reader; sh:path rdfc:reader; sh:name "input"; sh:maxCount 1;
  ], [
    sh:class rdfc:Writer; sh:path rdfc:writer; sh:name "output"; sh:maxCount 1;
  ].
"""


def component(identifier, class_name, properties, **shapes):
    document = make_component(identifier, class_name, properties, **shapes)
    document["data"]["rawTtl"] = PROCESSOR_TTL.format(demo=DEMO, cls=class_name)
    return document


POLLER_CM = component(
    "poller-cm",
    "PollerCm",
    [prop("url"), prop("output", "rdfc:Writer", required=True)],
    output_shape=CM,
)
POLLER_MM = component(
    "poller-mm",
    "PollerMm",
    [prop("url"), prop("output", "rdfc:Writer", required=True)],
    output_shape=MM,
)
SINK_CM = component(
    "sink-cm",
    "SinkCm",
    [prop("input", "rdfc:Reader", required=True)],
    input_shape=CM,
)
SINK_CM["data"]["deployment"] = {
    # written relative on purpose: it is resolved against where the runner
    # mounts the pipeline, which is not anything Elody can know
    "imports": ["./node_modules/@demo/sink/processors.ttl"],
    "packages": [],
}
UNTYPED = component(
    "untyped-poller",
    "UntypedPoller",
    [prop("output", "rdfc:Writer", required=True)],
)

COMPONENTS = {c["_id"]: c for c in [POLLER_CM, POLLER_MM, SINK_CM, UNTYPED]}

VALID = [
    processor_relation("poller-cm"),
    connected("sink-cm", "input", "poller-cm|output"),
]
# cm expected, mm produced: the chain the 409 exists for
INVALID = [
    processor_relation("poller-mm"),
    connected("sink-cm", "input", "poller-mm|output"),
]


class Call:
    def __init__(self, method, url, params, data, headers, auth):
        self.method = method
        self.url = url
        self.params = params or {}
        self.data = data
        self.headers = headers or {}
        self.auth = auth

    @property
    def graph(self):
        return self.params.get("graph")

    @property
    def body(self) -> str:
        if isinstance(self.data, bytes):
            return self.data.decode("utf-8")
        return self.data or ""

    def parsed(self) -> Graph:
        graph = Graph()
        graph.parse(data=self.body, format="turtle")
        return graph

    def imports(self) -> list[str]:
        return [str(o) for _, _, o in self.parsed().triples((None, OWL.imports, None))]


class FakeStore:
    """Stands in for the triple store's Graph Store Protocol endpoint."""

    def __init__(self, status=204, error=None):
        self.status = status
        self.error = error
        self.calls: list[Call] = []
        self.exceptions = requests.exceptions

    def _record(
        self, method, url, params=None, data=None, headers=None, auth=None, timeout=None
    ):
        self.calls.append(Call(method, url, params, data, headers, auth))
        if self.error:
            raise self.error

        class Response:
            status_code = self.status
            text = ""

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
    monkeypatch.setattr(publication, "requests", fake)
    monkeypatch.setenv("PIPELINE_GSP_ENDPOINT", ENDPOINT)
    monkeypatch.setenv("PIPELINE_GRAPH", GRAPH_BASE)
    monkeypatch.setenv("PIPELINE_EXPORT_BASE_URI", BASE_URI)
    for name in (
        "PIPELINE_PUBLISH_INVALID",
        "PIPELINE_IMPORT_BASE",
        "PIPELINE_STORE_USER",
        "PIPELINE_STORE_PASSWORD",
    ):
        # the suite also runs inside the container, where these are configured
        monkeypatch.delenv(name, raising=False)
    return fake


def publish(relations=VALID, **kwargs):
    return publication.publish_pipeline(
        make_pipeline(relations), components=COMPONENTS, **kwargs
    )


class TestPublishOnSave:
    def test_a_saved_pipeline_is_put_into_its_own_graph(self, store):
        assert publish() is True

        call = store.calls[0]
        assert (call.method, call.url) == ("PUT", ENDPOINT)
        assert call.graph == PIPELINE_GRAPH
        assert call.headers["Content-Type"].startswith("text/turtle")

    def test_the_graph_holds_the_pipeline_definition(self, store):
        publish()

        graph = store.of("PUT")[0].parsed()
        assert (PIPELINE_URI, None, TCS.PipelineDefinition) in graph
        assert len(list(graph.subjects(None, TCS.InstancePipelineComponent))) == 2

    def test_it_is_the_same_document_the_download_route_serves(self, store):
        """The acceptance criterion, stated as the equality it is.

        Whatever `definition.ttl` would answer is what lands in the store, so a
        consumer reading the graph and a consumer downloading the file cannot
        end up with two different pipelines. Both sides are read with the
        toolchain's base, which is the one thing a store forces: RDF has no
        relative IRIs, so `owl:imports` has to be resolved by somebody.
        """
        publish()

        downloaded = Graph()
        downloaded.parse(
            data=PipelineDefinitionSerializer(
                base_uri=f"{BASE_URI}/pipelines/pipeline-1/"
            ).serialize(make_pipeline(VALID), COMPONENTS),
            format="turtle",
            publicID=publication.IMPORT_BASE,
        )
        assert store.of("PUT")[0].parsed().isomorphic(downloaded)

    def test_a_relative_import_is_resolved_the_way_the_generator_resolves_it(
        self, store
    ):
        """A store cannot hold `<./node_modules/...>`.

        Left to itself it would resolve the reference against the endpoint's
        own URL, which names nothing. Resolving it here against the base the
        generator parses with is the only reading that stays true.
        """
        publish()

        assert (
            "file:///workspace/pipeline/node_modules/@demo/sink/processors.ttl"
            in store.of("PUT")[0].imports()
        )

    def test_the_import_base_is_configuration(self, store, monkeypatch):
        monkeypatch.setenv("PIPELINE_IMPORT_BASE", "file:///elsewhere/")

        publish()

        assert (
            "file:///elsewhere/node_modules/@demo/sink/processors.ttl"
            in store.of("PUT")[0].imports()
        )

    def test_republishing_replaces_rather_than_appends(self, store):
        publish()
        publish()

        assert [call.method for call in store.calls] == ["PUT", "PUT"]
        assert {call.graph for call in store.calls} == {PIPELINE_GRAPH}


class TestUnpublishOnDelete:
    def test_deleting_a_pipeline_drops_its_graph(self, store):
        assert publication.unpublish_pipeline(make_pipeline(VALID)) is True

        call = store.calls[0]
        assert (call.method, call.url, call.graph) == ("DELETE", ENDPOINT, PIPELINE_GRAPH)

    def test_a_graph_that_was_never_published_is_not_an_error(self, store):
        store.status = 404

        assert publication.unpublish_pipeline(make_pipeline(VALID)) is True


class TestInvalidChain:
    def test_an_incompatible_pipeline_is_not_published(self, store):
        assert publish(INVALID) is False
        assert store.of("PUT") == []

    def test_and_its_previous_version_is_withdrawn(self, store):
        publish()
        publish(INVALID)

        assert [call.method for call in store.calls] == ["PUT", "DELETE"]
        assert store.of("DELETE")[0].graph == PIPELINE_GRAPH

    def test_publishing_one_anyway_is_a_deliberate_configuration(self, store, monkeypatch):
        """`PIPELINE_PUBLISH_INVALID` is the store's `?force=true`.

        The download route lets a human force a broken export; nobody is there
        to answer for a save, so the same decision is taken once, in
        configuration, and the published graph has to say so itself -- a
        consumer never sees the refusal that would otherwise have been the
        only warning.
        """
        monkeypatch.setenv("PIPELINE_PUBLISH_INVALID", "true")

        assert publish(INVALID) is True

        graph = store.of("PUT")[0].parsed()
        comments = [str(c) for c in graph.objects(PIPELINE_URI, RDFS.comment)]
        assert any("validation" in comment for comment in comments)
        assert any("mm" in comment for comment in comments)

    def test_a_chain_that_cannot_be_checked_still_publishes(self, store):
        """A missing shape is a warning, not a refusal.

        Most components carry no contract at all; refusing those would leave
        the store empty for every pipeline that is merely undescribed rather
        than actually broken.
        """
        assert publish([processor_relation("untyped-poller")]) is True
        assert len(store.of("PUT")) == 1


class TestConfiguration:
    def test_nothing_is_published_without_an_endpoint(self, store, monkeypatch):
        monkeypatch.delenv("PIPELINE_GSP_ENDPOINT")

        assert publish() is False
        assert store.calls == []

    def test_nothing_is_published_without_a_graph_name(self, store, monkeypatch):
        monkeypatch.delenv("PIPELINE_GRAPH")

        assert publish() is False
        assert store.calls == []

    def test_an_id_that_could_forge_a_graph_name_is_refused(self, store):
        pipeline = {**make_pipeline(VALID), "_id": "pipeline 1> <urn:evil"}

        assert publication.publish_pipeline(pipeline, components=COMPONENTS) is False
        assert store.calls == []

    def test_the_write_is_anonymous_until_credentials_are_configured(self, store):
        publish()

        assert store.of("PUT")[0].auth is None

    def test_a_protected_store_is_written_to_as_the_configured_user(
        self, store, monkeypatch
    ):
        """Fuseki restricts `/*/data`, and the shared store will restrict more.

        Reads stay anonymous; only publishing carries credentials.
        """
        monkeypatch.setenv("PIPELINE_STORE_USER", "elody")
        monkeypatch.setenv("PIPELINE_STORE_PASSWORD", "s3cret")

        publish()

        assert store.of("PUT")[0].auth == ("elody", "s3cret")

    def test_a_store_that_refuses_the_write_does_not_break_the_save(self, store):
        store.status = 401

        assert publish() is False

    def test_an_unreachable_store_does_not_break_the_save(self, store, monkeypatch):
        monkeypatch.setattr(
            store,
            "error",
            requests.exceptions.ConnectionError("no route to host"),
        )

        assert publish() is False
