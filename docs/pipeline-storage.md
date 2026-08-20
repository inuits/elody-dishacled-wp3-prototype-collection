# Pipelines live in the triple store

A pipeline is the one thing Elody and the toolchain both hold an opinion about,
so it has exactly one home: the central store. There is no Mongo copy any more.
Elody's list and detail views render a `tcs:PipelineDefinition` read back out of
the store, and every edit — a config value, a connection, a rename, a delete —
is written there. Koen's CLI and Elody see the same triples in the same named
graph, so editing in one is visible in the other after a refresh.

This is the second half of [toolchain-publication.md](toolchain-publication.md),
which added the write. That one described the store as a mirror kept in step
after the fact; it is not a mirror any more, it is the store.

## The two directions

| | |
| --- | --- |
| entity → turtle | `serializers/pipeline_definition_serializer.py` (unchanged; it is also what `definition.ttl` serves) |
| turtle → entity | `serializers/pipeline_serializer.py` (`from_sparql_to_elody`) |
| which one, when | `object_configurations/pipeline_configuration.py` → `serialization(from, to)` |

The reverse mapping consults **nothing outside the graph**. No component is
looked up on GitHub to read a pipeline: everything it needs travels with the
definition, because the export ships a catalog fragment for the components it
uses. That is what makes a read cheap and what makes it keep working when a
repository moves.

```
<pipeline> a tcs:PipelineDefinition ;    ->  the entity
    dct:identifier "<id>" ;              ->  _id
    rdfs:label / rdfs:comment .          ->  metadata name / description

<step> a tcs:InstancePipelineComponent ;      ->  one hasProcessor relation
    prov:specializationOf <component> ;       ->  the relation key, via the
                                                  component's dct:identifier
    p-plan:hasInputVar [ tcs:embedded [...] ] ->  relation metadata, keyed by
                                                  the sh:name in the fragment
    tcs:readsFrom / tcs:writesTo <channel> .  ->  connections.<port>.from
```

### `dct:identifier`, and why the definition carries it

A definition names a component by IRI (`prov:specializationOf rdfc:Validate`),
which is right for the toolchain but not enough to read back: Elody addresses
the same component as a *document*, `rdf-connect--shacl-processor-ts`, and that
is the key its `hasProcessor` relation is stored under. So the export now emits
`dct:identifier` on the pipeline, on each component in the catalog fragment, and
on each dataset.

The alternative was resolving the IRI by listing components from GitHub on every
read, which is slower and loses a step the moment a repository moves. One extra
triple per subject buys a store that is a complete source of truth. It is
flagged for Thomas in [toolchain-open-questions.md](toolchain-open-questions.md)
§4b, since it is a non-`tcs:` term in a shared document.

**`?catalog=false` therefore produces a definition Elody cannot read back** — it
drops the fragment, and with it the identifiers. Publication always ships the
fragment; the download parameter is for hand-off, not for storage. There is a
test that states exactly this.

### What deliberately does not round-trip

* **Per-connection validation verdicts.** `connections.<port>.state` was
  stamped onto stored relations by a pre-crud hook. It is not published: the
  store is shared with the toolchain, and our reading of a chain has no
  business looking like the pipeline's own. It is computed on demand instead —
  `GET /pipelines/<id>/validation`, which is what the editor already calls.
  ("Draft/undo state" is out of scope for the same reason.)
* **Authored step order.** A plan is a set of steps; the only order the
  definition records is the one the channels describe. So steps come back in
  **dependency order** — producers before what they feed, alphabetical
  tie-break — which is stable across reads. Graph-iteration order would make
  the processor list shuffle between two reads of the same pipeline.
* **Metadata no config shape declares.** A definition can only carry a value a
  predicate exists for. Such a key was never published, so it cannot come back.
* **Typed values keep their lexical form.** `"10000"` typed as an integer comes
  back as `"10000"`, not `10000` — the shape types it on the way out and will
  type it again. Booleans are the exception: a checkbox is stored as a boolean,
  and `"true"` would not render as a ticked box.

## The engine grew a write side

`collection-api/api/storage/sparqlstore.py` served one shape of resource: a
subject and its properties, several to a named graph, read-only. A pipeline is
not that shape — it is several subjects and mostly blank nodes — so the engine
now also serves **graph-scoped** collections, which a configuration opts into:

```python
crud()["sparql"] = {
    "endpoint": ...,            # query endpoint, for CONSTRUCT
    "gsp_endpoint": ...,        # Graph Store Protocol endpoint, for writes
    "graph": ...,               # base IRI; one document is <graph>/<id>
    "graph_per_item": True,
    "user": ..., "password": ...,
    "target_class": "https://w3id.org/toolchain#PipelineDefinition",
    "identifier_predicate": "http://purl.org/dc/terms/identifier",
}
```

* **Read.** A document is addressed, not searched for: its id names its graph.
  Listing runs one `SELECT` for the graphs under the base and then one
  `CONSTRUCT` per graph — merging them into a single query would fuse the blank
  nodes of two different pipelines. The base filter is what keeps the alert
  graph, which shares the dataset, out of the collection.
* **Write.** A graph is also what the Graph Store Protocol addresses, so one
  `PUT` replaces a document and one `DELETE` removes it. There is no generic
  elody-to-RDF mapping and inventing one in the engine would put the vocabulary
  in the wrong place, so a writable collection is one whose serializer answers
  `from_elody_to_sparql` with turtle.
* **An empty answer withdraws the document.** That is how an incompatible chain
  stays out: the exports answer 409 rather than let one out, and the store must
  not keep the last version that happened to validate either. See
  `definition_for_store` in `pipeline/publication.py`.
* **Sub-document edits are read-modify-write.** A metadata patch, a relation
  patch, deleting one key — the unit the store can write is a graph, so there is
  no partial update to be had. Last writer wins, which is what the Graph Store
  Protocol says anyway.
* **A refused or unreachable write raises** (`ExternalStorageError`) instead of
  being swallowed. A save that did not happen must not answer as though it had.

Not implemented, on purpose: no history collection (versioning a definition is
out of scope, and there is no second collection to put one in) and no etag
precondition (a whole-graph replace has no basis for one).

## Routing: how `/entities/<id>` reaches the store

A route names a *collection*, and `entities` holds every type this client has,
so the collection cannot say which engine serves one of them. Reads have
resolved externally stored types by type for a while — that is how the filter
path finds alerts — and `storage/routing.py` now offers the same resolution to
the paths that write:

```python
storage, collection = storage_for(document_type="pipeline",
                                  collection="entities",
                                  default=self.storage)
#  -> (SparqlStorageManager(), "pipelines")
```

The pipeline configuration declares `"collection": "pipelines"`, and that is
what makes it findable: the engine is addressed under a name of its own while
the routes stay `/entities/...`, so **no frontend change was needed**.

Two places needed more than that:

* **A blind lookup.** `GET /entities/<id>` has no type to route on — that is
  what it is asking for. `_abort_if_item_doesnt_exist` and the
  collection-resolver branch of `_check_if_collection_and_item_exists` now fall
  back to `external_members_of(collection)` after the database has said no.

  Membership there is **declared, not inferred** — the configuration says
  `"routed_through": "entities"`. Inferring it ("external, and not this
  collection") would have dragged in every wrapper type a client happens to
  have: this client's `githubProcessors`, vliz's vocabularies, vlacc's
  cantook/boekenbank/muziekweb. Those have routes of their own, so a plain 404
  on `/entities` would have cost an HTTP round trip to each of them, and an id
  that happened to exist as one would have been answered from the entities
  route. A client that declares nothing gets an empty list and no change at
  all.
* **Detail enrichment.** `_set_entity_mediafile_and_thumbnail` and
  `_add_relations_to_metadata` are routed too. A store-backed entity has no row
  to read mediafiles off, and asking anyway used to fail on the missing document
  rather than answer "no mediafiles".

### `SCHEMA_TYPE` is not `storage_type`

`PipelineConfiguration.SCHEMA_TYPE` stays **`"elody"`**, and this is worth
knowing before changing it: the framework converts incoming content to the
configuration's `SCHEMA_TYPE` before validating it
(`_get_content_according_content_type`, `v2=True`). A pipeline *document* is an
ordinary elody document — metadata and relations, the shape the form saves. The
turtle is the *storage* format, which the engine asks the serializer for by
name. Setting `SCHEMA_TYPE = "sparql"` makes the resource layer hand turtle to
the validator, which fails with `'str' object has no attribute 'get'`.

The consequence is the oddly-named `from_elody_filter_to_elody_filter` on the
serializer. The framework looks a list filter's translator up as
`from_<spec>_filter_to_<SCHEMA_TYPE>_filter`, and every external type in every
client follows that convention — vliz's `vocab`, vlacc's `cantook`, this
client's `github`. Renaming the lookup after `storage_type` instead would read
better here and would break all of them, so the convention wins and the
serializer carries the awkward name.

## Which routes the editor actually uses

Worth recording, because it corrects a claim in the previous ticket's notes: the
crud hooks **do** fire on the path the builder saves through. `/entities/<id>`
is served by the v1 `DishacledEntityDetail`, which writes straight to storage —
but the editor does not use it. It saves relations and metadata, and those
routes are served by the framework's `resources/elody/*` layer, which goes
through `resources/base/document.py` and therefore the **v2** write family
(`put_item_from_collection`, `patch_item_from_collection_v2`, `delete_item`) —
the hook-calling one. That is why the old pre-crud-hook validation state was
being stamped after all.

So the engine implements the v2 family too, hooks included. What it leaves out
of them is the parts that are properties of a database, listed above.

`PublishesPipelines` in `resources/entity.py` is gone with the same reasoning:
writing *is* publishing now, so there is nothing left to mirror after a save.

## Migrating what was already saved

`scripts/publish-pipelines.py`, run in the collection-api container:

```
$ docker cp scripts/publish-pipelines.py <collection-api>:/tmp/
$ docker exec -w /app <collection-api> sh -c \
      'PYTHONPATH=/app/api python /tmp/publish-pipelines.py'
3 pipeline(s) in Mongo
  880db441-…  published  212 lines
  b49aeadd-…  published  94 lines
  aa35fa5e-…  skipped    refused: the chain does not validate

2 published. Mongo documents left in place; re-run with --delete to remove
the ones that published.
```

Publishing is the default and deleting is not, so a run can be repeated and
checked before anything is removed. `--delete` removes only the Mongo documents
that demonstrably reached the store — a pipeline that was refused is the one
case where the Mongo document is still the only copy there is. Fix its chain in
the builder and re-run.

## Verified end to end

Against the running stack, with the Mongo document deleted first so a read can
only come from the store:

```
GET /entities/<id>                     200  pipeline, 3 relations, in dependency order
  config values                             "Validation failure is fatal": true  (boolean kept)
POST /entities/filter  type=pipeline   200  2 pipelines, the invalid one absent
PATCH /entities/<id>/relations         200  edit lands in the store and reads back
POST /entities                         201  created only in the store (Mongo: 0), connection intact
GET /pipelines/<id>/export.ttl         200  rdfc pipeline built from the store-backed entity
GET /pipelines/<id>/definition.ttl     200
GET /pipelines/<id>/validation         200
DELETE /entities/<id>                  204  graph dropped; the next GET is 404
```

## Tests

| | |
| --- | --- |
| `tests/test_pipeline_serializer.py` | the round trip: entity → definition → entity, including the fixed point (read and re-export is isomorphic) and what deliberately does not survive |
| `collection-api/api/tests/unit/storage/test_sparqlstore.py` | graph-scoped reads, the write side, sub-document edits, the v2 family with its hooks |
| `collection-api/api/tests/unit/storage/test_routing.py` | `storage_for`, `external_members_of` |

## One limitation that is not ours to fix

The `elody` pip package's policy helper (`elody/policies/helpers.py`,
`get_item`) resolves a document by asking `StorageManager().get_db_engine()` —
the database, always. Every framework authorization policy that needs the item
goes through it (`generic_object_detail_policy`,
`generic_object_relations_policy`, `generic_object_metadata_policy`, the
mediafile ones), so **a client that stores an entity type externally and uses
one of those policies will get a 404 before the resource is reached.**

It does not bite here: this client authorizes with `all_allowed_policy`. But it
is the one place where "an entity type can live in an external store" is not
true framework-wide, and it cannot be fixed from this repository — the helper
ships in a published package. Worth a ticket against `elody` if another client
wants this.

## Known limits

* **No tenant isolation.** The store is not partitioned, and a definition is
  shared with services that have no tenant at all, so the configuration says so
  (`tenant_id_resolver` → `""`) rather than implying otherwise. Generic
  multi-tenant SPARQL is out of scope.
* **No history and no optimistic concurrency.** Two editors saving the same
  pipeline: the last one wins, silently.
* **Two assumptions are baked into the engine rather than configured.** RDF is
  read and written as turtle (both directions; the read side already was), and a
  document's graph is `<graph>/<id>`. Each is one config key away from being
  general — `content_type` and a graph template — and neither has a second
  consumer yet, so they are stated here instead of guessed at.
* **A pipeline whose chain does not validate is not in the store at all**, so
  Elody cannot list or open it either. That is the same rule the exports have
  always applied, but it now means the pipeline is invisible rather than merely
  unexportable. `PIPELINE_PUBLISH_INVALID=true` is the escape hatch, and the
  builder is where the chain gets fixed.
