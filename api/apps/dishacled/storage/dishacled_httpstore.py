import base64
from os import getenv

import requests
import requests_cache

from apps.dishacled.shacl.parser import ShaclParser
from storage.httpstore import HttpStorageManager
from configuration import get_object_configuration_mapper
from serialization.serialize import serialize

CACHE_LOCATION = getenv("CACHE_LOCATION", "/tmp/dishacled_http_store-cache")


class DishacledHttpStorageManager(HttpStorageManager):
    def __init__(self):
        self.github_api_url = getenv("GITHUB_API_URL", "https://api.github.com")
        self.github_token = getenv("GITHUB_TOKEN", "")
        self.processor_topic = getenv("GITHUB_PROCESSOR_TOPIC", "rdfc-processor")
        self.session = requests_cache.CachedSession(
            CACHE_LOCATION,
            expire_after=3600,
        )

    def _get_headers(self):
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.github_token:
            headers["Authorization"] = f"Bearer {self.github_token}"
        return headers

    def _get_items_by_identifiers(self, collection, identifiers, skip, limit):
        results = []
        for identifier in identifiers:
            item = self.get_item_from_collection_by_id(collection, identifier)
            if item:
                results.append(item)
        total = len(results)
        paginated = results[skip : skip + limit]
        return {
            "results": paginated,
            "count": total,
            "limit": limit,
            "skip": skip,
        }

    def get_items_from_collection(
        self,
        collection,
        skip=0,
        limit=20,
        fields=None,
        filters=[],
        sort=None,
        asc=True,
    ):
        # filters can be a dict (already serialized) or a list (raw)
        if isinstance(filters, dict):
            identifiers = filters.get("identifiers")
            extra_query = filters.get("q_extra", "")
        else:
            identifiers = None
            extra_query = ""
            if filters:
                filter_params = serialize(
                    filters,
                    type=collection,
                    to_format="github_filter",
                    from_format="elody_filter",
                )
                if isinstance(filter_params, dict):
                    identifiers = filter_params.get("identifiers")
                    extra_query = filter_params.get("q_extra", "")

        if identifiers:
            return self._get_items_by_identifiers(collection, identifiers, skip, limit)

        page_size = limit
        page_number = (skip // limit) + 1 if limit else 1

        query = f"topic:{self.processor_topic}"

        if extra_query:
            query = f"{query} {extra_query}"

        url = f"{self.github_api_url}/search/repositories"
        params = {
            "q": query,
            "per_page": page_size,
            "page": page_number,
            "sort": "stars",
            "order": "desc",
        }

        response = self.session.get(url, headers=self._get_headers(), params=params)
        if response.status_code not in [200]:
            return {"results": [], "count": 0, "limit": limit, "skip": skip}

        data = response.json()
        results = data.get("items", [])
        total_count = data.get("total_count", 0)

        prepared_documents = []
        for repo in results:
            prepared = _prepare_http_document(collection, repo)
            prepared["type"] = "githubProcessor"
            prepared_documents.append(prepared)

        return {
            "results": prepared_documents,
            "count": total_count,
            "limit": limit,
            "skip": skip,
        }

    def get_item_from_collection_by_id(self, collection, id):
        repo_path = id.replace("--", "/")
        url = f"{self.github_api_url}/repos/{repo_path}"
        response = self.session.get(url, headers=self._get_headers())

        if response.status_code == 404:
            return {}
        if response.status_code != 200:
            return {}

        repo = response.json()
        prepared = _prepare_http_document(collection, repo)
        prepared["type"] = "githubProcessor"

        ttl_files = self._find_ttl_files(repo)
        if ttl_files:
            prepared["metadata"].append(
                {"key": "shaclFiles", "value": ",".join(ttl_files)}
            )
            properties = self._parse_shacl_properties(repo, ttl_files)
            if properties:
                prepared["data"] = {"properties": properties}

        return prepared

    def _fetch_ttl_content(self, repo, file_path):
        owner = repo.get("owner", {}).get("login", "")
        name = repo.get("name", "")
        url = f"{self.github_api_url}/repos/{owner}/{name}/contents/{file_path}"
        response = self.session.get(url, headers=self._get_headers())
        if response.status_code != 200:
            return None
        data = response.json()
        content = data.get("content", "")
        encoding = data.get("encoding", "")
        if encoding == "base64":
            return base64.b64decode(content).decode("utf-8")
        return content

    def _parse_shacl_properties(self, repo, ttl_files):
        parser = ShaclParser()
        all_properties = []
        for ttl_path in ttl_files:
            content = self._fetch_ttl_content(repo, ttl_path)
            if not content:
                continue
            try:
                shapes = parser.parse(content)
            except Exception:
                continue
            for properties in shapes.values():
                for prop in properties:
                    all_properties.append(
                        {
                            "name": prop.name,
                            "inputFieldType": prop.input_field_type,
                            "isRequired": prop.is_required,
                            "inValues": prop.in_values,
                            "classRef": prop.class_ref,
                        }
                    )
        return all_properties

    def _find_ttl_files(self, repo):
        owner = repo.get("owner", {}).get("login", "")
        name = repo.get("name", "")
        default_branch = repo.get("default_branch", "main")

        url = f"{self.github_api_url}/repos/{owner}/{name}/git/trees/{default_branch}?recursive=1"
        response = self.session.get(url, headers=self._get_headers())
        if response.status_code != 200:
            return []

        tree = response.json().get("tree", [])
        return [item["path"] for item in tree if item["path"].endswith(".ttl")]


def _prepare_http_document(collection, document):
    try:
        result = serialize(
            document,
            type=collection,
            to_format="elody",
            from_format="github",
        )
        if "metadata" in result:
            return result
    except Exception:
        pass
    # Fallback: use GithubSerializer directly
    from apps.dishacled.serializers.github_serializer import GithubSerializer
    return GithubSerializer().from_github_to_elody(document)
