"""Publish the components Elody knows about into the shared catalog graph.

A pipeline definition only names its components; their identity, runner,
package coordinates and shapes live in a catalog. The toolchain has one
(`catalog.ttl` and `demonstrator-catalog-extensions.ttl` in
thcarsten/toolchain-specification), but it cannot know what Elody discovers
live on GitHub, so until now Elody shipped the description *inside* every
definition it exported -- the catalog fragment. That works and it merges, but
it means the same component is described in as many places as there are
pipelines using it, and a discovery service reading the store sees no
components at all unless it opens the pipelines.

So the descriptions are written into the store too, in the same
discovery-spec vocabulary (`serializers/component_catalog_serializer.py`, the
one the fragment is built from). Then a consumer -- Koen's `app-dcat-catalog`
discovery service, the pipeline generator, the shape matcher -- reads
components and pipelines out of one store, and a definition that references a
component by IRI alone resolves.

**One named graph per component**, `<CATALOG_GRAPH>/<encoded component IRI>`,
written with a Graph Store Protocol PUT. It is the same reasoning as for
pipeline definitions (`publication.py`): a component description hangs its
packages and whole shape sub-graphs off blank nodes, so "delete the triples of
component X" inside a shared graph has no reliable spelling, while a whole-graph
PUT is an atomic replace and therefore idempotent -- publishing the same
component twice leaves the store in exactly the state one publish leaves it in.
Consumers reach them the way they reach definitions:

    GRAPH ?g { ?component a tcs:PipelineComponent }

**Elody does not own the catalog.** Two rules keep it from claiming to:

* *Structurally* -- it only ever writes graphs under `CATALOG_GRAPH`, so a
  triple written by the toolchain catalog cannot be replaced or removed by a
  write from here whatever happens.
* *Editorially* -- before publishing, Elody asks whether the component is
  already described anywhere outside its own graphs -- the catalog graphs *and*
  the pipeline-definition graphs, since a definition carries the catalog
  fragment -- and skips it if it is.
  The toolchain catalog is authoritative for the components it carries; Elody
  adds only what it lacks. A component that becomes described there later is
  withdrawn from Elody's graph on the next publish, so the duplicate does not
  outlive the reason for it.

  This check needs a query endpoint. Without one, Elody publishes and says so
  in the log: a duplicate description in a separate graph is recoverable, a
  component missing from the catalog is what breaks a build.

Publishing never fails the thing that triggered it. The store is another
service; when it is down the pipeline is still saved and the component is still
listed, and the next publish catches up.

Configuration:

    CATALOG_GRAPH             base IRI the per-component graph is derived from
    CATALOG_GSP_ENDPOINT      Graph Store Protocol endpoint for the write
                              (defaults to PIPELINE_GSP_ENDPOINT -- the same
                              store, and usually the same dataset)
    CATALOG_SPARQL_ENDPOINT   query endpoint for the ownership check
                              (defaults to SPARQL_ENDPOINT)
    CATALOG_STORE_USER        credentials for the write endpoint, when it asks
    CATALOG_STORE_PASSWORD    for them (default to PIPELINE_STORE_*)

Nothing is published while `CATALOG_GRAPH` and an endpoint are unset, so an
environment without a store simply does not have this behaviour.
"""

from logging import getLogger
from os import getenv
from urllib.parse import quote

from rdflib import Graph

# `requests` is addressed through this module, as in `publication.py`, so the
# test suite can stand a store in for it without a triple store running.
import requests

from configuration import get_storage_mapper

from apps.dishacled.serializers.component_catalog_serializer import (
    ComponentCatalogSerializer,
    component_iri_of,
)


log = getLogger(__name__)

TIMEOUT = 30

# A component's `owl:imports` is relative (`./node_modules/...`) because it
# resolves against where the runner mounts the pipeline, which is not anything
# Elody knows. A store cannot hold that: it resolves the reference the moment it
# parses the document, and a Graph Store PUT would resolve it against the
# endpoint's own URL. So it is resolved here first, against the base the
# generator itself parses with -- the same value, from the same variable, as
# `publication.IMPORT_BASE`, since a component described in the catalog graph
# and the same component described in a definition have to name one file.
IMPORT_BASE = "file:///workspace/pipeline/"

PUBLISHED = "published"
SKIPPED = "skipped"
FAILED = "failed"
WITHDRAWN = "withdrawn"

# A dataset is described as a `dcat:Dataset` rather than as a component, so the
# check has to ask about both -- the point is whether anything outside Elody's
# graphs already describes this thing, not what it is.
#
# `%(exclusions)s` is one `!STRSTARTS` per base Elody writes, and *every* one of
# them has to be there. A published definition carries the catalog fragment
# (`DEFINITION_INCLUDE_CATALOG`), so the pipeline-definition graphs describe
# components as well -- excluding only the catalog base made Elody's own
# definitions answer this question about it.
ASK_DESCRIBED_ELSEWHERE = """\
PREFIX tcs: <https://w3id.org/toolchain#>
PREFIX dcat: <http://www.w3.org/ns/dcat#>
ASK {
  VALUES ?type { tcs:PipelineComponent dcat:Dataset }
  {
    GRAPH ?g { <%(component)s> a ?type }
    FILTER(%(exclusions)s)
  } UNION {
    <%(component)s> a ?type
  }
}
"""


def _first_env(*names) -> str:
    for name in names:
        value = getenv(name, "").strip()
        if value:
            return value
    return ""


def _endpoint() -> str:
    return _first_env("CATALOG_GSP_ENDPOINT", "PIPELINE_GSP_ENDPOINT")


def _query_endpoint() -> str:
    return _first_env("CATALOG_SPARQL_ENDPOINT", "SPARQL_ENDPOINT")


def _graph_base() -> str:
    return getenv("CATALOG_GRAPH", "").strip().rstrip("/")


def _own_graph_bases() -> tuple[str, ...]:
    """Every named-graph base Elody writes descriptions into.

    The catalog graphs, and the pipeline-definition graphs -- a definition
    carries the catalog fragment, so it describes components too. Read from the
    environment here rather than imported from `publication`, which imports
    this module.
    """
    bases = [_graph_base(), getenv("PIPELINE_GRAPH", "").strip().rstrip("/")]
    return tuple(dict.fromkeys(base for base in bases if base))


def _credentials():
    user = _first_env("CATALOG_STORE_USER", "PIPELINE_STORE_USER")
    if not user:
        return None
    password = getenv("CATALOG_STORE_PASSWORD")
    if password is None:
        password = getenv("PIPELINE_STORE_PASSWORD", "")
    return (user, password)


def _import_base() -> str:
    return _first_env("CATALOG_IMPORT_BASE", "PIPELINE_IMPORT_BASE") or IMPORT_BASE


def is_configured() -> bool:
    return bool(_graph_base() and _endpoint())


def graph_for_component(component_iri) -> str | None:
    """The named graph one component's description lives in, or None.

    The component's own IRI is what names the graph, percent-encoded: it is the
    only identifier every component has (a repository and a contract entry are
    joined on it), and encoding it keeps a graph name that is unique, stable
    across renames of anything else, and safe to put in a query string. The
    local name alone would collide between two namespaces declaring the same
    class name.
    """
    base = _graph_base()
    if not base or not component_iri:
        return None
    return f"{base}/{quote(str(component_iri), safe='')}"


# Discovery is a GitHub repository search, so it is paged; the catalog sweep
# walks pages of this size.
PAGE_SIZE = 20
COMPONENT_COLLECTION = "githubProcessors"


def _page_limit() -> int:
    """How many pages of discovered components one sweep covers.

    Discovery is unbounded in principle -- every repository under the processor
    topic -- so a cap keeps one sweep from turning into a hundred API calls. It
    is configuration because "every processor on GitHub" is a number that will
    change.
    """
    try:
        return max(1, int(getenv("CATALOG_PUBLISH_PAGES", "5")))
    except ValueError:
        return 5


def discovered_components() -> list:
    """Every component Elody can currently see, deduplicated by IRI.

    The same processor can arrive twice -- once as the repository it lives in,
    once as the contract-catalog entry that adds shapes to it -- and they
    describe one component, so the first one wins. Discovery failing part-way
    through returns what it has: a sweep that publishes most of the catalog is
    worth more than one that publishes none of it.
    """
    storage = get_storage_mapper().get("http")()
    documents, seen = [], set()
    for page in range(_page_limit()):
        try:
            answer = storage.get_items_from_collection(
                COMPONENT_COLLECTION, skip=page * PAGE_SIZE, limit=PAGE_SIZE
            )
        except Exception as error:
            log.warning(f"Component discovery stopped at page {page}: {error}")
            break
        results = (answer or {}).get("results") or []
        for document in results:
            component = component_iri_of(document)
            if component is None or component in seen:
                continue
            seen.add(component)
            documents.append(document)
        if len(results) < PAGE_SIZE:
            break
    return documents


def graph_for_document(document) -> str | None:
    """The graph the component this document describes belongs in, or None."""
    return graph_for_component(component_iri_of(document))


# -- the catalog itself ----------------------------------------------------


def component_turtle(document) -> str:
    """One component's description, as it is published.

    The same serializer the definition export embeds, so the two cannot drift.
    The runner is declared alongside it: a component that requires
    `rdfc:NodeRunner` and a catalog that does not say what that is describes
    half a deployment. A dataset has no runner and still has a description.
    """
    serializer = ComponentCatalogSerializer()
    runner = serializer.add(document)
    if len(serializer.graph) == 0:
        return ""
    if runner is not None:
        serializer.declare_runners({runner})

    resolved = Graph()
    resolved.parse(
        data=serializer.serialize(), format="turtle", publicID=_import_base()
    )
    return resolved.serialize(format="turtle")


def publish_component(document) -> str:
    """Upsert one component into the catalog graph. Returns the outcome.

    `SKIPPED` covers both "nothing to say about this" and "the toolchain
    catalog already says it" -- in the second case an earlier Elody description
    of the same component is withdrawn, which is the precedence rule taking
    effect rather than being merely documented.
    """
    if not is_configured():
        return SKIPPED

    component = component_iri_of(document)
    graph = graph_for_component(component)
    if graph is None:
        return SKIPPED

    if is_described_elsewhere(component):
        log.info(
            f"Component <{component}> is described outside Elody's catalog "
            "graphs; leaving that description in place."
        )
        return WITHDRAWN if _delete_graph(graph) else FAILED

    ttl = component_turtle(document)
    if not ttl:
        return SKIPPED
    return PUBLISHED if _put_graph(graph, ttl) else FAILED


def publish_components(documents) -> dict:
    """Upsert every component in `documents`. Returns outcome -> count.

    A component described by two documents -- the same processor reached as a
    repository and as a contract entry -- is published once: the graph is named
    after the component, so the second write would only replace the first with
    the same triples.
    """
    outcomes: dict[str, int] = {}
    seen = set()
    for document in documents or []:
        component = component_iri_of(document)
        if component is not None:
            if component in seen:
                continue
            seen.add(component)
        outcome = publish_component(document)
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
    return outcomes


def unpublish_component(component_iri) -> bool:
    """Drop Elody's description of a component from the catalog graph."""
    graph = graph_for_component(component_iri)
    if graph is None or not _endpoint():
        return False
    return _delete_graph(graph)


def is_described_elsewhere(component_iri) -> bool:
    """Whether something other than Elody already describes this component.

    False when it cannot be established -- no query endpoint, an unreachable or
    unhappy store. Publishing then goes ahead, because the failure modes are
    not symmetric: a second description in a graph of its own is a duplicate a
    consumer can see and we can withdraw, while a component the catalog does
    not describe is a pipeline that does not compile.
    """
    endpoint = _query_endpoint()
    bases = _own_graph_bases()
    if not endpoint or not bases or not component_iri:
        return False

    query = ASK_DESCRIBED_ELSEWHERE % {
        "component": str(component_iri),
        "exclusions": " && ".join(
            f'!STRSTARTS(STR(?g), "{base}/")' for base in bases
        ),
    }
    try:
        response = requests.post(
            endpoint,
            data={"query": query},
            headers={"Accept": "application/sparql-results+json"},
            auth=_credentials(),
            timeout=TIMEOUT,
        )
    except requests.exceptions.RequestException as error:
        log.warning(f"Catalog store {endpoint} unreachable for the ownership check: {error}")
        return False

    if response.status_code != 200:
        log.warning(
            f"Catalog store {endpoint} answered {response.status_code} for the "
            "ownership check; publishing anyway."
        )
        return False
    try:
        return bool(response.json().get("boolean"))
    except Exception:
        return False


# -- the graph store -------------------------------------------------------


def _put_graph(graph, ttl) -> bool:
    return _request(
        "put",
        graph,
        data=ttl.encode("utf-8"),
        headers={"Content-Type": "text/turtle; charset=utf-8"},
    )


def _delete_graph(graph) -> bool:
    # a graph that was never written is already in the state asked for, which
    # Fuseki reports as a 404
    return _request("delete", graph, accept_missing=True)


def _request(method, graph, *, accept_missing=False, **kwargs) -> bool:
    endpoint = _endpoint()
    try:
        response = getattr(requests, method)(
            endpoint,
            params={"graph": graph},
            auth=_credentials(),
            timeout=TIMEOUT,
            **kwargs,
        )
    except requests.exceptions.RequestException as error:
        log.warning(f"Catalog store {endpoint} unreachable: {error}")
        return False

    if response.status_code in (200, 201, 204):
        return True
    if accept_missing and response.status_code == 404:
        return True
    if response.status_code in (401, 403):
        log.warning(
            f"Catalog store {endpoint} refused the write ({response.status_code}); "
            "set CATALOG_STORE_USER / CATALOG_STORE_PASSWORD."
        )
        return False
    log.warning(
        f"Catalog store {endpoint} answered {response.status_code} for "
        f"{method.upper()} <{graph}>: {response.text[:200]}"
    )
    return False
