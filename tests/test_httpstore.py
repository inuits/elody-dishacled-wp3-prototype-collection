from unittest.mock import patch, MagicMock
import base64

from apps.dishacled.storage.dishacled_httpstore import DishacledHttpStorageManager


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
        # form fields (modalFormFields shape) derived from the SHACL
        form_fields = item["data"]["formFields"]
        assert form_fields["url"]["inputField"]["type"] == "baseTextField"
        assert form_fields["url"]["inputField"]["validation"]["value"] == ["required"]
        assert form_fields["follow"]["inputField"]["type"] == "baseCheckbox"
