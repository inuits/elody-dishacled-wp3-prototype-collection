"""Discovery reads many files, so it must not read them one after another.

Listing the picker has to know which processors each repository declares, and
that is only in its TTL files: one tree call plus one call per source file per
repository. Done serially that is ~60 round trips for a page of twenty -- ten
seconds against a 150ms API. The work is embarrassingly parallel; nothing about
it needed to be sequential.

These tests pin the two properties that make parallelism safe: the same rows in
the same order, and no repeated fetches.
"""

import base64
import threading
import time
from unittest.mock import MagicMock

import pytest

from apps.dishacled.storage.dishacled_httpstore import DishacledHttpStorageManager


def _ttl(class_name):
    return f"""\
@prefix rdfc: <https://w3id.org/rdf-connect#>.
@prefix sh: <http://www.w3.org/ns/shacl#>.

rdfc:{class_name} rdfc:jsImplementationOf rdfc:Processor.

[ ] a sh:NodeShape;
    sh:targetClass rdfc:{class_name};
    sh:property [ sh:path rdfc:x; sh:name "x" ].
"""


class _Github:
    """A mocked GitHub with a per-call delay and a call log.

    Two TTL files per repository, each declaring one processor, so a page of
    `repo_count` repositories is `repo_count * 2` components.
    """

    def __init__(self, repo_count=20, latency=0.0, failing=()):
        self.latency = latency
        self.failing = set(failing)
        self.calls = []
        self._lock = threading.Lock()
        self.repos = [
            {
                "owner": {"login": "o"},
                "name": f"r{index}",
                "full_name": f"o/r{index}",
                "html_url": "https://github.com/o/r",
                "default_branch": "main",
                "language": "TypeScript",
            }
            for index in range(repo_count)
        ]

    def _repo_name_in(self, url):
        for repo in self.repos:
            if f"/{repo['full_name']}/" in url or url.endswith(repo["full_name"]):
                return repo["name"]
        return None

    def get(self, url, **kwargs):
        with self._lock:
            self.calls.append(url)
        if self.latency:
            time.sleep(self.latency)

        name = self._repo_name_in(url)
        if name in self.failing and "/search/" not in url:
            response = MagicMock()
            response.status_code = 403
            return response

        response = MagicMock()
        response.status_code = 200
        if "/search/repositories" in url:
            response.json.return_value = {
                "items": self.repos,
                "total_count": len(self.repos),
            }
        elif "/git/trees/" in url:
            response.json.return_value = {
                "tree": [{"path": "a.ttl"}, {"path": "b.ttl"}]
            }
        elif url.endswith((".json", ".toml")):
            response.status_code = 404
        elif "/contents/" in url:
            path = url.split("/contents/", 1)[1]
            body = _ttl("A" if path == "a.ttl" else "B")
            response.json.return_value = {
                "content": base64.b64encode(body.encode()).decode(),
                "encoding": "base64",
            }
        else:
            response.json.return_value = next(
                (r for r in self.repos if r["name"] == name), self.repos[0]
            )
        return response

    def store(self):
        store = DishacledHttpStorageManager()
        store.session = MagicMock()
        store.session.get.side_effect = self.get
        return store


def _github_rows(results):
    return [d["_id"] for d in results["results"] if d["_id"].startswith("o--")]


class TestListingIsParallel:
    def test_a_page_is_not_a_serial_chain_of_round_trips(self):
        # 20 repos x (1 tree + 2 files) + 1 search = 61 calls. Serially at 20ms
        # that is 1.2s; the point of parallelism is that the wall clock is
        # nowhere near the sum.
        github = _Github(repo_count=20, latency=0.02)
        start = time.perf_counter()
        results = github.store().get_items_from_collection("githubProcessors")
        elapsed = time.perf_counter() - start

        serial = len(github.calls) * github.latency
        assert len(_github_rows(results)) == 40
        assert elapsed < serial / 3, (
            f"{elapsed:.2f}s for work that is {serial:.2f}s serially"
        )

    def test_the_rows_are_the_same_and_in_the_same_order(self):
        results = _Github(repo_count=5).store().get_items_from_collection(
            "githubProcessors"
        )
        # repositories in the order GitHub ranked them, classes sorted within
        assert _github_rows(results) == [
            f"o--r{index}--{class_name}"
            for index in range(5)
            for class_name in ("A", "B")
        ]

    def test_nothing_is_fetched_twice(self):
        github = _Github(repo_count=5)
        github.store().get_items_from_collection("githubProcessors")
        assert len(github.calls) == len(set(github.calls))

    def test_one_unreachable_repository_does_not_take_the_page_with_it(self):
        github = _Github(repo_count=5, failing={"r2"})
        results = github.store().get_items_from_collection("githubProcessors")
        rows = _github_rows(results)
        assert "o--r1--A" in rows and "o--r3--B" in rows
        # the failing one is still listed, as itself: a repository we could not
        # read is not a repository that is gone
        assert "o--r2" in rows


class TestRelationResolutionIsParallel:
    """Opening a pipeline resolves every step; that is the same fan-out."""

    def test_resolving_a_pipelines_processors_is_not_serial(self):
        github = _Github(repo_count=6, latency=0.02)
        ids = [f"o--r{index}--A" for index in range(6)]
        store = github.store()

        start = time.perf_counter()
        results = store.get_items_from_collection(
            "githubProcessors", filters={"identifiers": ids}
        )
        elapsed = time.perf_counter() - start

        assert [d["_id"] for d in results["results"]] == ids
        assert elapsed < len(github.calls) * github.latency / 2

    def test_the_order_of_the_requested_ids_is_kept(self):
        github = _Github(repo_count=4)
        ids = ["o--r3--B", "o--r0--A", "o--r2--A"]
        results = github.store().get_items_from_collection(
            "githubProcessors", filters={"identifiers": ids}
        )
        assert [d["_id"] for d in results["results"]] == ids


class TestParsingIsNotRepeated:
    def test_the_same_turtle_is_parsed_once(self):
        from apps.dishacled.shacl import contracts

        contracts.processor_classes_from_ttl.cache_clear()
        ttl = _ttl("A")
        contracts.processor_classes_from_ttl(ttl)
        contracts.processor_classes_from_ttl(ttl)
        info = contracts.processor_classes_from_ttl.cache_info()
        assert info.hits == 1 and info.misses == 1

    def test_the_result_cannot_be_mutated_through_the_cache(self):
        from apps.dishacled.shacl import contracts

        ttl = _ttl("A")
        first = contracts.processor_classes_from_ttl(ttl)
        first.append("https://example.org/injected")
        assert contracts.processor_classes_from_ttl(ttl) == [
            "https://w3id.org/rdf-connect#A"
        ]


class TestAListingRowIsUsableAsItStands:
    """A row the frontend has to complete costs a round trip per row.

    `processorConfig` is in the picker's *minimal* fragment, and the graphql
    resolver builds it from `data.properties` -- falling back to fetching the
    whole entity, one call per row, when the row does not carry them
    (`dishacledResolver.resolveProcessorConfig`). With one row per processor
    that fallback multiplied: forty rows, forty entity fetches, each reading the
    same repository again. The listing already has the files in hand, so it
    describes what it found.
    """

    def test_a_row_carries_its_own_config(self):
        results = _Github(repo_count=2).store().get_items_from_collection(
            "githubProcessors"
        )
        row = next(d for d in results["results"] if d["_id"] == "o--r0--A")
        assert row["data"]["properties"], "the resolver would refetch this row"
        assert row["data"]["formFields"]
        assert row["data"]["componentIri"] == "https://w3id.org/rdf-connect#A"

    def test_a_row_carries_its_ports(self):
        results = _Github(repo_count=1).store().get_items_from_collection(
            "githubProcessors"
        )
        row = next(d for d in results["results"] if d["_id"] == "o--r0--A")
        assert "ports" in row["data"]

    def test_the_repository_is_read_once_for_all_its_processors(self):
        # two classes in one repository must not mean two tree reads, two
        # manifest reads, or the same file downloaded twice
        github = _Github(repo_count=1)
        github.store().get_items_from_collection("githubProcessors")
        assert len([c for c in github.calls if "/git/trees/" in c]) == 1
        assert len([c for c in github.calls if c.endswith("package.json")]) == 1
        assert len([c for c in github.calls if c.endswith("a.ttl")]) == 1

    def test_a_page_costs_a_fixed_number_of_calls_per_repository(self):
        # 1 search + 5 x (1 repo-ish + 1 tree + 2 files + 2 manifests)
        github = _Github(repo_count=5)
        github.store().get_items_from_collection("githubProcessors")
        assert len(github.calls) <= 1 + 5 * 6


class TestTurtleIsParsedOncePerFile:
    """The same file, asked about by eight processors, is one parse.

    A repository like `file-utils-processors-ts` declares eight processors in
    one file, and every derivation from it -- the form, the properties, the
    shape index, the class list -- parsed that file again. 166 rows meant 166
    parses of the same turtle, which is not only slow but holds the GIL, so the
    concurrent fetches above stopped overlapping. The parsed graph is shared,
    read-only, keyed by content.
    """

    def test_deriving_two_forms_from_one_file_parses_it_once(self):
        from apps.dishacled.shacl.graphs import parsed_graph
        from apps.dishacled.shacl.form import shacl_to_form_fields

        parsed_graph.cache_clear()
        ttl = _ttl("A") + _ttl("B").split("\n", 2)[2]
        shacl_to_form_fields(ttl, target_class="https://w3id.org/rdf-connect#A")
        shacl_to_form_fields(ttl, target_class="https://w3id.org/rdf-connect#B")
        assert parsed_graph.cache_info().misses == 1

    def test_the_shared_graph_is_the_same_object(self):
        from apps.dishacled.shacl.graphs import parsed_graph

        ttl = _ttl("A")
        assert parsed_graph(ttl) is parsed_graph(ttl)

    def test_unparseable_turtle_is_remembered_as_such(self):
        from apps.dishacled.shacl.graphs import parsed_graph

        assert parsed_graph("not turtle @@@") is None
        assert parsed_graph("not turtle @@@") is None

    def test_a_page_of_multi_processor_repositories_is_not_cpu_bound(self):
        # eight processors per repository, five repositories: the work is
        # 5 parses, not 40. Guarded by a generous ceiling rather than a precise
        # time, so the test says "not quadratic" and not "fast on this laptop".
        from apps.dishacled.shacl.graphs import parsed_graph

        parsed_graph.cache_clear()
        classes = "".join(
            _ttl(f"P{index}") if index == 0 else _ttl(f"P{index}").split("\n", 2)[2]
            for index in range(8)
        )
        github = _Github(repo_count=5)
        github.get = _with_body(github, classes)
        store = github.store()
        results = store.get_items_from_collection("githubProcessors")
        rows = _github_rows(results)
        first = parsed_graph.cache_info().misses

        store.get_items_from_collection("githubProcessors")
        second = parsed_graph.cache_info().misses

        assert len(rows) == 5 * 8
        # parses are per distinct file, not per row: 40 rows, far fewer parses.
        # (The demo components in the contract catalog parse their own turtle,
        # which is why this is a bound and not an exact count.)
        assert first < len(rows)
        # ...and the page can be opened again for free
        assert second == first


def _with_body(github, body):
    """Serve `body` for every TTL file this fake GitHub is asked for."""
    original = github.get

    def get(url, **kwargs):
        response = original(url, **kwargs)
        if "/contents/" in url and url.endswith(".ttl"):
            response.json.return_value = {
                "content": base64.b64encode(body.encode()).decode(),
                "encoding": "base64",
            }
        return response

    github.get = get
    return get


class TestTheWorkerBound:
    """Configuration must not be able to break the app at import."""

    def _reload(self, value):
        import importlib
        import os

        from apps.dishacled.storage import dishacled_httpstore

        previous = os.environ.get("COMPONENT_FETCH_WORKERS")
        if value is None:
            os.environ.pop("COMPONENT_FETCH_WORKERS", None)
        else:
            os.environ["COMPONENT_FETCH_WORKERS"] = value
        try:
            return importlib.reload(dishacled_httpstore).FETCH_WORKERS
        finally:
            if previous is None:
                os.environ.pop("COMPONENT_FETCH_WORKERS", None)
            else:
                os.environ["COMPONENT_FETCH_WORKERS"] = previous
            importlib.reload(dishacled_httpstore)

    def test_unset_uses_the_default(self):
        assert self._reload(None) == 16

    def test_empty_uses_the_default(self):
        # `docker compose` passes a variable declared in .env with no value as
        # an empty string, and int("") raises -- which would be a failed import
        # of the whole app, not a slow picker
        assert self._reload("") == 16

    def test_nonsense_uses_the_default(self):
        assert self._reload("plenty") == 16

    def test_a_configured_value_is_honoured(self):
        assert self._reload("4") == 4

    def test_it_is_capped(self):
        assert self._reload("100000") == 64

    def test_zero_still_leaves_one_worker(self):
        assert self._reload("0") == 1
