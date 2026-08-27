"""A component that cannot be fetched must not disappear from its pipeline.

The processors panel on a pipeline resolves each `hasProcessor` relation key
through the http store, and the store reads GitHub. GitHub is rate limited (60
requests an hour without a token) and occasionally simply down, and when a
lookup failed the store answered `{}` -- so the panel rendered empty and only
came back after a refresh, once the cache had warmed or the window had reset.

A relation is evidence that the component exists; failing to describe it is not
evidence that it does not. So a lookup that cannot reach GitHub falls back to
what the id itself says.
"""

from unittest.mock import MagicMock

import requests

from apps.dishacled.storage.dishacled_httpstore import DishacledHttpStorageManager


PIPELINE_KEYS = [
    "rdf-connect--file-utils-processors-ts--GlobRead",
    "rdf-connect--log-processor-ts--LogProcessorJs",
]


def _rate_limited_store():
    """A store whose every GitHub call is answered with the 403 of a spent budget."""
    store = DishacledHttpStorageManager()
    response = MagicMock()
    response.status_code = 403
    response.json.return_value = {
        "message": "API rate limit exceeded for 91.181.227.83."
    }
    store.session = MagicMock()
    store.session.get.return_value = response
    return store


def _offline_store():
    store = DishacledHttpStorageManager()
    store.session = MagicMock()
    store.session.get.side_effect = requests.exceptions.ConnectionError(
        "Failed to resolve 'api.github.com'"
    )
    return store


class TestARelationSurvivesAFailedLookup:
    def test_a_rate_limited_lookup_still_describes_the_component(self):
        component = _rate_limited_store().get_item_from_collection_by_id(
            "githubProcessors", PIPELINE_KEYS[0]
        )
        assert component["_id"] == PIPELINE_KEYS[0]
        assert component["type"] == "githubProcessor"

    def test_it_carries_the_name_and_repository_the_id_states(self):
        component = _rate_limited_store().get_item_from_collection_by_id(
            "githubProcessors", PIPELINE_KEYS[0]
        )
        metadata = {m["key"]: m["value"] for m in component["metadata"]}
        assert metadata["name"] == "GlobRead"
        assert metadata["repository"] == "rdf-connect/file-utils-processors-ts"

    def test_it_says_it_could_not_be_described(self):
        # so nothing downstream mistakes a placeholder for a described component
        component = _rate_limited_store().get_item_from_collection_by_id(
            "githubProcessors", PIPELINE_KEYS[0]
        )
        assert component["data"]["unresolved"] is True
        assert "rawTtl" not in component["data"]

    def test_the_row_says_why_it_is_bare(self):
        component = _rate_limited_store().get_item_from_collection_by_id(
            "githubProcessors", PIPELINE_KEYS[0]
        )
        description = next(
            m["value"] for m in component["metadata"] if m["key"] == "description"
        )
        assert "Could not be read from GitHub" in description

    def test_an_offline_lookup_behaves_the_same(self):
        component = _offline_store().get_item_from_collection_by_id(
            "githubProcessors", PIPELINE_KEYS[1]
        )
        assert component["_id"] == PIPELINE_KEYS[1]
        assert component["data"]["unresolved"] is True

    def test_the_whole_pipeline_panel_still_renders(self):
        # this is the reported symptom: every row gone until a refresh
        results = _rate_limited_store().get_items_from_collection(
            "githubProcessors", filters={"identifiers": PIPELINE_KEYS}
        )
        assert [d["_id"] for d in results["results"]] == PIPELINE_KEYS
        assert results["count"] == 2

    def test_a_legacy_repository_key_survives_too(self):
        component = _rate_limited_store().get_item_from_collection_by_id(
            "githubProcessors", "rdf-connect--log-processor-ts"
        )
        metadata = {m["key"]: m["value"] for m in component["metadata"]}
        assert component["_id"] == "rdf-connect--log-processor-ts"
        assert metadata["repository"] == "rdf-connect/log-processor-ts"

    def test_a_missing_repository_is_still_a_404(self):
        # 404 is an answer: this component really is not there, and inventing a
        # row for it would hide a broken relation instead of showing it
        store = DishacledHttpStorageManager()
        response = MagicMock()
        response.status_code = 404
        store.session = MagicMock()
        store.session.get.return_value = response

        assert (
            store.get_item_from_collection_by_id("githubProcessors", PIPELINE_KEYS[0])
            == {}
        )

    def test_an_unknown_class_in_a_reachable_repository_is_still_nothing(self):
        # covered by test_component_identity, restated here so the fallback is
        # not read as "any id resolves"
        from tests.test_component_identity import _store

        assert (
            _store().get_item_from_collection_by_id(
                "githubProcessors",
                "rdf-connect--file-utils-processors-ts--NoSuchProcessor",
            )
            == {}
        )


# --------------------------------------------------------------------------
# The other half: not needing the fallback in the first place.
#
# Discovering which processors a repository declares means reading its TTL
# files, so listing a page of repositories went from one GitHub call to one per
# file per repository -- 81 for a page of twenty, on an unauthenticated budget
# of 60 an hour. Most of those files are not processor definitions.
# --------------------------------------------------------------------------

import base64


TTL = """\
@prefix rdfc: <https://w3id.org/rdf-connect#>.
@prefix sh: <http://www.w3.org/ns/shacl#>.

rdfc:A rdfc:jsImplementationOf rdfc:Processor.

[ ] a sh:NodeShape;
    sh:targetClass rdfc:A;
    sh:property [ sh:path rdfc:x; sh:name "x" ].
"""


def _counting_session(tree_paths, repo_count=1):
    calls = []
    repos = [
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

    def _response(payload):
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = payload
        return response

    def _get(url, **kwargs):
        calls.append(url)
        if "/search/repositories" in url:
            return _response({"items": repos, "total_count": repo_count})
        if "/git/trees/" in url:
            return _response({"tree": [{"path": path} for path in tree_paths]})
        if "/contents/package.json" in url or "/contents/pyproject.toml" in url:
            missing = MagicMock()
            missing.status_code = 404
            return missing
        if "/contents/" in url:
            return _response(
                {
                    "content": base64.b64encode(TTL.encode()).decode(),
                    "encoding": "base64",
                }
            )
        return _response(repos[0])

    session = MagicMock()
    session.get.side_effect = _get
    return session, calls


class TestFixtureFilesAreNotFetched:
    """Test fixtures and examples are not processor definitions."""

    # what shacl-processor-ts and rml-processor-jvm actually hold
    PATHS = [
        "processors.ttl",
        "tests/shacl/point.ttl",
        "tests/data/valid.ttl",
        "tests/data/invalid.report.ttl",
        "example/mapping.ttl",
        "examples/demo.ttl",
        "docs/snippet.ttl",
        "src/processor.ttl",
    ]

    def _store(self, paths=None, repo_count=1):
        store = DishacledHttpStorageManager()
        store.session, self.calls = _counting_session(
            paths if paths is not None else self.PATHS, repo_count
        )
        return store

    def test_only_the_source_files_are_read(self):
        store = self._store()
        store.get_item_from_collection_by_id("githubProcessors", "o--r0--A")
        fetched = [
            url.split("/contents/", 1)[1]
            for url in self.calls
            if "/contents/" in url and not url.endswith((".json", ".toml"))
        ]
        assert sorted(fetched) == ["processors.ttl", "src/processor.ttl"]

    def test_a_repository_of_only_fixtures_reads_nothing(self):
        store = self._store(paths=["tests/data/valid.ttl", "docs/x.ttl"])
        store.get_item_from_collection_by_id("githubProcessors", "o--r0")
        assert not [url for url in self.calls if "/contents/" in url and url.endswith(".ttl")]

    def test_a_page_reads_only_the_source_files_of_each_repository(self):
        # 2 source TTL files per repository, not the 5 in the tree. The
        # manifest lookups (package.json, pyproject.toml) are counted
        # separately below: they are per repository, not per file, and not per
        # processor either.
        store = self._store(repo_count=20)
        store.get_items_from_collection("githubProcessors")
        ttl_reads = [
            url for url in self.calls if "/contents/" in url and url.endswith(".ttl")
        ]
        assert len(ttl_reads) == 40

    def test_a_page_asks_each_repository_for_its_manifest_once(self):
        # A repository holding eight processors must not be asked for its
        # package.json eight times -- the row for each processor is built from
        # one read of the repository.
        store = self._store(repo_count=20)
        store.get_items_from_collection("githubProcessors")
        manifests = [url for url in self.calls if url.endswith((".json", ".toml"))]
        trees = [url for url in self.calls if "/git/trees/" in url]
        assert len(trees) == 20
        assert len(manifests) == 40  # package.json + pyproject.toml, per repo
