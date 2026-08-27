# A component is a processor, not a repository

Elody discovers components by searching GitHub for the `rdfc-processor` topic
(`GITHUB_PROCESSOR_TOPIC`). A repository, though, is not a component: it is
where one or more components live.

| Repository | Processors it declares |
| --- | --- |
| `file-utils-processors-ts` | GlobRead, FolderRead, ReadFile, FileWriter, Substitute, Envsub, UnzipFile, GunzipFile |
| `http-utils-processor-ts` | HttpFetch, HttpServer |
| `log-processor-ts` | LogProcessorJs, SendProcessorJs |
| `sparql-ingest-processor-ts` | SPARQLIngest |

So a component is identified by the processor class it is, in the repository it
came from:

```
rdf-connect--file-utils-processors-ts--GlobRead
<owner>-----<repo>-------------------  <class>
```

and the class travels with the document as `data.componentIri`
(`https://w3id.org/rdf-connect#GlobRead`). Everything that needs a shape —
the config form, the ports, both TTL exports, the published catalog
description, the `shui.ttl` endpoint — reads it from there.

## Why it is stored rather than derived

It used to be derived, in two places independently: the form derivation took
the first `rdfc:*ImplementationOf rdfc:Processor` subject in graph order, and
the export took one out of a `set`. Neither is an order — a repository
declaring eight processors yielded a different one per process, and the two
paths could pick differently in the *same* process.

That failed silently in the worst way. Selecting `file-utils-processors-ts` and
filling in `globPattern` could export `a rdfc:Envsub`, whose shape has no
`globPattern`, so `emit_config_values` skipped the value: a pipeline that
looked configured and carried no configuration. Observed with the real
repository, across five hash seeds, before the fix:

| Seed | form offered | export wrote |
| --- | --- | --- |
| 0 | GlobRead | UnzipFile |
| 1 | FolderRead | GlobRead |
| 2 | GunzipFile | GlobRead |
| 3 | FolderRead | SendProcessorJs (log-processor-ts) |
| 4 | Envsub | ReadFile |

A component that knows what it is cannot disagree with itself.

## What the rules are now

* **The class list is sorted** (`processor_classes_from_ttl`), so a
  repository-level id resolves the same way everywhere and forever.
* **A shape is enough.** A file with no `ImplementationOf` triple falls back to
  the classes its `sh:NodeShape`s target
  (`shape_target_classes_from_ttl`) — otherwise a processor described by a
  shape alone would lose its config form along with its identity. No
  `owl:imports` is synthesised for such a file: nothing said it defines a
  processor.
* **Properties, ports and form are one class's.** They used to be pooled across
  every shape in the repository, which is where `http-utils-processor-ts` got a
  port list mixing HttpFetch's and HttpServer's.
* **`owl:imports` names the file the class is declared in**, not every TTL file
  in the repository that happens to declare something.
* **The repository is still named**, in the `repository` metadata key; the
  `name` key is now the class (`GlobRead`), which is also what a step is
  slugged from.

## Compatibility

Pipelines saved before this change hold bare `owner--repo` keys in their
`hasProcessor` relations. Those still resolve — to the first class the
repository declares, in IRI order — so an old pipeline opens and exports
instead of losing its steps. For a single-processor repository that is exactly
the same component as before. For a multi-processor one it is a guess, the same
guess as before but now a stable one: what the user meant was never recorded,
so nothing can recover it. Re-pick the processor on those steps.

Listings are per class and deliberately light — `componentIri` and the
repository metadata, no `rawTtl`, no form, no ports. The frontend fetches a
component by id as soon as it needs its config or its ports
(`dishacledResolver.resolveProcessorConfig`), so a picker page does not pay for
shapes nobody asked to see.

## What discovery costs, and what happens when it runs out

The classes a repository declares are only in its files, so listing a page
reads them: one tree call plus one call per *source* TTL file per repository.
For a page of twenty that is around 40 GitHub calls, and **anonymous GitHub
allows 60 an hour**. Rendering a pipeline's processor panel costs another lookup
per step. So:

* **Set `GITHUB_TOKEN`.** It needs no scopes for public repositories and lifts
  the budget to 5000/hour. It is passed into collection-api by
  `docker-compose.yml` and documented in `.env.dist`; before this it was not
  wired through at all, so setting it had no effect.
* **Fixtures are not read.** TTL files under `test/`, `tests/`, `example(s)/`,
  `doc(s)/`, `fixture(s)/` and `__tests__/` are not processor definitions.
  Skipping them took `shacl-processor-ts` from nine files to one and
  `rml-processor-jvm` from three to one — and keeps their shapes out of the
  component's `rawTtl`, where they never belonged.
  `PROCESSOR_TTL_EXCLUDED_DIRS` overrides the list.
* **A failed lookup no longer deletes the component.** A `hasProcessor`
  relation is evidence that its component exists; a 403 or a DNS failure is not
  evidence that it does not. The store used to answer `{}`, which emptied the
  whole processor list of a pipeline until the next refresh. It now falls back
  to what the id itself says — the class as the name, the repository, and a
  description saying it could not be read — with `data.unresolved: true` and no
  `rawTtl`, so both exports skip it rather than emitting a stage with no class.
  A **404 is still nothing**: that repository really is absent, and inventing a
  row would hide a broken relation instead of showing it.

Successful responses are cached for an hour (`requests_cache`), and the detail
view reuses what the listing already fetched. Error responses are not cached,
which is why a retry can succeed where the previous call failed.

## Three things make it fast enough to use

**One parse per file.** Every derivation -- the class list, the config form, the
properties, the ports, the shape the export writes with -- parsed the file
again, so a repository declaring eight processors parsed its own turtle dozens
of times. `shacl/graphs.py` holds one parsed graph per document text, shared
read-only. This was the dominant cost: `ShuiFormBuilder.__init__` alone
accounted for 47 seconds of cumulative parsing on a single page, and because
parsing holds the GIL it also flattened the concurrency below.

**A listing row is finished when it is served.** `processorConfig` is in the
picker's own fragment and the graphql resolver builds it from
`data.properties`, *fetching the whole entity per row* when the row does not
carry them (`dishacledResolver.resolveProcessorConfig`). Serving light rows
therefore bought nothing and cost one GitHub round trip per row -- forty rows,
forty repository reads. The listing already has the files, so it describes what
it found. One tree, one download per source file, one manifest lookup, per
repository, whatever the number of processors in it.

**Two rounds, not a chain per repository.** `_components_of_all` reads every
tree and manifest, then every file: two rounds of independent calls whatever
the page size, bounded by `COMPONENT_FETCH_WORKERS` (default 16, capped at 64,
and a malformed value falls back rather than failing the import). Fanning out
*per repository* instead would nest one pool inside another, and then the bound
bounds nothing -- eight repositories with one file each is sixteen sockets.
Order is preserved (GitHub's ranking for a page, relation order for a pipeline)
and one item's failure yields nothing for that item -- logged, not swallowed --
instead of emptying the page.

Measured on a page of twenty repositories against a 150ms API:

| | calls | wall clock | CPU |
| --- | --- | --- | --- |
| serial, parse per row, light rows | 61 | 9.86s | 0.41s |
| + full rows (the resolver's refetch gone) | 61 | 11.07s | — |
| + one parse per file | 61 | 2.36s | 0.87s |
| + two flat rounds, 16 workers | 61 | **1.63s** | 0.82s |

The middle row is the point: describing rows properly made *this* endpoint
slower while removing forty entity fetches from the page as a whole. The
picker's own numbers are the ones to trust here, not the endpoint's.

## Where it lives

| Piece | Where |
| --- | --- |
| Class discovery | `shacl/contracts.py` — `processor_classes_from_ttl`, `shape_target_classes_from_ttl` |
| Ids, identity, listing | `storage/dishacled_httpstore.py` — `_split_component_id`, `_identify_as_component`, `_components_of` |
| Shape for a class | `serializers/pipeline_ttl_serializer.py` — `_ShapeIndex.from_ttl(raw_ttl, target_class)` |
| Form for a class | `shacl/shui.py`, `shacl/form.py` — `target_class` |
| Failure fallback, discovery cost | `storage/dishacled_httpstore.py` — `_unresolved_component`, `_is_source_ttl` |
| Tests | `tests/test_component_identity.py`, `tests/test_component_resilience.py` |
