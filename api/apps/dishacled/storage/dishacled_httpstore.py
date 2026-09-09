import base64
import json
import tomllib
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from logging import getLogger
from os import getenv

import requests
import requests_cache
from requests.adapters import HTTPAdapter
from rdflib import Graph

from apps.dishacled.pipeline.connections import (
    ports_for_component,
    split_component_key,
)
from apps.dishacled.shacl.graphs import is_valid_turtle
from apps.dishacled.shacl.parser import ShaclParser
from apps.dishacled.shacl.contracts import (
    ContractCatalog,
    processor_classes_from_ttl,
    shape_target_classes_from_ttl,
)
from apps.dishacled.shacl.form import (
    shacl_properties_to_form_fields,
    shacl_to_form_fields,
)
from apps.dishacled.storage.local_component_source import LocalComponentSource
from storage.httpstore import HttpStorageManager
from configuration import get_object_configuration_mapper
from serialization.serialize import serialize

CACHE_LOCATION = getenv("CACHE_LOCATION", "/tmp/dishacled_http_store-cache")

log = getLogger(__name__)

# Package-manager IRIs the toolchain pipeline generator routes on: `:npm` rows
# land in the generated package.json, `:pip` rows in pyproject.toml, and
# anything else is dropped from both.
NPM_SUPPLIER = "http://example.org/example/npm"
PIP_SUPPLIER = "http://example.org/example/pip"

EMPTY_DEPLOYMENT = {"imports": [], "packages": []}

# `<owner>--<repo>--<ProcessorClass>`. A component is a processor class in a
# repository, not the repository: `file-utils-processors-ts` declares eight
# processors and each is a component in its own right. The separator is the one
# the owner/repo id already uses, so nothing else has to learn a new shape.
ID_SEPARATOR = "--"

# Directories whose TTL files are not processor definitions. Reading them costs
# a GitHub request each -- `shacl-processor-ts` keeps seven test fixtures next
# to one processor file -- and their shapes have no business in a component's
# rawTtl either. Configurable because a repository may disagree.
NON_SOURCE_DIRECTORIES = tuple(
    stripped
    for stripped in (
        part.strip()
        for part in getenv(
            "PROCESSOR_TTL_EXCLUDED_DIRS",
            "test,tests,example,examples,doc,docs,fixture,fixtures,__tests__",
        ).split(",")
    )
    if stripped
)


def _is_source_ttl(path: str) -> bool:
    """Whether a repository-relative TTL path can hold a processor definition."""
    return not any(
        segment in NON_SOURCE_DIRECTORIES for segment in str(path).split("/")[:-1]
    )


# Discovery is one HTTP call per file per repository and nothing about it is
# sequential, so it is not done sequentially: a page of twenty repositories is
# ~60 round trips, which is ten seconds against a 150ms API and around one when
# fanned out. Bounded because GitHub is a shared resource -- its own guidance is
# to stay under 100 concurrent requests, and a picker page is not the only thing
# running. An unset or unreadable value falls back to the default rather than
# taking the app down at import.
def _worker_count() -> int:
    try:
        configured = int(getenv("COMPONENT_FETCH_WORKERS", "") or 16)
    except ValueError:
        configured = 16
    return max(1, min(configured, 64))


FETCH_WORKERS = _worker_count()

# "the manifest has not been read yet", as distinct from "there is none" --
# which is a legitimate answer and is cached as `None`
_UNREAD = object()


def _drop_vary(response, *args, **kwargs):
    """Strip `Vary` from a GitHub response, before the cache stores it.

    See `DishacledHttpStorageManager._build_session`. The header is only ever
    read by the cache: nothing else here varies a request by anything but its
    URL.
    """
    response.headers.pop("Vary", None)
    return response


def _in_parallel(items, work):
    """`work` over `items`, concurrently, in order, one failure at a time.

    Order is the caller's: a picker page keeps GitHub's ranking and a pipeline
    keeps the order its relations are in. An item whose work raises yields None
    rather than taking the whole page down -- the caller decides what a missing
    one means.
    """
    items = list(items)
    if not items:
        return []
    if len(items) == 1:
        return [_guarded(work, items[0])]
    with ThreadPoolExecutor(max_workers=min(FETCH_WORKERS, len(items))) as pool:
        return list(pool.map(lambda item: _guarded(work, item), items))


def _guarded(work, item):
    try:
        return work(item)
    except Exception:
        # A worker must not take the page down with it, but a swallowed
        # exception that is never printed is how a bug becomes a mystery.
        log.exception("component discovery failed for %s", item)
        return None


def _local_name(iri) -> str:
    text = str(iri)
    if "#" in text:
        return text.split("#")[-1]
    return text.rsplit("/", 1)[-1]


def _split_component_id(id) -> tuple[str, str | None]:
    """`owner--repo--Class` -> ("owner/repo", "Class"); no class -> None.

    A bare `owner--repo` is what pipelines saved before components were
    identified per class hold, and it still has to resolve -- to the first class
    the repository declares, which is stable because the list is sorted.
    """
    parts = str(id).split(ID_SEPARATOR)
    if len(parts) >= 3:
        return "/".join(parts[:2]), ID_SEPARATOR.join(parts[2:])
    return str(id).replace(ID_SEPARATOR, "/"), None


def _identify_as_component(document, repo, component_iri) -> None:
    """Re-key a repository document as one of the components it holds.

    The repository is still worth naming -- it is where the processor comes from
    and how a user recognises it -- so it moves to its own `repository` key
    rather than being overwritten by the class name.
    """
    document["_id"] = _component_id(repo, component_iri)
    document["identifiers"] = [document["_id"]]
    metadata = [
        item for item in document.get("metadata", []) if item.get("key") != "name"
    ]
    metadata.insert(0, {"key": "name", "value": _local_name(component_iri)})
    metadata.append({"key": "repository", "value": repo.get("full_name") or ""})
    document["metadata"] = metadata


def _component_id(repo, component_iri) -> str:
    full_name = repo.get("full_name") or ""
    return (
        f"{full_name.replace('/', ID_SEPARATOR)}"
        f"{ID_SEPARATOR}{_local_name(component_iri)}"
    )


def _unresolved_component(collection, id) -> dict:
    """What an id says about itself, when GitHub cannot be asked.

    A `hasProcessor` relation is evidence that the component exists; a failed
    lookup is not evidence that it does not. Rendering nothing loses the whole
    processor list of a pipeline until the next refresh, so the id -- which
    carries the repository and the processor class -- is described on its own.

    Deliberately without `rawTtl`: nothing here knows the shape, and both
    exports skip a component that has none rather than exporting a stage with
    no class. `unresolved` says so out loud.
    """
    repo_path, class_name = _split_component_id(id)
    owner, _, repo = repo_path.partition("/")
    return {
        "_id": id,
        "identifiers": [id],
        "type": "githubProcessor",
        "metadata": [
            {"key": "name", "value": class_name or repo or id},
            # the row has to say why it is bare, or it reads as a component
            # whose author documented nothing
            {
                "key": "description",
                "value": (
                    "Could not be read from GitHub (rate limited or "
                    "unreachable); its configuration is unavailable until the "
                    "next successful fetch."
                ),
            },
            {"key": "url", "value": f"https://github.com/{repo_path}"},
            {"key": "owner", "value": owner},
            {"key": "repository", "value": repo_path},
        ],
        "relations": [],
        "data": {"unresolved": True},
    }


def _as_step(document, key, instance) -> dict:
    """The same component, addressed as one step of a pipeline.

    Everything about it is the component's -- its shape, its form, its ports --
    except the identity, which is the step's. That is what makes the processor
    list show two rows for two loggers and each config modal write to its own
    relation.
    """
    step = dict(document)
    step["_id"] = key
    step["identifiers"] = [key]

    component_id = (document or {}).get("_id")
    metadata = []
    for item in document.get("metadata", []) or []:
        if item.get("key") == "name":
            # the two rows would otherwise be indistinguishable in the list
            metadata.append(
                {"key": "name", "value": f"{item.get('value')} ({instance})"}
            )
        else:
            metadata.append(item)
    step["metadata"] = metadata

    data = dict(document.get("data") or {})
    data["componentId"] = component_id
    data["instance"] = instance
    step["data"] = data
    return step


def _declares(content: str, component_iri=None) -> bool:
    """Whether this file is the processor definition to import.

    Only files that actually declare a processor are imported -- a repository's
    test fixtures and doc snippets are not processor definitions. With a class
    named, only the file declaring *that* class: importing a sibling
    processor's file would install a definition this component does not use.
    """
    classes = processor_classes_from_ttl(content)
    if component_iri is None:
        return bool(classes)
    return str(component_iri) in classes


def _is_valid_turtle(content: str) -> bool:
    return is_valid_turtle(content)


class DishacledHttpStorageManager(HttpStorageManager):
    def __init__(self):
        self.github_api_url = getenv("GITHUB_API_URL", "https://api.github.com")
        self.github_token = getenv("GITHUB_TOKEN", "")
        self.processor_topic = getenv("GITHUB_PROCESSOR_TOPIC", "rdfc-processor")
        self.local_components = LocalComponentSource()
        self.session = self._build_session(CACHE_LOCATION)

    def _build_session(self, cache_name, **overrides):
        """The cached session, made to actually cache and to fit the fan-out.

        Two things stand between `CachedSession` and a cache hit here, and
        neither is about GitHub being uncacheable:

        * `Vary`. GitHub answers `Vary: Accept, Authorization, ...`, and
          `Authorization` is one of requests-cache's default
          `ignored_parameters` -- it is redacted from the stored request so the
          token never lands in the cache file. requests-cache will not match a
          `Vary` header it has deliberately forgotten the value of, so it
          counts every lookup as a miss. Every request here carries a token, so
          nothing was ever served from cache and every listing re-read every
          file. Dropping the header in a response hook -- which runs before the
          response is stored -- restores matching without storing the token:
          the cache key is the URL, which is all that distinguishes these
          requests anyway.
        * the socket pool. `requests` pools ten connections per host;
          discovery fans out to `FETCH_WORKERS`. Every worker past the tenth
          opened a connection, used it once and had it discarded, paying a TLS
          handshake per file.
        """
        session = requests_cache.CachedSession(
            cache_name,
            expire_after=3600,
            # A 404 is an answer, not a failure: most repositories have no
            # `pyproject.toml` and the jvm ones have no `package.json`, and
            # every caller here already reads "not 200" as "no manifest" or "no
            # such repository". Left uncached it was the only thing a warm
            # listing still went to the network for -- two round trips per
            # manifest-less repository, on every request.
            allowable_codes=(200, 404),
            **overrides,
        )
        session.hooks["response"].append(_drop_vary)
        adapter = HTTPAdapter(
            pool_connections=FETCH_WORKERS, pool_maxsize=FETCH_WORKERS
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def _get_headers(self):
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.github_token:
            headers["Authorization"] = f"Bearer {self.github_token}"
        return headers

    def _get_items_by_identifiers(self, collection, identifiers, skip, limit):
        """The components these ids name, once each, in the order asked for.

        This is what a pipeline's processor panel calls -- one lookup per step,
        each of them a handful of GitHub calls -- so they run together.

        Deduplicated, because the same component can be named twice in one
        request and it is still one component: an entity's `identifiers` are
        its uuid *and* its IRI, and the panel's `$parentIds` filter passes
        both, so "the components of this pipeline" used to be resolved once per
        identifier. That doubled the count the panel displays (six components
        reported as twelve) and doubled the lookups behind it.
        """
        identifiers = list(dict.fromkeys(identifiers or []))
        results = [
            item
            for item in _in_parallel(
                identifiers,
                lambda identifier: self.get_item_from_collection_by_id(
                    collection, identifier
                ),
            )
            if item
        ]
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
        # A `compatible_with` filter is ours, not GitHub's: it carries the
        # pipeline's current component ids so the listing can float the
        # components whose input shape matches the last one's output shape to
        # the top (shape-guided suggestions in the picker). Popped here so the
        # github_filter serializer never sees an unknown key.
        compat_ids = None
        if isinstance(filters, list):
            kept = []
            for f in filters:
                keys = f.get("key") if isinstance(f, dict) else None
                keys = keys if isinstance(keys, list) else [keys]
                if isinstance(f, dict) and "compatible_with" in keys:
                    value = f.get("value")
                    compat_ids = value if isinstance(value, list) else [value]
                else:
                    kept.append(f)
            filters = kept

        suggest_pipeline_ids = None
        suggest_shape_iris = None
        related_pipeline_ids = None
        # filters can be a dict (already serialized) or a list (raw)
        if isinstance(filters, dict):
            identifiers = filters.get("identifiers")
            extra_query = filters.get("q_extra", "")
            compat_ids = filters.get("compat_ids") or compat_ids
            suggest_pipeline_ids = filters.get("suggest_for_pipeline")
            suggest_shape_iris = filters.get("suggest_for_shape")
            related_pipeline_ids = filters.get("related_to_pipeline")
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
                    compat_ids = filter_params.get("compat_ids") or compat_ids
                    suggest_pipeline_ids = filter_params.get("suggest_for_pipeline")
                    suggest_shape_iris = filter_params.get("suggest_for_shape")

        # Pipeline-driven suggestions: resolve the chain's tail component from
        # the pipeline itself. Hard-filters the listing to actual matches, but
        # only while the user is not searching — a search term lifts the
        # filter so incompatible components stay reachable on purpose.
        # The panel's related listing: the pipeline's current components, read
        # from the store itself so a fresh save shows without a parent refetch.
        if related_pipeline_ids is not None:
            keys = []
            for pid in related_pipeline_ids:
                keys.extend(self._pipeline_processor_keys(pid))
            # several identifiers may name one pipeline (uuid and IRI), and a
            # step key can only appear once in it either way
            identifiers = list(dict.fromkeys(keys))

        # Suggestions cover fan-out: a component is suggested when its input
        # shape matches the output of ANY component already in the pipeline —
        # one producer may feed several consumers (monitor → dashboard AND
        # monitor → sparql-ingest), so the frontier is every open output, not
        # just the chain's last link.
        hard_suggest = False
        compat_shapes = None
        if suggest_shape_iris:
            # The picker was opened from one specific output port, so this is
            # the most precise scope there is: exactly that port's shape(s),
            # no pipeline resolution. A search term lifts the hard filter the
            # same way it does for pipeline-wide suggestions.
            values = (
                suggest_shape_iris
                if isinstance(suggest_shape_iris, list)
                else [suggest_shape_iris]
            )
            compat_shapes = {str(v) for v in values if v}
            hard_suggest = not extra_query
        elif compat_ids:
            compat_shapes = self._output_shapes_of(collection, compat_ids)
        elif suggest_pipeline_ids:
            keys = []
            for pid in suggest_pipeline_ids:
                keys.extend(self._pipeline_processor_keys(pid))
            components = []
            for key in keys:
                component_id, _ = split_component_key(key)
                components.append(component_id or key)
            # same pipeline named twice, and two steps of one component: the
            # union of output shapes is over components, not over mentions
            components = list(dict.fromkeys(components))
            if components:
                compat_shapes = self._output_shapes_of(collection, components)
                hard_suggest = not extra_query

        # A present (even if empty) identifiers filter restricts the result to
        # exactly those identifiers. An empty list yields no results without
        # falling through to the "search all repos by topic" branch below.
        if identifiers is not None:
            return self._with_compat_sort(
                self._get_items_by_identifiers(collection, identifiers, skip, limit),
                compat_shapes,
                hard=hard_suggest,
            )

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
            return self._with_compat_sort(
                {
                    "results": local_documents,
                    "count": len(local_documents),
                    "limit": limit,
                    "skip": skip,
                },
                compat_shapes,
                hard=hard_suggest,
            )

        data = response.json()
        results = data.get("items", [])

        prepared_documents = list(local_documents)
        prepared_documents.extend(self._components_of_all(collection, results))

        # Still counted in repositories, not in components: a page is a page of
        # GitHub search results, and how many processors the repositories on the
        # pages after this one declare is not knowable without fetching them. So
        # the count stays a paging bound (it is what decides whether there is a
        # next page) and a page simply yields at least as many rows as it did.
        total_count = data.get("total_count", 0) + len(local_documents)

        return self._with_compat_sort(
            {
                "results": prepared_documents,
                "count": total_count,
                "limit": limit,
                "skip": skip,
            },
            compat_shapes,
            hard=hard_suggest,
        )

    def _pipeline_processor_relations(self, pipeline_id):
        """The pipeline's hasProcessor relations (key + metadata), in order.

        Imported lazily: storage.routing pulls in configuration, which imports
        this module -- a top-level import would be circular.
        """
        try:
            from storage.routing import get_external_storage

            doc = get_external_storage("sparql").get_item_from_collection_by_id(
                "pipelines", pipeline_id
            )
        except Exception:
            return []
        return [
            r
            for r in (doc or {}).get("relations", [])
            if r.get("type") == "hasProcessor" and r.get("key")
        ]

    def _pipeline_processor_keys(self, pipeline_id):
        return [r["key"] for r in self._pipeline_processor_relations(pipeline_id)]

    def _pipeline_tail_component(self, pipeline_id):
        """The chain's tail: the last component not consumed as a producer.

        Relation order alone is not the chain -- a connect-save may rewrite the
        relations in a different order. The `connections.<port>.from` metadata
        says which components already feed another one; the tail is the last
        relation whose component nobody consumes. With no connections at all
        this degrades to the last relation, which is the freshest addition.
        """
        relations = self._pipeline_processor_relations(pipeline_id)
        if not relations:
            return None

        def norm(value):
            step = str(value).split("|")[0].split("~")[0]
            return step[len("local--"):] if step.startswith("local--") else step

        consumed = set()
        for rel in relations:
            for entry in rel.get("metadata") or []:
                key = str(entry.get("key", ""))
                if (
                    key.startswith("connections.")
                    and key.endswith(".from")
                    and entry.get("value")
                ):
                    consumed.add(norm(entry["value"]))

        unconsumed = [
            r["key"] for r in relations if norm(r["key"]) not in consumed
        ]
        pick = unconsumed[-1] if unconsumed else relations[-1]["key"]
        # split_component_key, not _split_component_id: the relation key is a
        # component id (possibly `component~instance`), never `owner--repo`.
        component_id, _ = split_component_key(pick)
        return component_id or pick

    def _output_shapes_of(self, collection, component_ids):
        """The union of output-shape IRIs over the given components."""
        shapes = set()
        for component_id in component_ids:
            try:
                doc = self.get_item_from_collection_by_id(collection, component_id)
            except Exception:
                continue
            for port in ((doc or {}).get("data") or {}).get("ports") or []:
                if port.get("direction") == "out" and port.get("shapeIri"):
                    shapes.add(port["shapeIri"])
        return shapes

    # Store-plumbing the compiler inserts (per the logical scenario-a model);
    # real components a user may still find via search, but never suggested
    # as the next chain step.
    SUGGESTION_PLUMBING_IRIS = {
        "https://w3id.org/rdf-connect#SPARQLIngest",
        "https://w3id.org/rdf-connect#Sdsify",
        "https://w3id.org/rdf-connect#SkolemizationProcessor",
    }

    def _is_plumbing(self, item) -> bool:
        iri = ((item.get("data") or {}).get("componentIri")) or ""
        return iri in self.SUGGESTION_PLUMBING_IRIS

    def _with_compat_sort(self, result, out_shapes, hard=False):
        """Float shape-compatible components to the top of a listing.

        `out_shapes` is what the pipeline currently produces; a component
        whose input shape consumes any of it is a suggestion. Ranking, not
        filtering (unless `hard`): an incompatible component stays listed
        below the suggestions, so the deliberate-mismatch path keeps working.
        """
        if not out_shapes:
            return result

        def rank(item):
            item_ports = ((item.get("data") or {}).get("ports")) or []
            inputs = [p for p in item_ports if p.get("direction") == "in"]
            if any(p.get("shapeIri") in out_shapes for p in inputs):
                return 0  # input shape matches the tail's output: suggest first
            if any(not p.get("shapeIri") for p in inputs):
                return 1  # consumes, but carries no contract to judge by
            if inputs:
                return 2  # consumes something else: the mismatch candidates
            return 3  # sources consume nothing, never a follow-up suggestion

        ordered = sorted(result["results"], key=rank)
        if hard:
            matches = [
                item
                for item in ordered
                if rank(item) == 0 and not self._is_plumbing(item)
            ]
            # Suggestions, not a dead end: with no real matches the full
            # (sorted) list stays, rather than an empty picker.
            if matches:
                result["results"] = matches
                result["count"] = len(matches)
                return result
        result["results"] = ordered
        return result

    def _components_of_all(self, collection, repos):
        """Every component of every repository on a page.

        In phases, not per repository: a repository's reads are a chain (its
        tree, then its files) and nesting one fan-out inside another means the
        worker bound no longer bounds anything -- eight repositories times a
        file each is sixteen sockets, and a repository with ten files is worse.

        Flattened, the whole page is two rounds of independent calls whatever
        its size: every tree and manifest, then every file. Depth stays
        constant, and `COMPONENT_FETCH_WORKERS` means what it says.
        """
        repos = list(repos)
        if not repos:
            return []

        # round one: the file list and the manifest of every repository
        reads = _in_parallel(
            [
                *(
                    (lambda repo=repo: ("paths", repo, self._source_ttl_paths(repo)))
                    for repo in repos
                ),
                *(
                    (
                        lambda repo=repo: (
                            "package",
                            repo,
                            self._package_from_manifest(repo),
                        )
                    )
                    for repo in repos
                ),
            ],
            lambda read: read(),
        )
        paths_by_repo = {id(repo): [] for repo in repos}
        package_by_repo = {id(repo): None for repo in repos}
        for read in reads:
            if not read:
                continue
            kind, repo, value = read
            if kind == "paths":
                paths_by_repo[id(repo)] = value or []
            else:
                package_by_repo[id(repo)] = value

        # round two: every file of every repository
        wanted = [
            (repo, path) for repo in repos for path in paths_by_repo[id(repo)]
        ]
        contents = _in_parallel(
            wanted, lambda item: self._fetch_ttl_content(item[0], item[1])
        )
        documents_by_repo = {id(repo): [] for repo in repos}
        for (repo, path), content in zip(wanted, contents):
            if content and _is_valid_turtle(content):
                documents_by_repo[id(repo)].append((path, content))

        # and then it is only parsing, which is cached per file
        rows = []
        for repo in repos:
            rows.extend(
                self._describe(
                    collection,
                    repo,
                    documents_by_repo[id(repo)],
                    package_by_repo[id(repo)],
                )
            )
        return rows

    def _describe(self, collection, repo, ttl_documents, package):
        """One row per processor this repository declares, or one for itself."""
        classes = self._processor_classes(ttl_documents)
        if not classes:
            return [
                self._component_document(
                    collection, repo, ttl_documents, None, package
                )
            ]
        return [
            self._component_document(
                collection, repo, ttl_documents, component_iri, package
            )
            for component_iri in classes
        ]

    def _components_of(self, collection, repo):
        """Every component one repository holds, described.

        Fully described, not stubbed: `processorConfig` is in the picker's own
        fragment and the graphql resolver builds it from `data.properties`,
        falling back to fetching the whole entity when a row does not carry
        them -- one extra round trip per row, each re-reading this same
        repository. The files are already in hand here, and turning them into a
        form is CPU with a cached parse, so the row is finished on the spot.

        The repository is read once for all of its processors: one tree, one
        download per source file, one manifest lookup.
        """
        ttl_documents, package = self._read_repository(repo)
        return self._describe(collection, repo, ttl_documents, package)

    def get_item_from_collection_by_id(self, collection, id):
        """One component, or one *step* of one.

        A pipeline addresses its steps by `component~step` so that two steps of
        one component are two rows the UI can configure separately (see
        `pipeline/connections.py`). Both resolve to the same component; the step
        keeps its own id, so the row and its config form stay its own.
        """
        component_id, instance = split_component_key(id)
        if instance:
            document = self.get_item_from_collection_by_id(
                collection, component_id
            )
            return _as_step(document, id, instance) if document else document

        # Catalog-declared components have no repository behind them, so they
        # resolve locally and never touch GitHub.
        if self.local_components.matches(id):
            return self.local_components.get_document(id) or {}

        repo_path, class_name = _split_component_id(id)
        url = f"{self.github_api_url}/repos/{repo_path}"
        try:
            response = self.session.get(url, headers=self._get_headers())
        except requests.exceptions.RequestException:
            # GitHub unreachable (offline / DNS failure): describe what the id
            # says rather than dropping the component out of its pipeline.
            return _unresolved_component(collection, id)

        if response.status_code == 404:
            # 404 is an answer: this repository really is not there, and
            # inventing a row would hide a broken relation instead of showing it
            return {}
        if response.status_code != 200:
            # rate limited (403) or a bad gateway: an outage, not a deletion
            return _unresolved_component(collection, id)

        repo = response.json()
        ttl_documents, package = self._read_repository(repo)
        classes = self._processor_classes(ttl_documents)

        component_iri = None
        if class_name is not None:
            component_iri = next(
                (iri for iri in classes if _local_name(iri) == class_name), None
            )
            if component_iri is None:
                # a class this repository does not declare is not a component,
                # however plausible the id looked
                return {}
        elif classes:
            component_iri = classes[0]

        return self._component_document(
            collection, repo, ttl_documents, component_iri, package
        )

    def _read_repository(self, repo):
        """Its source TTL files and its package manifest, read together.

        The two are independent -- one is the tree and its turtle, the other is
        `package.json` / `pyproject.toml` -- so waiting for the first before
        starting the second doubles the depth of the chain for no reason.
        """
        ttl_documents, package = _in_parallel(
            [
                lambda: self._ttl_documents(repo),
                lambda: self._package_from_manifest(repo),
            ],
            lambda read: read(),
        )
        return ttl_documents or [], package

    def _source_ttl_paths(self, repo):
        """The repository-relative paths of its TTL files that can hold a processor."""
        return [
            ttl_path
            for ttl_path in self._find_ttl_files(repo)
            if _is_source_ttl(ttl_path)
        ]

    def _ttl_documents(self, repo):
        """The repository's parseable TTL files, as (path, content).

        Repos may contain TTL files that are not valid standalone turtle (test
        fixtures, doc snippets). Keep only files that parse, so rawTtl (their
        concatenation) stays parseable for the form derivation and the pipeline
        TTL export. The path travels with the content because the export needs
        to name the file an import points at, not just its triples.
        """
        paths = self._source_ttl_paths(repo)
        contents = _in_parallel(
            paths, lambda ttl_path: self._fetch_ttl_content(repo, ttl_path)
        )
        return [
            (ttl_path, content)
            for ttl_path, content in zip(paths, contents)
            if content and _is_valid_turtle(content)
        ]

    @staticmethod
    def _processor_classes(ttl_documents) -> list:
        """Every processor class this repository declares, in a fixed order.

        A repository that declares no implementation at all falls back to the
        classes its shapes target: a processor described by a shape alone is
        still a component, and dropping it would take its config form with it.
        """
        contents = [content for _, content in ttl_documents]

        declared = sorted({
            iri for content in contents for iri in processor_classes_from_ttl(content)
        })
        if declared:
            return declared

        return sorted({
            iri
            for content in contents
            for iri in shape_target_classes_from_ttl(content)
        })

    def _component_document(
        self, collection, repo, ttl_documents, component_iri, package=_UNREAD
    ):
        """One component: a processor class, described by its own shape.

        A repository declaring no processor at all still yields a document -- it
        is listed as itself, with no class and no config form, the way it was
        before components were identified per class.
        """
        prepared = _prepare_http_document(collection, repo)
        prepared["type"] = "githubProcessor"

        if ttl_documents:
            prepared["metadata"].append(
                {
                    "key": "shaclFiles",
                    "value": ",".join(path for path, _ in ttl_documents),
                }
            )

        if component_iri is None:
            return prepared

        _identify_as_component(prepared, repo, component_iri)

        contents = [content for _, content in ttl_documents]
        raw_ttl = "\n".join(contents)
        prop_objects = self._parse_shacl_property_objects(contents)
        if not prop_objects:
            return prepared

        # Form fields follow SHACL 1.2 UI: this class's properties, with nested
        # node shapes as inputFieldWithSubFields (shui:DetailsEditor). Fall back
        # to the flat mapping of the same class's properties.
        main_props = self._parse_main_processor_property_objects(
            [raw_ttl], component_iri
        )
        try:
            form_fields = shacl_to_form_fields(raw_ttl, target_class=component_iri)
        except Exception:
            form_fields = shacl_properties_to_form_fields(
                main_props or prop_objects
            )
        prepared["data"] = {
            # this class's own properties: the ports and the config form are
            # about one processor, not about everything the repository holds
            "properties": self._properties_of(main_props or prop_objects),
            "formFields": form_fields,
            "rawTtl": raw_ttl,
            "componentIri": str(component_iri),
            "deployment": self._deployment(
                repo, ttl_documents, component_iri, package
            ),
            **self._contract_overlay(component_iri),
        }
        # ports are derived from the two above: the channel-typed
        # config properties named by the shape, typed by the contract
        prepared["data"]["ports"] = [
            port.to_dict() for port in ports_for_component(prepared)
        ]

        return prepared

    def _contract_overlay(self, component_iri):
        """Input/output/config shapes for the component this repo implements.

        Joined on the class IRI, which the component now knows rather than
        having to be guessed from the file. Returns nothing when the component
        is unknown to the catalog, so a processor without a contract simply
        carries no shape keys.
        """
        try:
            contract = ContractCatalog.default().get(str(component_iri))
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

    def _deployment(self, repo, ttl_documents, component_iri=None, package=_UNREAD):
        """Where this processor is installed from, read off the repository.

        The pipeline generator turns `spdx:Package` into a `package.json` /
        `pyproject.toml` entry and follows `owl:imports` to find the processor
        definition at start-up. Neither is in the SHACL file, so both come
        from the repository's own manifest.
        """
        # the caller may already have read the manifest -- a repository holding
        # eight processors should not be asked for its package.json eight times
        if package is _UNREAD:
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
                if _declares(content, component_iri)
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

    def _parse_main_processor_property_objects(self, contents, component_iri=None):
        # only this processor's own properties (skip aux shapes like
        # HttpFetchAuth/HttpFetchOptions, and the other processors the same file
        # declares) so the config form stays focused
        parser = ShaclParser()
        all_props = []
        for content in contents:
            try:
                all_props.extend(
                    parser.parse_main_processor_properties(content, component_iri)
                )
            except Exception:
                continue
        return all_props

    @staticmethod
    def _properties_of(prop_objects):
        return [
            {
                "name": prop.name,
                "inputFieldType": prop.input_field_type,
                "isRequired": prop.is_required,
                "inValues": prop.in_values,
                "classRef": prop.class_ref,
            }
            for prop in prop_objects
        ]

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
