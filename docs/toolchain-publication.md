# Publishing pipeline definitions to the central triple store

Saving a pipeline in Elody writes its `tcs:PipelineDefinition` into the central
triple store, in the same request. (Since
[pipeline-storage.md](pipeline-storage.md) the store is the *only* copy; this
document describes the write and the conventions it follows, which are
unchanged.) That is what makes an Elody-composed
pipeline visible to the rest of the toolchain: in the demonstrator architecture
(`docs/architecture drawing for demonstrator.drawio`, 2026-08-12) the frontend
and the services do not call each other — they meet in one store. A pipeline
that exists only behind `GET /pipelines/<id>/definition.ttl` is a pipeline the
generator cannot reach.

The runnable RDF-Connect `export.ttl` is **not** published: it is a build
artefact of the generator, not an input to it (see `toolchain-export.md`).

The components a definition *names* are published too, into a catalog graph of
their own — see [component-catalog.md](component-catalog.md). That is what makes
a definition's `prov:specializationOf <component>` resolvable without the
catalog fragment travelling inside it.

## What lands where

| | |
| --- | --- |
| Endpoint | `PIPELINE_GSP_ENDPOINT` — a Graph Store Protocol endpoint (`/store/data` on the local Fuseki) |
| Named graph | `<PIPELINE_GRAPH>/<pipeline id>`, e.g. `http://mu.semte.ch/graphs/pipeline-definitions/880db441-…` |
| Write | `PUT` the whole graph on save, `DELETE` the graph on delete |
| Credentials | `PIPELINE_STORE_USER` / `PIPELINE_STORE_PASSWORD`, Basic auth |

Reading them back is a cross-graph query, so a consumer needs no list of
pipelines:

```sparql
SELECT ?g ?pipeline WHERE {
  GRAPH ?g { ?pipeline a <https://w3id.org/toolchain#PipelineDefinition> }
}
```

## Why one graph per pipeline

The alternative is every definition in one shared graph, maintained with
`DELETE`/`INSERT`. It founders on what a definition actually looks like:
configuration blocks, package entries and whole shape sub-graphs hang off blank
nodes, and component descriptions are shared between pipelines. "Delete the
triples of pipeline X" then has no reliable SPARQL spelling — a traversal deep
enough to catch the blank nodes is also deep enough to take a component
description another pipeline still needs.

With a graph per pipeline, republishing is one atomic replace and deletion is
exact, both in a single protocol call. The cost is that a consumer writes
`GRAPH ?g` — which the query above does anyway.

The naming still needs confirming with Koen and Thomas; it is one environment
variable either way.

## An invalid chain is not published

Both export routes answer **409** for a pipeline whose shapes do not line up,
so that a chain which would fail at run time does not leave the system. The
store is where it would leave the system furthest, so the same rule applies: an
invalid pipeline is not published, and **whatever was published for it before
is withdrawn**.

Withdrawing matters as much as refusing. Without it a consumer would go on
compiling the last version that happened to validate, while the pipeline in
Elody has since been broken — the store would be quietly stale in exactly the
case where being stale is worst.

Since the store became the only copy, this has a sharper consequence: a
pipeline whose chain does not validate is not *anywhere*, so Elody cannot list
or open it either. See the known limits in
[pipeline-storage.md](pipeline-storage.md).

`PIPELINE_PUBLISH_INVALID=true` is the store's `?force=true`. The download
route lets a human force a broken export; nobody is standing at a save to be
asked, so the decision is taken once, in configuration. A forced definition
carries the violations as `rdfs:comment` on the pipeline — the turtle comment
block the download prepends is syntax, which a store does not keep, and a
consumer that never saw the refusal has to be able to find the warning in the
data.

A chain that merely *cannot be checked* — a component with no declared shapes —
is a warning, not a refusal, and publishes. Most components carry no contract
at all; refusing those would leave the store empty for every pipeline that is
undescribed rather than broken.

## `owl:imports` and the base

A component's `owl:imports` is written relative (`./node_modules/@rdfc/…`),
because it resolves against where the runner mounts the pipeline — not against
anything Elody knows. RDF has no relative IRIs, though: a store resolves them
the moment it parses the document, and a Graph Store PUT would resolve them
against the endpoint's own URL, producing
`http://triplestore:3030/store/node_modules/…`, which names nothing.

So the document is resolved before it is sent, against the base the generator
itself parses with, `file:///workspace/pipeline/` (rdfine `GraphReader`; see
`toolchain-export.md`). The store then holds the same IRIs the generator would
derive from the file. `PIPELINE_IMPORT_BASE` overrides it.

This is the only difference between the published graph and the downloaded
file. Read the download with the same base and the two are isomorphic — which
is asserted, both in the test suite and by hand:

```
stored triples : 218
definition.ttl : 218
isomorphic     : True
```

## Where it is triggered

**Superseded.** Publishing is no longer something that happens *after* a save:
the pipeline type is stored in the triple store, so the write itself is the
publication. See [pipeline-storage.md](pipeline-storage.md). What remains of
this module is `definition_for_store` — the rule about what belongs in the store
and what does not — which the storage engine calls on every write, plus
`publish_pipeline`, which the one-shot migration script uses.

The paragraph that used to be here said the crud hooks never fire on this
client's routes, and that was wrong. `/entities/<id>` is served by the v1
resource, which does write straight to storage — but the editor does not save
through it. It saves relations and metadata, and those routes come from the
framework's `resources/elody/*` layer, which goes through the **v2** write
family and therefore does call the hooks. The `PublishesPipelines` overrides
written on that assumption are gone.

## The local store

`docker-compose/triplestore/fuseki-config.ttl` serves two services over one
dataset:

```
/alerts/sparql   query     the alert fixture, unchanged
/alerts/get      gsp-r
/store/sparql    query     the central store
/store/update    update
/store/data      gsp-rw    ← Elody publishes here
```

`/alerts` stays read-only: that graph is written by the pipeline, not by us.

**The dataset is on disk** (TDB2 in the `triplestore` volume). It used to be an
in-memory one, reseeded on every start, which was right while the store was a
mirror of Mongo — losing it cost nothing, because the next save republished.
Since [pipeline-storage.md](pipeline-storage.md) the store is the only copy, so
that same property meant **restarting the container lost every pipeline in the
client**; the local stand-in was the one thing making the store less reliable
than the database it replaced.

The alert fixture keeps the old behaviour: `docker-compose/triplestore/
entrypoint.sh` starts Fuseki and then PUTs `alerts.ttl` over the errors graph,
so that graph is reseeded on every start while everything else survives. A
whole-graph PUT is a replace, so restarting cannot accumulate copies of it, and
an edit to `alerts.ttl` takes effect on the next start. It goes in over HTTP
because a Jena assembler has no way to seed one named graph of a persistent
dataset.

The volume is mounted at `/fuseki`, the image's own volume, rather than at
`/fuseki/databases`: an empty named volume on the inner path arrives root-owned
and Fuseki (uid 100) cannot write to it — `FusekiConfigException: Not writable`.

Pipelines saved before this change were in memory and are gone with the restart
that dropped them. `scripts/publish-pipelines.py` republishes them from the
Mongo documents the migration left in place.

Fuseki restricts `/*/data` and `/*/update` to the admin user out of the box,
which is why publishing carries credentials while reading does not. Write
authorisation on redpencil's shared store is still to be agreed.

## Configuration

```
PIPELINE_GSP_ENDPOINT      Graph Store Protocol endpoint
PIPELINE_GRAPH             base IRI of the per-pipeline named graph
PIPELINE_STORE_USER        Basic-auth credentials for the write endpoint
PIPELINE_STORE_PASSWORD
PIPELINE_PUBLISH_INVALID   publish a failing chain anyway, annotated (false)
PIPELINE_IMPORT_BASE       base the relative owl:imports resolve against
PIPELINE_EXPORT_BASE_URI   root the pipeline's own IRIs are built on
SPARQL_ENDPOINT            query endpoint, for consumers
```

The catalog graph has a configuration block of its own, which defaults to this
one: see [component-catalog.md](component-catalog.md).

Unset `PIPELINE_GSP_ENDPOINT` or `PIPELINE_GRAPH` and nothing is published — an
environment without a store simply does not have this behaviour.

`PIPELINE_EXPORT_BASE_URI` is now configured rather than taken from the request
(`pipeline_base_uri()`), so the downloaded definition and the published graph
name the same things, and a save that does not arrive over HTTP still mints
stable IRIs.

## Tests

`tests/test_pipeline_publication.py` covers publish on save, republish on
update, withdrawal on delete, the invalid-chain behaviour both ways, the import
base, the credentials, and that neither an unreachable nor a refusing store
breaks a save. The store is faked at the `requests` boundary, so the suite
needs no triple store.
