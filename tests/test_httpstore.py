from unittest.mock import patch, MagicMock
import base64

import requests

from apps.dishacled.storage.dishacled_httpstore import DishacledHttpStorageManager
from apps.dishacled.storage.local_component_source import LocalComponentSource


CM_SHAPE = "https://dishacled.github.io/demo#MeasurementsInCmShape"

# A repository implementing a component the contract catalog knows about. The
# `rdfc:jsImplementationOf` subject is the join key.
CONTRACTED_TTL = """\
@prefix rdfc: <https://w3id.org/rdf-connect#>.
@prefix sh: <http://www.w3.org/ns/shacl#>.
@prefix xsd: <http://www.w3.org/2001/XMLSchema#>.
@prefix demo: <https://dishacled.github.io/demo#>.

demo:ThresholdMonitorCm rdfc:jsImplementationOf rdfc:Processor.

[ ] a sh:NodeShape;
  sh:targetClass demo:ThresholdMonitorCm;
  sh:property [
    sh:datatype xsd:decimal;
    sh:path demo:threshold;
    sh:name "threshold";
    sh:minCount 1;
  ].
"""

EXAMPLE_TTL = """\
@prefix rdfc: <https://w3id.org/rdf-connect#>.
@prefix sh: <http://www.w3.org/ns/shacl#>.
@prefix xsd: <http://www.w3.org/2001/XMLSchema#>.

[ ] a sh:NodeShape;
  sh:targetClass rdfc:LdesClient;
  sh:property [
    sh:datatype xsd:string;
    sh:path rdfc:url;
    sh:name "url";
    sh:maxCount 1;
    sh:minCount 1;
  ];
  sh:property [
    sh:datatype xsd:boolean;
    sh:path rdfc:follow;
    sh:name "follow";
    sh:maxCount 1;
  ].
"""

MOCK_REPO = {
    "owner": {"login": "rdfc"},
    "name": "ldes-client",
    "default_branch": "main",
}


class TestDishacledHttpStorageManagerParseShacl:
    def test_parse_shacl_properties_returns_formatted_properties(self):
        store = DishacledHttpStorageManager()
        encoded = base64.b64encode(EXAMPLE_TTL.encode("utf-8")).decode("utf-8")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "content": encoded,
            "encoding": "base64",
        }
        store.session = MagicMock()
        store.session.get.return_value = mock_response

        properties = store._parse_shacl_properties(MOCK_REPO, ["processor.ttl"])

        assert len(properties) == 2

        url_prop = next(p for p in properties if p["name"] == "url")
        assert url_prop["inputFieldType"] == "baseTextField"
        assert url_prop["isRequired"] is True
        assert url_prop["inValues"] == []

        follow_prop = next(p for p in properties if p["name"] == "follow")
        assert follow_prop["inputFieldType"] == "baseCheckbox"
        assert follow_prop["isRequired"] is False

    def test_parse_shacl_properties_handles_fetch_failure(self):
        store = DishacledHttpStorageManager()

        mock_response = MagicMock()
        mock_response.status_code = 404
        store.session = MagicMock()
        store.session.get.return_value = mock_response

        properties = store._parse_shacl_properties(MOCK_REPO, ["missing.ttl"])
        assert properties == []

    def test_parse_shacl_properties_handles_invalid_ttl(self):
        store = DishacledHttpStorageManager()
        encoded = base64.b64encode(b"not valid ttl content @@@").decode("utf-8")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "content": encoded,
            "encoding": "base64",
        }
        store.session = MagicMock()
        store.session.get.return_value = mock_response

        properties = store._parse_shacl_properties(MOCK_REPO, ["bad.ttl"])
        assert properties == []

    def test_fetch_ttl_content_decodes_base64(self):
        store = DishacledHttpStorageManager()
        original = "some ttl content"
        encoded = base64.b64encode(original.encode("utf-8")).decode("utf-8")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "content": encoded,
            "encoding": "base64",
        }
        store.session = MagicMock()
        store.session.get.return_value = mock_response

        result = store._fetch_ttl_content(MOCK_REPO, "file.ttl")
        assert result == original

    def test_fetch_ttl_content_returns_none_on_failure(self):
        store = DishacledHttpStorageManager()

        mock_response = MagicMock()
        mock_response.status_code = 404
        store.session = MagicMock()
        store.session.get.return_value = mock_response

        result = store._fetch_ttl_content(MOCK_REPO, "missing.ttl")
        assert result is None


class TestGetItemsByIdentifiers:
    def test_empty_identifiers_filter_returns_no_results_without_calling_github(self):
        # A relation filter that resolved to no related processors must yield
        # zero results, NOT fall through to "search all repos by topic".
        store = DishacledHttpStorageManager()
        store.session = MagicMock()

        result = store.get_items_from_collection(
            "githubProcessors", filters={"identifiers": []}
        )

        assert result["results"] == []
        assert result["count"] == 0
        store.session.get.assert_not_called()

    def test_get_item_by_id_is_resilient_to_connection_errors(self):
        # An unreachable GitHub (e.g. offline / DNS failure) must not crash the
        # whole request. It used to resolve to nothing, which emptied the
        # processor list of every pipeline until the next refresh, so the item
        # now falls back to what its id says -- see test_component_resilience.
        store = DishacledHttpStorageManager()
        store.session = MagicMock()
        store.session.get.side_effect = requests.exceptions.ConnectionError(
            "Failed to resolve 'api.github.com'"
        )

        item = store.get_item_from_collection_by_id("githubProcessors", "rdfc--x")
        assert item["_id"] == "rdfc--x"
        assert item["data"] == {"unresolved": True}


class TestGetItemIncludesRawTtl:
    def test_get_item_from_collection_by_id_includes_raw_ttl(self):
        store = DishacledHttpStorageManager()
        encoded = base64.b64encode(EXAMPLE_TTL.encode("utf-8")).decode("utf-8")

        repo_response = MagicMock()
        repo_response.status_code = 200
        repo_response.json.return_value = {
            **MOCK_REPO,
            "full_name": "rdfc/ldes-client",
            "html_url": "https://github.com/rdfc/ldes-client",
        }

        tree_response = MagicMock()
        tree_response.status_code = 200
        tree_response.json.return_value = {
            "tree": [{"path": "processor.ttl"}]
        }

        content_response = MagicMock()
        content_response.status_code = 200
        content_response.json.return_value = {
            "content": encoded,
            "encoding": "base64",
        }

        store.session = MagicMock()
        store.session.get.side_effect = [
            repo_response,
            tree_response,
            content_response,
        ]

        item = store.get_item_from_collection_by_id(
            "githubProcessors", "rdfc--ldes-client"
        )

        assert item["data"]["rawTtl"] == EXAMPLE_TTL
        assert len(item["data"]["properties"]) == 2
        # form fields (modalFormFields shape) derived from the SHACL. The types
        # are the Elody InputFieldTypes enum values, not the intermediate
        # baseTextField/baseCheckbox names -- see _FIELD_TYPE_MAP in form.py.
        form_fields = item["data"]["formFields"]
        assert form_fields["url"]["inputField"]["type"] == "text"
        assert form_fields["url"]["inputField"]["validation"]["value"] == ["required"]
        assert form_fields["follow"]["inputField"]["type"] == "checkbox"


def _repo_fetch_session(
    ttl: str,
    full_name: str = "rdfc/ldes-client",
    package_json: str | None = None,
    tree=None,
    files: dict | None = None,
):
    """A mocked session answering the repo / tree / contents calls.

    Dispatches on the requested URL rather than on call order, so a test that
    adds or drops a fetch does not have to know the sequence.
    """
    repo_response = MagicMock()
    repo_response.status_code = 200
    repo_response.json.return_value = {
        **MOCK_REPO,
        "full_name": full_name,
        "html_url": f"https://github.com/{full_name}",
    }

    tree_response = MagicMock()
    tree_response.status_code = 200
    tree_response.json.return_value = {
        "tree": tree if tree is not None else [{"path": "processor.ttl"}]
    }

    def _contents(body):
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "content": base64.b64encode(body.encode("utf-8")).decode("utf-8"),
            "encoding": "base64",
        }
        return response

    missing = MagicMock()
    missing.status_code = 404

    def _get(url, **kwargs):
        if "/git/trees/" in url:
            return tree_response
        if "/contents/package.json" in url:
            return _contents(package_json) if package_json else missing
        if "/contents/pyproject.toml" in url:
            return missing
        if "/contents/" in url:
            path = url.split("/contents/", 1)[1]
            return _contents((files or {}).get(path, ttl))
        return repo_response

    session = MagicMock()
    session.get.side_effect = _get
    return session


class TestContractOverlay:
    """A GitHub-discovered processor picks up the contract for the component it
    implements, joined on the class IRI in its own TTL."""

    def test_data_includes_input_and_output_shape_for_known_component(self):
        store = DishacledHttpStorageManager()
        store.session = _repo_fetch_session(CONTRACTED_TTL)

        item = store.get_item_from_collection_by_id(
            "githubProcessors", "rdfc--threshold-monitor"
        )

        assert item["data"]["componentIri"] == (
            "https://dishacled.github.io/demo#ThresholdMonitorCm"
        )
        assert item["data"]["componentKind"] == "component"
        assert item["data"]["inputShape"]["iri"] == CM_SHAPE
        assert item["data"]["outputShape"]["iri"] == CM_SHAPE

    def test_shape_ttl_is_available_for_validation(self):
        store = DishacledHttpStorageManager()
        store.session = _repo_fetch_session(CONTRACTED_TTL)

        item = store.get_item_from_collection_by_id(
            "githubProcessors", "rdfc--threshold-monitor"
        )

        from rdflib import Graph

        shape_ttl = item["data"]["outputShape"]["ttl"]
        assert len(Graph().parse(data=shape_ttl, format="turtle")) > 0

    def test_no_shape_keys_when_component_is_not_in_catalog(self):
        store = DishacledHttpStorageManager()
        store.session = _repo_fetch_session(EXAMPLE_TTL)

        item = store.get_item_from_collection_by_id(
            "githubProcessors", "rdfc--ldes-client"
        )

        assert "inputShape" not in item["data"]
        assert "outputShape" not in item["data"]

    def test_existing_data_keys_are_unchanged(self):
        store = DishacledHttpStorageManager()
        store.session = _repo_fetch_session(CONTRACTED_TTL)

        item = store.get_item_from_collection_by_id(
            "githubProcessors", "rdfc--threshold-monitor"
        )

        assert item["data"]["rawTtl"] == CONTRACTED_TTL
        assert [p["name"] for p in item["data"]["properties"]] == ["threshold"]
        assert item["data"]["formFields"]["threshold"]["inputField"]["type"] == "number"

    def test_overlay_survives_a_broken_catalog(self):
        store = DishacledHttpStorageManager()
        store.session = _repo_fetch_session(CONTRACTED_TTL)

        with patch(
            "apps.dishacled.storage.dishacled_httpstore.ContractCatalog.default",
            side_effect=RuntimeError("catalog unreadable"),
        ):
            item = store.get_item_from_collection_by_id(
                "githubProcessors", "rdfc--threshold-monitor"
            )

        assert item["data"]["rawTtl"] == CONTRACTED_TTL
        assert "inputShape" not in item["data"]


class TestLocalComponents:
    """Components declared only in the contract catalog have no repository, so
    they are served from the catalog under a `local--` id."""

    def test_local_id_resolves_without_calling_github(self):
        store = DishacledHttpStorageManager()
        store.session = MagicMock()

        item = store.get_item_from_collection_by_id(
            "githubProcessors", "local--threshold-monitor-cm"
        )

        assert item["_id"] == "local--threshold-monitor-cm"
        store.session.get.assert_not_called()

    def test_local_document_has_the_github_processor_envelope(self):
        store = DishacledHttpStorageManager()
        store.session = MagicMock()

        item = store.get_item_from_collection_by_id(
            "githubProcessors", "local--threshold-monitor-cm"
        )

        assert item["type"] == "githubProcessor"
        assert "local--threshold-monitor-cm" in item["identifiers"]
        assert item["relations"] == []
        metadata = {m["key"]: m["value"] for m in item["metadata"]}
        assert metadata["name"] == "Threshold monitor (cm)"
        assert metadata["source"] == "contracts"

    def test_local_document_exposes_input_and_output_shape(self):
        store = DishacledHttpStorageManager()
        store.session = MagicMock()

        item = store.get_item_from_collection_by_id(
            "githubProcessors", "local--threshold-monitor-cm"
        )

        assert item["data"]["inputShape"]["iri"] == CM_SHAPE
        assert item["data"]["outputShape"]["iri"] == CM_SHAPE

    def test_local_document_still_renders_a_config_form(self):
        store = DishacledHttpStorageManager()
        store.session = MagicMock()

        item = store.get_item_from_collection_by_id(
            "githubProcessors", "local--http-poller-cm"
        )

        assert item["data"]["formFields"]["url"]["inputField"]["type"] == "text"
        assert item["data"]["rawTtl"]

    def test_local_dataset_exposes_output_shape_only(self):
        store = DishacledHttpStorageManager()
        store.session = MagicMock()

        item = store.get_item_from_collection_by_id(
            "githubProcessors", "local--sensor-feed-cm"
        )

        assert item["data"]["outputShape"]["iri"] == CM_SHAPE
        assert item["data"]["inputShape"] is None
        assert item["data"]["componentKind"] == "dataset"

    def test_unknown_local_id_resolves_to_nothing(self):
        store = DishacledHttpStorageManager()
        store.session = MagicMock()

        assert (
            store.get_item_from_collection_by_id("githubProcessors", "local--nope")
            == {}
        )
        store.session.get.assert_not_called()

    def test_listing_includes_local_components_first(self):
        store = DishacledHttpStorageManager()
        search_response = MagicMock()
        search_response.status_code = 200
        search_response.json.return_value = {"items": [], "total_count": 0}
        store.session = MagicMock()
        store.session.get.return_value = search_response

        result = store.get_items_from_collection("githubProcessors")

        ids = [doc["_id"] for doc in result["results"]]
        assert "local--threshold-monitor-cm" in ids
        assert result["count"] == len(ids)

    def test_identifiers_filter_resolves_local_ids(self):
        store = DishacledHttpStorageManager()
        store.session = MagicMock()

        result = store.get_items_from_collection(
            "githubProcessors",
            filters={"identifiers": ["local--http-poller-mm"]},
        )

        assert result["count"] == 1
        assert result["results"][0]["_id"] == "local--http-poller-mm"
        store.session.get.assert_not_called()


# A repository implementing a processor the contract catalog knows nothing
# about -- so its coordinates can only come from its own manifest.
UNCONTRACTED_TTL = """\
@prefix rdfc: <https://w3id.org/rdf-connect#>.
@prefix sh: <http://www.w3.org/ns/shacl#>.
@prefix xsd: <http://www.w3.org/2001/XMLSchema#>.

rdfc:ThresholdMonitorTs rdfc:jsImplementationOf rdfc:Processor.

[ ] a sh:NodeShape;
  sh:targetClass rdfc:ThresholdMonitorTs;
  sh:property [
    sh:datatype xsd:decimal; sh:path rdfc:max; sh:name "max"; sh:minCount 1;
  ].
"""

PACKAGE_JSON = """\
{
  "name": "@rdfc/threshold-monitor-processor-ts",
  "version": "0.0.1-alpha.2",
  "description": "Threshold monitor"
}
"""

PYPROJECT = """\
[project]
name = "rdfc-threshold-monitoring"
version = "0.2.1"
"""


class TestDeploymentCoordinates:
    """What a build tool needs to actually obtain the processor.

    The toolchain pipeline generator turns these into `package.json` /
    `pyproject.toml` entries and the `owl:imports` the RDF-Connect runner
    follows at start-up. Without them an exported pipeline names components
    nothing can install.
    """

    def _item(self, ttl=UNCONTRACTED_TTL, **kwargs):
        store = DishacledHttpStorageManager()
        store.session = _repo_fetch_session(ttl, **kwargs)
        return store.get_item_from_collection_by_id(
            "githubProcessors", "rdfc--threshold-monitor"
        )

    def test_npm_package_is_read_from_package_json(self):
        deployment = self._item(package_json=PACKAGE_JSON)["data"]["deployment"]
        assert deployment["packages"] == [
            {
                "name": "@rdfc/threshold-monitor-processor-ts",
                "version": "^0.0.1-alpha.2",
                "supplier": "http://example.org/example/npm",
            }
        ]

    def test_import_points_inside_the_installed_package(self):
        # Verified against the published tarballs: @rdfc packages carry their
        # TTL at the same path the repository does.
        deployment = self._item(package_json=PACKAGE_JSON)["data"]["deployment"]
        assert deployment["imports"] == [
            "./node_modules/@rdfc/threshold-monitor-processor-ts/processor.ttl"
        ]

    def test_only_files_declaring_a_processor_are_imported(self):
        # A repository's other TTL files (test fixtures, documentation
        # snippets) are not processor definitions and must not be imported.
        deployment = self._item(
            package_json=PACKAGE_JSON,
            tree=[{"path": "processor.ttl"}, {"path": "test/fixture.ttl"}],
            files={"test/fixture.ttl": EXAMPLE_TTL},
        )["data"]["deployment"]
        assert deployment["imports"] == [
            "./node_modules/@rdfc/threshold-monitor-processor-ts/processor.ttl"
        ]

    def test_a_repository_without_a_manifest_declares_no_package(self):
        deployment = self._item()["data"]["deployment"]
        assert deployment["packages"] == []
        assert deployment["imports"] == []

    def test_an_unparseable_manifest_is_ignored(self):
        deployment = self._item(package_json="{ not json")["data"]["deployment"]
        assert deployment["packages"] == []

    def test_a_manifest_without_a_name_is_ignored(self):
        deployment = self._item(package_json='{"version": "1.0.0"}')["data"][
            "deployment"
        ]
        assert deployment["packages"] == []

    def test_a_python_processor_is_routed_to_pip(self):
        store = DishacledHttpStorageManager()
        session = _repo_fetch_session(UNCONTRACTED_TTL)

        def _get(url, **kwargs):
            if "/contents/pyproject.toml" in url:
                response = MagicMock()
                response.status_code = 200
                response.json.return_value = {
                    "content": base64.b64encode(
                        PYPROJECT.encode("utf-8")
                    ).decode("utf-8"),
                    "encoding": "base64",
                }
                return response
            return session.get.side_effect(url, **kwargs)

        store.session = MagicMock()
        store.session.get.side_effect = _get
        item = store.get_item_from_collection_by_id(
            "githubProcessors", "rdfc--threshold-monitor"
        )
        package = item["data"]["deployment"]["packages"][0]
        assert package["name"] == "rdfc-threshold-monitoring"
        assert package["supplier"] == "http://example.org/example/pip"

    def test_a_python_processor_gets_no_synthesised_import(self):
        # The install location of a Python package depends on the interpreter
        # version in the image, which we cannot know from here.
        store = DishacledHttpStorageManager()
        session = _repo_fetch_session(UNCONTRACTED_TTL)

        def _get(url, **kwargs):
            if "/contents/pyproject.toml" in url:
                response = MagicMock()
                response.status_code = 200
                response.json.return_value = {
                    "content": base64.b64encode(
                        PYPROJECT.encode("utf-8")
                    ).decode("utf-8"),
                    "encoding": "base64",
                }
                return response
            return session.get.side_effect(url, **kwargs)

        store.session = MagicMock()
        store.session.get.side_effect = _get
        item = store.get_item_from_collection_by_id(
            "githubProcessors", "rdfc--threshold-monitor"
        )
        assert item["data"]["deployment"]["imports"] == []

    def test_a_catalog_contract_wins_over_the_repository(self):
        # A component the contract catalog declares carries curated
        # coordinates; the repository manifest is only the fallback.
        deployment = self._item(ttl=CONTRACTED_TTL, package_json=PACKAGE_JSON)[
            "data"
        ]["deployment"]
        assert deployment["packages"][0]["name"] == "@dishacled/demo-processors"

    def test_deployment_survives_an_unreachable_manifest(self):
        store = DishacledHttpStorageManager()

        def _get(url, **kwargs):
            if "/contents/package.json" in url:
                raise requests.exceptions.RequestException("offline")
            return _repo_fetch_session(UNCONTRACTED_TTL).get.side_effect(url, **kwargs)

        store.session = MagicMock()
        store.session.get.side_effect = _get
        item = store.get_item_from_collection_by_id(
            "githubProcessors", "rdfc--threshold-monitor"
        )
        assert item["data"]["deployment"]["packages"] == []
        assert item["data"]["rawTtl"] == UNCONTRACTED_TTL


class TestAPipelineNamedTwice:
    """One pipeline, several identifiers, each component listed once.

    An entity's `identifiers` are its uuid *and* its IRI, and the panel's
    `$parentIds` filter passes both -- a relation may be stored under either.
    Resolving the pipeline's components once per identifier returned each of
    them twice: the listing deduplicated the rows (its identifiers are the same
    document id) but the count did not, so a six-component pipeline reported
    twelve. The GraphQL layer then dedupes the results too, which is why the
    rows looked right and only the number was wrong.
    """

    UUID = "20cc1dc3-7e05-4c6f-8772-5704c5f608d9"
    IRI = f"http://collection-api.localhost:8000/pipelines/{UUID}"
    KEYS = ["local--threshold-monitor-js", "local--alert-visualisation"]

    def store(self, monkeypatch):
        store = DishacledHttpStorageManager()
        store.session = MagicMock()
        # both identifiers name the same pipeline, so both resolve to its keys
        monkeypatch.setattr(
            store, "_pipeline_processor_keys", lambda pipeline_id: list(self.KEYS)
        )
        return store

    def test_each_component_is_counted_once(self, monkeypatch):
        store = self.store(monkeypatch)

        result = store.get_items_from_collection(
            "githubProcessors",
            filters={"related_to_pipeline": [self.UUID, self.IRI]},
        )

        assert [item["_id"] for item in result["results"]] == self.KEYS
        assert result["count"] == len(self.KEYS)

    def test_one_identifier_gives_the_same_answer(self, monkeypatch):
        store = self.store(monkeypatch)

        result = store.get_items_from_collection(
            "githubProcessors", filters={"related_to_pipeline": [self.UUID]}
        )

        assert [item["_id"] for item in result["results"]] == self.KEYS
        assert result["count"] == len(self.KEYS)

    def test_a_repeated_identifier_is_looked_up_once(self, monkeypatch):
        """Not only the count: the duplicate was a second round of lookups.

        Every component in the panel was fetched twice, and for a component
        discovered on GitHub that is a second set of API calls.
        """
        store = DishacledHttpStorageManager()
        store.session = MagicMock()
        looked_up = []
        original = store.get_item_from_collection_by_id

        def counting(collection, identifier):
            looked_up.append(identifier)
            return original(collection, identifier)

        monkeypatch.setattr(store, "get_item_from_collection_by_id", counting)

        store.get_items_from_collection(
            "githubProcessors",
            filters={"identifiers": ["local--alert-visualisation"] * 3},
        )

        assert looked_up == ["local--alert-visualisation"]


class TestOverlayComponentsAreNotListedLocally:
    """A catalog entry that only adds shapes to a GitHub repository must not
    also appear as a local component -- that would list the same thing twice."""

    def source(self):
        return LocalComponentSource()

    def test_overlaid_components_are_absent_from_the_listing(self):
        ids = {d["_id"] for d in self.source().list_documents()}
        assert "local--sparql-ingest" not in ids

    def test_a_component_the_catalog_fully_describes_is_listed(self):
        """The threshold monitor stopped being an overlay: discovery does not
        return its repository, so this listing is its only way in."""
        ids = {d["_id"] for d in self.source().list_documents()}
        assert "local--threshold-monitor-js" in ids

    def test_overlaid_components_are_not_resolvable_by_local_id(self):
        assert self.source().get_document("local--sparql-ingest") is None

    def test_the_alert_store_is_listed(self):
        """It has no repository, so the catalog is its only home."""
        ids = {d["_id"] for d in self.source().list_documents()}
        assert "local--alert-store" in ids

    def test_the_alert_store_exposes_the_error_shape(self):
        document = self.source().get_document("local--alert-store")
        assert document["data"]["componentKind"] == "dataset"
        assert document["data"]["outputShape"]["iri"] == (
            "http://lblod.data.gift/shapes/ErrorShape"
        )
        assert document["data"]["inputShape"] is None

    def test_the_demo_components_are_still_listed(self):
        ids = {d["_id"] for d in self.source().list_documents()}
        assert "local--threshold-monitor-cm" in ids
        assert "local--sensor-feed-cm" in ids
