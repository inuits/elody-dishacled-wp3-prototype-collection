# Elody's components in the shared catalog graph

A pipeline definition only *names* its components; what those names mean —
identity, runner, package coordinates, config/input/output shapes — lives in a
catalog. The toolchain has one (`catalog.ttl` and
`demonstrator-catalog-extensions.ttl` in thcarsten/toolchain-specification), but
it cannot know what Elody discovers live on GitHub or declares in the interim
contract catalog, so until now Elody shipped those descriptions *inside* every
definition it exported — the catalog fragment, `?catalog=false` to drop it.

That works and it merges, but it has two costs. The same component is described
once per pipeline that uses it, and a service reading the store sees **no
components at all** unless it opens the pipelines — which is exactly what a
discovery service (Koen's `app-dcat-catalog`) is supposed not to have to do.

So the descriptions are published as well. This resolves §4 of
[toolchain-open-questions.md](toolchain-open-questions.md) the way the
demonstrator architecture implies: one shared catalog, every service reads it,
Elody contributes to it.

## What lands where

| | |
| --- | --- |
| Endpoint | `CATALOG_GSP_ENDPOINT`, defaulting to `PIPELINE_GSP_ENDPOINT` — same store, same dataset |
| Named graph | `<CATALOG_GRAPH>/<url-encoded component IRI>`, e.g. `http://mu.semte.ch/graphs/catalog/https%3A%2F%2Fw3id.org%2Frdf-connect%23Validate` |
| Write | `PUT` the whole graph, `DELETE` it to withdraw |
| Credentials | `CATALOG_STORE_USER` / `CATALOG_STORE_PASSWORD`, defaulting to the `PIPELINE_STORE_*` pair |
| Vocabulary | the discovery spec's — `tcs:PipelineComponent`, `tcs:configShape` / `inputShape` / `outputShape`, `spdx:Package`, `owl:imports` |

Reading them back needs no list of components:

```sparql
SELECT ?component WHERE {
  GRAPH ?g { ?component a <https://w3id.org/toolchain#PipelineComponent> }
}
```

One component's description:

```turtle
demo:ThresholdMonitorCm a tcs:PipelineComponent, dcat:Resource ;
    dct:identifier "local--threshold-monitor-cm" ;
    rdfc:jsImplementationOf rdfc:Processor ;
    rdfs:label "Threshold monitor (cm)" ;
    rdfs:description "Emits an oslc:Error when a measurement exceeds …" ;
    dct:requires rdfc:NodeRunner, [ a spdx:Package ; spdx:name "@demo/monitor" ] ;
    owl:imports <file:///workspace/pipeline/node_modules/@demo/monitor/processor.ttl> ;
    dcat:qualifiedRelation
        [ a dcat:Relationship ; dcat:hadRole tcs:configShape ; dct:relation demo:ThresholdMonitorShape ],
        [ a dcat:Relationship ; dcat:hadRole tcs:outputShape ; dct:relation <http://lblod.data.gift/shapes/ErrorShape> ] .

rdfc:NodeRunner a rdfc:Runner, tcs:PipelineComponent ; dct:requires rdfc:Orchestrator .
```

`rdfc:jsImplementationOf` is carried deliberately: it is the join key between a
repository and its catalog entry (`component_iri_from_ttl`), and a consumer
reading the graph on its own has no processor file to read it off. So is
`dct:identifier` — Elody addresses the same component as a *document*
(`rdf-connect--shacl-processor-ts--Validate`), and that is the key a pipeline's
`hasProcessor` relation is stored under. It is flagged for Thomas in
[toolchain-open-questions.md](toolchain-open-questions.md) §4b along with the
same triple on a definition.

What that document id names is one *processor*, not one repository — a
repository declaring eight processors is eight components, each with its own
id, shape and ports. See
[component-identity.md](component-identity.md).

## Datasets go in as datasets

A `dcat:Dataset` is a source of data, not a deployable component: it has no
runner, no package and nothing to install, and describing it as a
`tcs:PipelineComponent` would offer the generator a step it cannot start. It is
published all the same — a DCAT catalog is more about datasets than about
processors — as `dcat:Dataset`, with its identifier, label and the output shape
a downstream component can be checked against. The ownership check asks about
both types, since the question is whether anything outside Elody's graphs
already describes this thing, not what it is.

## A component nothing installs asks for no runner

A `dcat:PipelineComponent` is not necessarily an RDF-Connect processor. Elody's
own alert visualisation is a step of the pipeline — its input contract is what a
connection into it is checked against — but there is nothing to install and
nothing to start: it reads the alerts out of the store the pipeline writes to
([alert-component.md](alert-component.md)). The semantic.works services and the
LDIO components in the demo catalog are the same kind of thing.

So the description follows what the catalog entry declares. An entry with no
`rdfc:*ImplementationOf` and no deployment coordinates is **not runnable**, and
its description carries neither an implementation predicate nor
`dct:requires <runner>`; what it does carry is the `dcterms:requires` the entry
itself states — the store it reads through, an orchestrator of its own
framework. The alternative was to keep defaulting to `rdfc:NodeRunner`, which
tells the generator to start a Node processor that does not exist: a pipeline
that either fails to come up or comes up doing nothing. Runners are still
declared for the components that actually ask for one.

## One graph per component

The same reasoning as one graph per pipeline definition
([toolchain-publication.md](toolchain-publication.md)): a component description
hangs its packages and whole shape sub-graphs off blank nodes, so "delete the
triples of component X" inside a shared graph has no reliable SPARQL spelling,
while a whole-graph `PUT` is an atomic replace — which is what makes a publish
**idempotent**. Publishing the same component twice leaves the store in exactly
the state one publish leaves it in, so a sweep can be re-run without thinking
about it.

The graph is named after the component's own IRI, percent-encoded. It is the
only identifier every component has — a repository and a contract entry are
joined on it — and it survives a repository being renamed or moved. The local
name alone (`Validate`) would collide between two namespaces declaring the same
class name.

## Elody does not own the catalog

Two rules keep it from claiming to, and the second is the one that had to be
agreed with Thomas:

* **Structurally.** Elody only ever writes graphs under `CATALOG_GRAPH`. A
  triple written by the toolchain catalog cannot be replaced or removed by a
  write from here, whatever happens.
* **Editorially.** Before publishing, Elody asks the store whether the
  component is already described *outside* its own graphs:

  ```sparql
  ASK {
    { GRAPH ?g { <component> a tcs:PipelineComponent }
      FILTER(!STRSTARTS(STR(?g), "<CATALOG_GRAPH>/")) }
    UNION
    { <component> a tcs:PipelineComponent }
  }
  ```

  If it is, Elody does not describe it again — **and withdraws its own earlier
  description of it**, so a duplicate does not outlive the reason for it. The
  toolchain catalog is authoritative for the components it carries; Elody adds
  only what it lacks.

  *Every* base Elody writes is excluded, not just the catalog one. A published
  definition carries the catalog fragment, so the pipeline-definition graphs
  describe components too — and asking only about `CATALOG_GRAPH` made Elody's
  own definitions answer this question about Elody's own components: after the
  first pipeline save the component looked like the toolchain's, publishing
  stopped, and the earlier description was withdrawn. It was silent and
  order-dependent (whichever pipeline was saved first decided which components
  stayed in the catalog), which is why it is worth spelling out here rather
  than only in the code.

The check needs a query endpoint (`CATALOG_SPARQL_ENDPOINT`, defaulting to
`SPARQL_ENDPOINT`). Without one, Elody publishes and says so in the log: the
failure modes are not symmetric — a second description in a graph of its own is
a duplicate a consumer can see and we can withdraw, while a component the
catalog does not describe is a pipeline that does not compile.

## Catalog membership

A component description is not only "here is a component" — it says which
catalog the component belongs to:

```turtle
<https://elody.eu/catalog#ElodyCatalog> a tcs:Catalog ;
    dcat:resource demo:ThresholdMonitorCm .
```

This is not decoration. The toolchain's application profile carries
`tcs:SpecializedComponentIsCatalogedShape`, which requires the component a step
names through `prov:specializationOf` to be a `dcat:resource` of some
`tcs:Catalog`. A description that declares only `a tcs:PipelineComponent` is a
component in no catalog at all, and a definition built on it violates the
profile on **every** Elody-only step — the difference between a definition the
generator compiles and one it rejects. It is emitted by the shared serializer,
so the published graph and the export fragment carry it identically.

The catalog is **Elody's own**, not one of the toolchain's, and that follows
from the ownership rule above rather than being a separate choice: adding
resources to a `tcs:Catalog` the toolchain owns would break editorial ownership
from the inside, leaving a consumer unable to tell which catalog claims a
component — which is the question precedence is decided on. Because the
per-component graphs name the catalog by IRI rather than by blank node, they
union into one catalog with many members, which is what a consumer querying
`?catalog dcat:resource ?component` relies on.

`CATALOG_IRI` overrides it, for the case the demonstrator settles on one shared
catalog subject across its services.

**Components only, and datasets deliberately not.** The same application profile
carries `tcs:CatalogShape`: *every* `dcat:resource` of a `tcs:Catalog` must be a
`tcs:PipelineComponent`. A dataset is not one — the generator cannot start it as
a step — so listing it would trade the violation the registration clears for a
new one. A dataset stays discoverable in the catalog graph by its own
`a dcat:Dataset`, which is what a DCAT consumer looks for anyway. Runners are
left out for a different reason: one is emitted because a component requires it,
nothing specialises a runner, and the toolchain's catalog carries the runners
itself, so listing it here would be an ownership claim with nothing behind it.

## When it is published

| trigger | covers |
| --- | --- |
| saving a pipeline | the components that pipeline names |
| `POST /pipeline-components/catalog` | everything discovery returns |
| `scripts/publish-catalog.py` | the same sweep, without a token |

Saving a pipeline is the one that matters for the definition: a step names its
component by IRI, and until the store describes that IRI the reference is
dangling. It happens inside `definition_for_store` — the same function that
decides whether a definition belongs in the store at all — so every write path
gets it, and it can never fail a save: a catalog write that does not happen
leaves the description in the fragment, where it has always been.

The sweep is what a discovery service needs, because a component nobody has
used in a pipeline yet is still a component the catalog should list. Discovery
is a paged GitHub search, so it is capped: `CATALOG_PUBLISH_PAGES` pages of 20.
`GET /pipeline-components/catalog` lists what a sweep would publish, and into
which graph, without writing anything.

```
$ curl -XPOST .../pipeline-components/catalog
{"components": 12, "outcomes": {"published": 10, "withdrawn": 1, "skipped": 1}}
```

Verified against the running stack, publishing the interim contract catalog into
the local Fuseki and reading it back:

```
publish       : {'published': 6}
republish     : {'published': 6}     <- idempotent: same graphs, same triples
in the store  : PipelineComponent 5 (4 + rdfc:NodeRunner), Dataset 2, Resource 4
                532 triples under http://mu.semte.ch/graphs/catalog/

after saving a pipeline, every step's component resolves:
  demo:ThresholdMonitorCm  -> catalog graph, dct:identifier local--threshold-monitor-cm
  demo:HttpPollerCm        -> catalog graph, dct:identifier local--http-poller-cm
```

`withdrawn` is the ownership rule taking effect: the toolchain catalog has
caught up with that component, so Elody's copy is gone. `skipped` is a document
with no component IRI to describe — a repository under the topic that ships no
processor definition.

## The fragment has not gone away yet

The ticket's last step is to flip the definition export to `?catalog=false` by
default, so a definition references components by IRI only. That is now one
environment variable — `DEFINITION_INCLUDE_CATALOG` — but it is still `true`,
for a reason worth writing down.

**Elody reads its own pipelines back out of the definition**
([pipeline-storage.md](pipeline-storage.md)), and the reverse mapping
deliberately consults nothing outside the graph it was handed: the component's
`dct:identifier` (the key a `hasProcessor` relation is stored under) and its
config shape (the only thing relating a predicate in the definition to the
`sh:name` the form uses) both come from the fragment. Drop the fragment and a
pipeline is published but not readable — there is a test that states exactly
this.

Flipping the default therefore needs the read side to resolve a component from
the catalog graph instead, which is a cross-graph query per component on every
pipeline read. That is worth doing, and it is worth doing **after** the graph
convention is agreed with Koen — the alternative is writing the query twice.
Until then:

* the store holds both, and they agree: the fragment and the published graph
  are built by the same serializer
  (`serializers/component_catalog_serializer.py`), which is asserted rather
  than assumed — a definition without its fragment, merged with the catalog
  graphs of the components it names, is *isomorphic* to the definition with it;
* a consumer that reads the catalog graph can already ignore the fragment
  entirely: the two describe the same triples, so the union is the fragment;
* `?catalog=false` on the download already works for hand-off, and
  `DEFINITION_INCLUDE_CATALOG=false` flips the default for an environment whose
  consumers all read the store.

## `owl:imports` and the base

The same problem the published definition has, with the same answer: a
component's `owl:imports` is relative (`./node_modules/@rdfc/…`) because it
resolves against where the runner mounts the pipeline, and a store resolves a
relative IRI the moment it parses the document — against the endpoint's own URL,
which names nothing. So the description is resolved before it is sent, against
`file:///workspace/pipeline/` (`CATALOG_IMPORT_BASE`, defaulting to
`PIPELINE_IMPORT_BASE`). It has to be the *same* base as the definition's, or
the catalog and a definition would name two different files.

## Configuration

```
CATALOG_GRAPH             base IRI of the per-component named graph
CATALOG_GSP_ENDPOINT      Graph Store Protocol endpoint  (→ PIPELINE_GSP_ENDPOINT)
CATALOG_SPARQL_ENDPOINT   query endpoint for the ownership check  (→ SPARQL_ENDPOINT)
CATALOG_STORE_USER        Basic-auth credentials for the write  (→ PIPELINE_STORE_USER)
CATALOG_STORE_PASSWORD                                          (→ PIPELINE_STORE_PASSWORD)
CATALOG_IRI               the tcs:Catalog components are registered in
                          (https://elody.eu/catalog#ElodyCatalog)
CATALOG_PUBLISH_PAGES     pages of 20 components one sweep covers (5)
CATALOG_IMPORT_BASE       base the relative owl:imports resolve against
DEFINITION_INCLUDE_CATALOG  whether a downloaded definition carries the fragment (true)
```

Unset `CATALOG_GRAPH` (or every endpoint) and nothing is published — an
environment without a store simply does not have this behaviour.

## Still to agree

* **The graph name**, with Koen: `http://mu.semte.ch/graphs/catalog/<encoded
  IRI>` here, aligned with the pipeline-definition and errors graphs. Whether
  `app-dcat-catalog` reads one graph, a prefix, or federates is his call; it is
  one environment variable on our side either way. Its multi-source discovery
  service should need nothing Elody-specific beyond the endpoint and that base.
* **Precedence**, with Thomas: as implemented, the toolchain catalog wins for
  anything it describes and Elody fills the gaps. The opposite rule (Elody's
  live GitHub reading is fresher than a checked-in catalog file) is defensible
  too, and is one boolean — but it should be a decision, not a default.
* **Write authorisation** on redpencil's store, which is the same open question
  the pipeline definitions have.

## Tests

`tests/test_component_catalog.py` — the graph naming, upsert idempotency, what
the description contains (identity, the implementation join key, the shapes in
their roles, the runner), the ownership rule both ways including the withdrawal,
the configuration defaults, the discovery sweep's paging and dedup, that a store
which is down or refuses does not break a save, and that the fragment and the
catalog graph are one description. The store is faked at the `requests`
boundary, so the suite needs no triple store.

`tests/test_pipeline_definition_serializer.py` adds the gated one that matters
for the flip: a definition **without** a fragment, read alongside the catalog
graphs Elody publishes, introduces no application-profile violation the shipped
toolchain catalog does not already have. It runs against a real
toolchain-specification checkout (`TOOLCHAIN_SPECIFICATION_PATH`) and is skipped
without one.

The checkout is cloned by `task clone-repos` (it is in the client's
`repositories.txt`) and bind-mounted read-only at `/opt/toolchain-specification`,
which is where `TOOLCHAIN_SPECIFICATION_PATH` points in `.env.dist`. The gate
also needs the generator's own dependencies (`rdfine`, `compilers`) importable
in the collection-api container; they are not in the image, so they have to be
installed there before the class stops skipping. Note that upstream moves: the
2026-08-24 check found all eight of these tests failing against HEAD, one cause
being the cataloguing shape this page's *Catalog membership* section answers,
the other an `rdfine` SPARQL-compaction bug that is not ours
([toolchain-open-questions.md](toolchain-open-questions.md) §6).
