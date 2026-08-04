import base64
import json
import tomllib
from os import getenv

import requests
import requests_cache
from rdflib import Graph

from apps.dishacled.pipeline.connections import ports_for_component
from apps.dishacled.shacl.parser import ShaclParser
from apps.dishacled.shacl.contracts import ContractCatalog, component_iri_from_ttl
from apps.dishacled.shacl.form import (
    shacl_properties_to_form_fields,
    shacl_to_form_fields,
)
from apps.dishacled.storage.local_component_source import LocalComponentSource
from storage.httpstore import HttpStorageManager
from configuration import get_object_configuration_mapper
from serialization.serialize import serialize

CACHE_LOCATION = getenv("CACHE_LOCATION", "/tmp/dishacled_http_store-cache")

# Package-manager IRIs the toolchain pipeline generator routes on: `:npm` rows
# land in the generated package.json, `:pip` rows in pyproject.toml, and
# anything else is dropped from both.
NPM_SUPPLIER = "http://example.org/example/npm"
PIP_SUPPLIER = "http://example.org/example/pip"

EMPTY_DEPLOYMENT = {"imports": [], "packages": []}


def _is_valid_turtle(content: str) -> bool:
    try:
        Graph().parse(data=content, format="turtle")
        return True
    except Exception:
        return False


class DishacledHttpStorageManager(HttpStorageManager):
    def __init__(self):
        self.github_api_url = getenv("GITHUB_API_URL", "https://api.github.com")
        self.github_token = getenv("GITHUB_TOKEN", "")
        self.processor_topic = getenv("GITHUB_PROCESSOR_TOPIC", "rdfc-processor")
        self.local_components = LocalComponentSource()
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

        # A present (even if empty) identifiers filter restricts the result to
        # exactly those identifiers. An empty list yields no results without
        # falling through to the "search all repos by topic" branch below.
        if identifiers is not None:
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

        # Catalog-declared components are listed first so the demo components
        # are visible on the first page alongside the discovered repositories.
        local_documents = self.local_components.list_documents(extra_query)

        response = self.session.get(url, headers=self._get_headers(), params=params)
        if response.status_code not in [200]:
            return {
                "results": local_documents,
                "count": len(local_documents),
                "limit": limit,
                "skip": skip,
            }

        data = response.json()
        results = data.get("items", [])
        total_count = data.get("total_count", 0) + len(local_documents)

        prepared_documents = list(local_documents)
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
        # Catalog-declared components have no repository behind them, so they
        # resolve locally and never touch GitHub.
        if self.local_components.matches(id):
            return self.local_components.get_document(id) or {}

        repo_path = id.replace("--", "/")
        url = f"{self.github_api_url}/repos/{repo_path}"
        try:
            response = self.session.get(url, headers=self._get_headers())
        except requests.exceptions.RequestException:
            # GitHub unreachable (offline / DNS failure): resolve to nothing
            # instead of failing the whole request.
            return {}

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
            # Repos may contain TTL files that are not valid standalone
            # turtle (test fixtures, doc snippets). Keep only files that
            # parse, so rawTtl (their concatenation) stays parseable for the
            # form derivation and the pipeline TTL export. The path travels
            # with the content because the export needs to name the file an
            # import points at, not just its triples.
            ttl_documents = [
                (ttl_path, content)
                for ttl_path, content in (
                    (ttl_path, self._fetch_ttl_content(repo, ttl_path))
                    for ttl_path in ttl_files
                )
                if content and _is_valid_turtle(content)
            ]
            contents = [content for _, content in ttl_documents]
            prop_objects = self._parse_shacl_property_objects(contents)
            if prop_objects:
                raw_ttl = "\n".join(contents)
                # Form fields follow SHACL 1.2 UI: the main processor shape's
                # properties, with nested node shapes as inputFieldWithSubFields
                # (shui:DetailsEditor). Fall back to the flat main-shape mapping.
                try:
                    form_fields = shacl_to_form_fields(raw_ttl)
                except Exception:
                    main_props = self._parse_main_processor_property_objects(
                        contents
                    )
                    form_fields = shacl_properties_to_form_fields(
                        main_props or prop_objects
                    )
                prepared["data"] = {
                    "properties": self._parse_shacl_contents(contents),
                    "formFields": form_fields,
                    "rawTtl": raw_ttl,
                    "deployment": self._deployment(repo, ttl_documents),
                    **self._contract_overlay(raw_ttl),
                }
                # ports are derived from the two above: the channel-typed
                # config properties named by the shape, typed by the contract
                prepared["data"]["ports"] = [
                    port.to_dict() for port in ports_for_component(prepared)
                ]

        return prepared

    def _contract_overlay(self, raw_ttl):
        """Input/output/config shapes for the component this repo implements.

        Joined on the class IRI the repo's own TTL declares. Returns nothing
        when the component is unknown to the catalog, so a processor without a
        contract simply carries no shape keys.
        """
        try:
            contract = ContractCatalog.default().get(component_iri_from_ttl(raw_ttl))
        except Exception:
            return {}
        if not contract:
            return {}
        data = contract.to_data()
        if data.get("deployment") == EMPTY_DEPLOYMENT:
            # A curated contract overrides what the repository says, but only
            # where it actually says something -- otherwise a catalog entry
            # without coordinates would erase the ones we just read.
            data.pop("deployment")
        return data

    def _deployment(self, repo, ttl_documents):
        """Where this processor is installed from, read off the repository.

        The pipeline generator turns `spdx:Package` into a `package.json` /
        `pyproject.toml` entry and follows `owl:imports` to find the processor
        definition at start-up. Neither is in the SHACL file, so both come
        from the repository's own manifest.
        """
        package = self._package_from_manifest(repo)
        if not package:
            return dict(EMPTY_DEPLOYMENT)

        imports = []
        if package["supplier"] == NPM_SUPPLIER:
            # An npm package publishes its TTL at the same path the repository
            # holds it at (verified against the published @rdfc tarballs), so
            # the repo-relative path is also the in-package one. Only files
            # that actually declare a processor are imported -- a repository's
            # test fixtures and doc snippets are not processor definitions.
            imports = [
                f"./node_modules/{package['name']}/{path}"
                for path, content in ttl_documents
                if component_iri_from_ttl(content)
            ]
        # A Python package's install location depends on the interpreter
        # version baked into the image, which is not knowable from here, so no
        # import is synthesised for pip.

        return {"imports": imports, "packages": [package]}

    def _package_from_manifest(self, repo):
        try:
            manifest = self._fetch_ttl_content(repo, "package.json")
            if manifest:
                data = json.loads(manifest)
                name, version = data.get("name"), data.get("version")
                if name:
                    return {
                        "name": name,
                        # the generator writes npm versions verbatim into
                        # package.json, so the range operator belongs here
                        "version": f"^{version}" if version else None,
                        "supplier": NPM_SUPPLIER,
                    }

            manifest = self._fetch_ttl_content(repo, "pyproject.toml")
            if manifest:
                project = tomllib.loads(manifest).get("project") or {}
                name, version = project.get("name"), project.get("version")
                if name:
                    return {
                        "name": name,
                        # pyproject entries are emitted as `<name><version>`,
                        # so the version has to carry its own operator
                        "version": f">={version}" if version else None,
                        "supplier": PIP_SUPPLIER,
                    }
        except Exception:
            # an unreadable or malformed manifest leaves the component without
            # coordinates; it must not take the whole lookup down
            return None
        return None

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
        contents = [
            content
            for content in (
                self._fetch_ttl_content(repo, ttl_path) for ttl_path in ttl_files
            )
            if content
        ]
        return self._parse_shacl_contents(contents)

    def _parse_shacl_property_objects(self, contents):
        parser = ShaclParser()
        all_props = []
        for content in contents:
            try:
                shapes = parser.parse(content)
            except Exception:
                continue
            for properties in shapes.values():
                all_props.extend(properties)
        return all_props

    def _parse_main_processor_property_objects(self, contents):
        # only the main processor shape's properties (skip aux shapes like
        # HttpFetchAuth/HttpFetchOptions) so the config form stays focused
        parser = ShaclParser()
        all_props = []
        for content in contents:
            try:
                all_props.extend(parser.parse_main_processor_properties(content))
            except Exception:
                continue
        return all_props

    def _parse_shacl_contents(self, contents):
        return [
            {
                "name": prop.name,
                "inputFieldType": prop.input_field_type,
                "isRequired": prop.is_required,
                "inValues": prop.in_values,
                "classRef": prop.class_ref,
            }
            for prop in self._parse_shacl_property_objects(contents)
        ]

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
