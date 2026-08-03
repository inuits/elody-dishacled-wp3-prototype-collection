from unittest.mock import patch, MagicMock
import base64

import requests

from apps.dishacled.storage.dishacled_httpstore import DishacledHttpStorageManager


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
        # whole request; a single item simply resolves to nothing.
        store = DishacledHttpStorageManager()
        store.session = MagicMock()
        store.session.get.side_effect = requests.exceptions.ConnectionError(
            "Failed to resolve 'api.github.com'"
        )

        assert store.get_item_from_collection_by_id("githubProcessors", "rdfc--x") == {}


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


def _repo_fetch_session(ttl: str, full_name: str = "rdfc/ldes-client"):
    """A mocked session answering the repo / tree / contents calls in order."""
    repo_response = MagicMock()
    repo_response.status_code = 200
    repo_response.json.return_value = {
        **MOCK_REPO,
        "full_name": full_name,
        "html_url": f"https://github.com/{full_name}",
    }

    tree_response = MagicMock()
    tree_response.status_code = 200
    tree_response.json.return_value = {"tree": [{"path": "processor.ttl"}]}

    content_response = MagicMock()
    content_response.status_code = 200
    content_response.json.return_value = {
        "content": base64.b64encode(ttl.encode("utf-8")).decode("utf-8"),
        "encoding": "base64",
    }

    session = MagicMock()
    session.get.side_effect = [repo_response, tree_response, content_response]
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
