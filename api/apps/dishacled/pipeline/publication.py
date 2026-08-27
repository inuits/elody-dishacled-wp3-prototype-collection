"""Publish a pipeline's `tcs:PipelineDefinition` into the central triple store.

Elody composes pipelines; the toolchain services (pipeline generator, shape
validator, the CLI demo script) consume them. In the demonstrator architecture
they do not call each other -- they meet in one triple store. So a pipeline
that only exists as a download at `GET /pipelines/<id>/definition.ttl` is a
pipeline the rest of the toolchain cannot see. Saving it in Elody writes the
same document into the store, synchronously, so it is queryable by the time the
request returns.

Mongo stays the working store: it holds the pipeline being edited, with the
relations, ports and connection verdicts the UI reasons over. The graph is a
projection of it, in the framework-agnostic `tcs:` vocabulary the toolchain
speaks -- never the runnable RDF-Connect `export.ttl`, which is a build
artefact of the generator, not an input to it.

**One named graph per pipeline**, `<PIPELINE_GRAPH>/<id>`, written with a Graph
Store Protocol PUT. That makes republishing an atomic replace and deletion
exact. The alternative -- every definition in one shared graph, maintained with
DELETE/INSERT -- founders on what a definition actually looks like: config
blocks, package entries and whole shape sub-graphs hang off blank nodes, and
component descriptions are shared between pipelines, so "delete the triples of
pipeline X" has no reliable spelling. Consumers reach the definitions with
`GRAPH ?g { ?p a tcs:PipelineDefinition }`.

**An invalid chain is not published**, and whatever was published for it before
is withdrawn. Both export routes answer 409 for an incompatible chain so that a
pipeline which would fail at run time does not leave the system; the store is
where it would leave the system furthest. Withdrawing matters as much as
refusing: a consumer must not go on compiling the last version that happened to
validate. `PIPELINE_PUBLISH_INVALID=true` is the store's `?force=true` -- the
definition is published with the violations attached to it as comments, since
nobody is standing at the save to be warned.

Publishing never fails a save. The store is another service; when it is down
the pipeline is still edited, and the next save republishes.

Configuration:

    PIPELINE_GSP_ENDPOINT   Graph Store Protocol endpoint (Fuseki: .../data)
    PIPELINE_GRAPH          base IRI the per-pipeline graph is derived from
    PIPELINE_STORE_USER     credentials for the write endpoint, when it asks
    PIPELINE_STORE_PASSWORD   for them (Fuseki restricts /*/data by default)
    PIPELINE_PUBLISH_INVALID  publish anyway, annotated (default: false)
    PIPELINE_EXPORT_BASE_URI  root the pipeline's own IRIs are built on
    PIPELINE_IMPORT_BASE    base the relative `owl:imports` are resolved against

Nothing is published while the first two are unset, so an environment without a
store simply does not have this behaviour.
"""

import re
from logging import getLogger
from os import getenv

import requests
from rdflib import Graph, Literal, RDFS

from apps.dishacled.pipeline import catalog
from apps.dishacled.pipeline.validation import validate_pipeline
from apps.dishacled.serializers.pipeline_definition_serializer import (
    PipelineDefinitionSerializer,
)


log = getLogger(__name__)

TIMEOUT = 30

# The id becomes part of a graph IRI in a query string, so it is held to the
# same conservative character set the SPARQL read engine uses for identifiers:
# anything that could close the IRI and start something else is refused rather
# than escaped.
SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:@-]{1,255}$")

# A component's `owl:imports` is written relative (`./node_modules/...`) and
# has to stay that way in the file: it is resolved against where the runner
# mounts the pipeline, not against anything Elody knows. RDF has no relative
# IRIs, though -- a store resolves them the moment it parses the document, and
# a Graph Store PUT would resolve them against the endpoint's own URL, which is
# meaningless. So the document is resolved here first, against the base the
# generator itself parses with (rdfine `GraphReader`, see docs/toolchain-export.md).
IMPORT_BASE = "file:///workspace/pipeline/"

FORCED_NOTE = (
    "WARNING: this definition was published despite failing chain validation."
)


def _endpoint() -> str:
    return getenv("PIPELINE_GSP_ENDPOINT", "").strip()


def _graph_base() -> str:
    return getenv("PIPELINE_GRAPH", "").strip().rstrip("/")


def _is_true(value) -> bool:
    return str(value).lower() in ("1", "true", "yes")


def _credentials():
    """Basic-auth credentials for the write endpoint, if it is protected.

    A store worth writing to restricts who may write: Fuseki does out of the
    box (`/*/data` and `/*/update` in shiro.ini), and the shared store will
    too. Reads stay anonymous, so only the publisher needs these.
    """
    user = getenv("PIPELINE_STORE_USER", "").strip()
    if not user:
        return None
    return (user, getenv("PIPELINE_STORE_PASSWORD", ""))


def _import_base() -> str:
    return getenv("PIPELINE_IMPORT_BASE", "").strip() or IMPORT_BASE


class IncompletePipeline(Exception):
    """A definition could not represent every step, so it must not be written.

    The store is a pipeline's only home and a save is a whole-graph PUT, so
    writing a definition that is missing a step deletes that step -- and the
    usual reason for missing one is transient: GitHub rate limited or offline,
    a repository renamed. Refusing the write keeps the pipeline as it was; the
    next save, once the component can be described again, writes it whole.
    """

    def __init__(self, keys):
        self.keys = list(keys)
        super().__init__(
            "Not every processor of this pipeline could be described: "
            + ", ".join(self.keys)
            + ". Nothing was written, so the pipeline is unchanged."
        )


def _publish_invalid() -> bool:
    return _is_true(getenv("PIPELINE_PUBLISH_INVALID", ""))


def pipeline_base_uri(pipeline_id) -> str:
    """The `.../pipelines/<id>/` prefix both exports and the store agree on.

    The IRIs a definition mints have to be the same wherever it is read, so the
    root is configuration first. It falls back to the incoming request only to
    keep a development stack that configures nothing working -- a save arriving
    over AMQP has no request to fall back to.
    """
    root = getenv("PIPELINE_EXPORT_BASE_URI", "").strip() or _request_root()
    return f"{root.rstrip('/')}/pipelines/{pipeline_id}/"


def _request_root() -> str:
    try:
        from flask import has_request_context, request

        if has_request_context():
            return request.url_root
    except Exception:
        pass
    return getenv("COLLECTION_API_URL", "").strip()


def graph_for(pipeline_id):
    """The named graph one pipeline's definition lives in, or None."""
    base = _graph_base()
    if not base or not pipeline_id or not SAFE_IDENTIFIER.match(str(pipeline_id)):
        return None
    return f"{base}/{pipeline_id}"


def definition_turtle(pipeline, components, report=None) -> str:
    """The pipeline definition as it is published.

    The document is what `definition.ttl` serves, read with the base the
    generator reads it with -- the one difference a store forces, since it
    cannot hold a relative IRI. A report is only passed when the chain is
    invalid and publishing was forced anyway, in which case its findings are
    carried into the graph itself: the comment block the download prepends is
    turtle syntax, which a triple store does not keep.
    """
    pipeline_id = pipeline.get("_id")
    serializer = PipelineDefinitionSerializer(base_uri=pipeline_base_uri(pipeline_id))
    ttl = serializer.serialize(pipeline, components)
    if serializer.unrepresented:
        raise IncompletePipeline(serializer.unrepresented)

    graph = Graph()
    graph.parse(data=ttl, format="turtle", publicID=_import_base())
    if report is not None and not report.is_valid:
        subject = serializer.pipeline_uri
        graph.add((subject, RDFS.comment, Literal(f"{FORCED_NOTE} {report.summary}")))
        for violation in report.violations:
            graph.add((subject, RDFS.comment, Literal(violation.message)))
    return graph.serialize(format="turtle")


def definition_for_store(pipeline, *, components=None) -> str:
    """The definition this pipeline should have in the store, or "".

    An empty string means the pipeline does not belong there: its chain does
    not validate, and both export routes answer 409 for that rather than let a
    pipeline out that would fail at run time. The caller withdraws whatever was
    published before -- a consumer must not go on reading the last version that
    happened to validate.

    `PIPELINE_PUBLISH_INVALID=true` publishes it anyway, annotated.
    """
    if components is None:
        # imported here so the module can be read without the app's storage
        from apps.dishacled.resources.pipeline_components import (
            load_pipeline_components,
        )

        components = load_pipeline_components(pipeline)

    report = validate_pipeline(pipeline, components)
    if not report.is_valid and not _publish_invalid():
        log.warning(
            f"Not publishing pipeline {pipeline.get('_id')}: {report.summary} "
            "Set PIPELINE_PUBLISH_INVALID=true to publish it anyway."
        )
        return ""

    publish_catalog_entries(components.values())
    return definition_turtle(pipeline, components, report)


def publish_catalog_entries(components) -> None:
    """Make sure the store describes the components this definition names.

    A definition that references a component by IRI alone is only readable if
    something in the store says what that IRI is, and Elody is the only thing
    that knows -- the component was discovered on GitHub or declared in the
    interim contract catalog (see `catalog.py`). Publishing them alongside the
    definition is what lets the fragment go away.

    It is a side effect of a save, so it cannot be allowed to fail one: a
    catalog write that does not happen leaves the description in the fragment,
    where it has always been.
    """
    try:
        catalog.publish_components(components)
    except Exception as error:  # pragma: no cover - defensive
        log.warning(f"Could not publish catalog entries: {error}")


def publish_pipeline(pipeline, *, components=None) -> bool:
    """Write a saved pipeline's definition into the store.

    Returns whether the definition is now in the store. A pipeline that is
    refused, or one the store never accepted, answers False -- the caller is a
    crud hook, which reports rather than raises.
    """
    graph = graph_for((pipeline or {}).get("_id"))
    if not graph or not _endpoint():
        return False

    try:
        ttl = definition_for_store(pipeline, components=components)
    except IncompletePipeline as incomplete:
        # not a withdrawal: leave the store exactly as it is
        log.error(f"Not publishing pipeline {pipeline.get('_id')}: {incomplete}")
        return False
    if not ttl:
        # withdraw an earlier version, so nothing goes on reading a definition
        # of this pipeline that no longer validates
        _delete_graph(graph)
        return False
    return _put_graph(graph, ttl)


def republish_saved_pipeline(storage, id) -> bool:
    """Publish the entity with this id, if it is a pipeline.

    The crud hooks are the natural home for this, but only the v2 generic
    resources call them and this client serves the v1 routes (see
    `resources/entity.py`), so the routes that actually save a pipeline call
    this directly. Reading the entity back is deliberate: the routes that save
    one part of it -- a relation, a metadata key -- answer with that part, and
    a definition has to be built from the whole document.
    """
    try:
        document = storage.get_item_from_collection_by_id("entities", id)
    except Exception as error:
        log.warning(f"Could not read pipeline {id} back for publication: {error}")
        return False
    if not document or document.get("type") != "pipeline":
        return False
    return publish_pipeline(document)


def unpublish_pipeline(pipeline) -> bool:
    """Drop a deleted pipeline's definition from the store."""
    graph = graph_for((pipeline or {}).get("_id"))
    if not graph or not _endpoint():
        return False
    return _delete_graph(graph)


def _put_graph(graph, ttl) -> bool:
    return _request(
        "put",
        graph,
        data=ttl.encode("utf-8"),
        headers={"Content-Type": "text/turtle; charset=utf-8"},
    )


def _delete_graph(graph) -> bool:
    # A graph that was never written is already in the state asked for, and
    # Fuseki says so with a 404.
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
        log.warning(f"Triple store {endpoint} unreachable: {error}")
        return False

    if response.status_code in (200, 201, 204):
        return True
    if accept_missing and response.status_code == 404:
        return True
    if response.status_code in (401, 403):
        log.warning(
            f"Triple store {endpoint} refused the write ({response.status_code}); "
            "set PIPELINE_STORE_USER / PIPELINE_STORE_PASSWORD."
        )
        return False
    log.warning(
        f"Triple store {endpoint} answered {response.status_code} for "
        f"{method.upper()} <{graph}>: {response.text[:200]}"
    )
    return False
