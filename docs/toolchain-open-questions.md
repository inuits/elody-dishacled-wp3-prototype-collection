# Open questions for the toolchain team

Coordination points between Elody's pipeline export and the toolchain
specification (Thomas) / the alert feed (Aad, redpencil; Arthur, IDLab).
Everything here is a decision we cannot take alone, with what we did in the
meantime and why.

Artefacts to put alongside these questions:

* a sample `GET /pipelines/<id>/definition.ttl` export,
* the project the generator compiles from it (`docker compose build` passes),
* `docs/toolchain-export.md` for the modelling decisions,
* the running alert endpoint and its sample data, for §5
  (`docs/alert-fixture.md`), and how Elody reads them
  (`docs/alert-ingestion.md`).

## 1. Shape-role vocabulary — resolved, no answer needed

We coined `tcs:inputShape` / `tcs:outputShape` alongside the catalog's existing
`tcs:configShape`, flagged as needing confirmation. `test suite/README.md` and
`docs/shacl-validation-plan.md` specify exactly those names, so this matched
independently. Recorded here only so nobody re-opens it.

## 2. `tcs:passthroughShape` and roles on `tcs:Channel`

The test suite adds a fourth role asserting that data passes through a
component unchanged, and attaches roles to `tcs:Channel` as well as to
components. Elody's contract model reads three roles and only on components,
so a passthrough component currently round-trips as "no shape".

**Question:** is `tcs:passthroughShape` settled enough to implement against, or
still moving? And are channel-level roles expected to be authored, or derived
(the plan describes deriving them from the passthrough component's
`readsFrom` / `writesTo`)?

## 3. Two shape matchers

§3 of `shacl-validation-plan.md` describes an external shape-matching algorithm
for deciding whether an upstream `outputShape` satisfies a downstream
`inputShape`. Elody already does this: it synthesises an instance under the
producer's output shape and SHACL-validates it against the consumer's input
shape, reporting a located, human-readable violation per connection.

Sample-based validation and structural subsumption do not agree in general, so
two matchers with different semantics would give two different answers about
the same pipeline.

**Question:** should Elody's matcher converge on the toolchain's once it
exists, call it, or stay deliberately separate as a fast editor-time check with
the toolchain's as the authority at build time? Worth settling before either
side hardens.

## 4. Should Elody ship a catalog fragment at all? — answered by doing it

A pipeline definition only names its components; their identity, dependencies
and shapes live in the catalog. Elody knows components the toolchain catalog
does not — anything discovered on GitHub, plus the interim contract catalog —
so the export carried a fragment describing them, and `?catalog=false` turned it
off.

**Taken the other way, as the architecture implies:** Elody now publishes the
components it knows about into a shared catalog graph, one named graph per
component, in the discovery-spec vocabulary
([component-catalog.md](component-catalog.md)). A discovery service reading the
store sees components without opening any pipeline, and a definition can
reference a component by IRI alone.

Three things are ours and settled: the fragment and the published description
are built by one serializer (so they cannot say different things — asserted by
isomorphism, not assumed); publishing is an idempotent whole-graph `PUT`; and
Elody never writes outside its own graphs, so it cannot overwrite the toolchain
catalog even by accident.

**What we still need from you:**

* **Koen — the graph name.** `http://mu.semte.ch/graphs/catalog/<url-encoded
  component IRI>`, aligned with the pipeline-definition and errors graphs.
  Confirm it, or say what `app-dcat-catalog` wants: one graph, a prefix, or
  federation. It is one environment variable on our side.
* **Thomas — precedence.** As implemented the toolchain catalog wins for
  anything it describes and Elody fills the gaps; a component the catalog
  catches up on is *withdrawn* from Elody's graph on the next publish, so the
  duplicate does not outlive the reason for it. The opposite rule — Elody's
  live GitHub reading is fresher than a checked-in file — is defensible too.
  We would rather it were a decision than a default.

* **Thomas / Koen — may Elody edit catalog members it does not own?** The
  architecture diagram (`docs/drawio_image.png`) has the frontend UI service —
  us — *"writes: updates on catalog members (edited by the user)"*, against one
  triple store holding one dishacled catalog. As implemented Elody writes only
  inside its own graphs and *skips* any component the toolchain catalog already
  describes, so a user editing such a component in Elody's UI has nowhere for
  that edit to go. Those two pictures cannot both be right. Either the UI edits
  only Elody-contributed members (what we built), or the toolchain catalog is
  editable through us and the structural guarantee has to be replaced by
  something weaker — provenance per triple, say. This is the precedence question
  above with a user attached to it, and it is the one we would most like
  answered.

**Also settled since:** an Elody component description now registers the
component in a `tcs:Catalog` of its own
(`<https://elody.eu/catalog#ElodyCatalog> dcat:resource <component>`, overridable
with `CATALOG_IRI`). Upstream's `tcs:SpecializedComponentIsCatalogedShape`
requires it of anything a step specialises, so without it every Elody-only step
violated the application profile — see §6, this is one of the two reasons the
gated generator tests broke against HEAD. If the demonstrator wants one shared
catalog subject rather than one per contributor, that is one variable.

**Not done, deliberately:** the definition export still ships the fragment by
default. Elody reads its own pipelines back out of the store, and the reverse
mapping needs the fragment's `dct:identifier` and config shape to do it, so
dropping it makes a pipeline publishable but unreadable. Making the read side
resolve components from the catalog graph is the remaining step, and it should
be written once the graph convention above is settled rather than twice.
`DEFINITION_INCLUDE_CATALOG=false` already flips it for a consumer that reads
the catalog graph.

## 4b. Named graphs for published pipeline definitions

Elody now publishes a saved pipeline's `tcs:PipelineDefinition` into the
central store rather than only serving it as a download
(`docs/toolchain-publication.md`). Two things need agreeing before the dry run.

**The graph naming.** We write one named graph per pipeline,
`http://mu.semte.ch/graphs/pipeline-definitions/<id>`, aligned with the errors
graph. One graph per pipeline is what makes republishing an atomic replace and
deletion exact: a definition hangs its config, packages and shapes off blank
nodes, so scoping a `DELETE` to one pipeline inside a shared graph has no
reliable spelling. Consumers therefore query `GRAPH ?g { ?p a
tcs:PipelineDefinition }`. Both the endpoint and the base IRI are environment
variables, so a different convention costs us a restart.

**Write authorisation.** Locally we write over the Graph Store Protocol with
Basic auth (Fuseki restricts `/*/data` out of the box). What does redpencil's
store expect — Basic, a token, a service account per writer?

**Question for Koen / Thomas:** confirm or correct the naming, and say what a
writer has to present.

**New — `dct:identifier` in the catalog fragment.** Pipelines are now *stored*
in the graph rather than mirrored into it (`docs/pipeline-storage.md`), so Elody
has to be able to read a definition back. A step names its component by IRI,
which the toolchain wants, but Elody addresses the same component as a document
(`rdf-connect--shacl-processor-ts--Validate`) and that is the key it stores the step
under. So the export emits `dct:identifier` on the pipeline, on each component
in the fragment and on each dataset. The alternative — resolving the IRI by
listing components from GitHub on every read — is slower and loses a step as
soon as a repository moves.

It is one extra non-`tcs:` triple per subject in a shared document, which is why
it is here: is `dct:identifier` acceptable, or does the toolchain already have a
term for "the id the composing tool knows this component by"?

## 5. The `oslc:Error` alert contract

The alert feed Elody ingests and visualises is the output of the pipeline it
exports: the demonstrator ends `ThresholdMonitor → … → SparqlIngest`, with
`rdfc:typeFilter oslc:Error` and `graphStoreUrl "http://identifier/sparql"`.

### Settled on our side

Elody now ships a local SPARQL endpoint seeded with sample `oslc:Error` alerts
(`docs/alert-fixture.md`), so this is no longer a proposal — there are
artefacts to react to:

* `ErrorShape` is in the contract catalog **verbatim** from the processor's own
  `processor.ttl` (`rdf-connect/threshhold-monitor-processor`, branch `master`),
  under the IRI it publishes it at, `http://lblod.data.gift/shapes/ErrorShape`.
  A gated test asserts graph isomorphism against the published file, so drift
  is detectable rather than assumed away.
* It is declared **once**, as a shape on the components rather than as a second
  copy in the fixture: `outputShape` of the threshold monitor, `inputShape` of
  sparql-ingest, `outputShape` of the alert store. The chain validation, the
  exported pipeline definition and this hand-off all read the same triples.
* Sample alerts are pyshacl-validated against that shape, with a negative
  control so the check cannot pass vacuously.

### Still open — for Aad / Arthur

**Is `http://identifier/sparql` the endpoint to standardise on?** Locally we
serve `/alerts/sparql` from a Fuseki container and read the address from
configuration (`ALERT_SPARQL_ENDPOINT`, `ALERT_GRAPH`), so Elody is not
hardcoded to either. We also assumed the named graph
`http://mu.semte.ch/graphs/errors`, which is what the processor's README writes
to — confirm or correct.

Elody now ingests the alerts through that configuration (`docs/alert-ingestion.md`):
they are queried live and served as entities, with nothing stored on our side.
Switching to whatever endpoint and graph you settle on is therefore two
environment variables and a restart, so the answer costs us nothing either way
— but we cannot pick it for you.

**Is the component catalog the right home for the shape?** It works and it
keeps one copy, but it means a shape published by lblod is restated in a
DiSHACLed catalog. The alternative is that we reference it and fetch it.

### New — a defect in the alert output

**The processor writes each alert on a blank node.** `src/index.ts` does
`const id = blankNode()`. Three things contradict that:

* lblod's own consumer, `loket-error-alert-service`, can only retrieve an alert
  by URI (`VALUES ?uri { … }`) and triggers on a delta whose subject is a URI;
* the processor's own README example inserts `<http://example.org/errors/1>`;
* it already generates a `mu:uuid` it could mint a stable IRI from.

A blank node cannot be linked to, cited, or deep-linked from a UI, so Elody
cannot visualise one as an addressable thing. Our fixture mints
`<…/alerts/{uuid}>` and we would like the processor to do the same.

Note `ErrorShape` constrains properties, not node kind, so it accepts both
forms — the shape will not settle this on its own.

### New — one shape, two IRIs

The demonstrator's own catalog declares the alert shape as `loket:ErrorShape`
(`http://lblod.data.gift/shapes/loket-error-alert-service/ErrorShape`, in
`catalogs/components/sw.ttl` on the `proposal` branch); Elody declares it under
the IRI the processor publishes and lblod's consumer queries,
`http://lblod.data.gift/shapes/ErrorShape`.

They describe the same alert, but a connection is only valid when both ends
name the shape *identically* — identity of the IRI is what makes an
output/input pair comparable at all. So as things stand, a chain that crosses
the boundary (our threshold monitor into their error-alert service, or their
monitor into Elody's alert visualisation) validates only because the demo
catalog carries both IRIs on the semantic.works entries as a local addition,
and the hand-off in `docs/examples/demonstrator-elody.ttl` writes Elody's input
shape with *their* IRI.

**For Thomas / Koen:** which IRI is the one, and does the other become an
`owl:sameAs` / a re-declaration? Our preference is the published one
(`.../shapes/ErrorShape`): it is what the processor emitting the alerts
declares, so it is the only one a consumer can discover without reading a
DiSHACLed catalog first. Either answer is a find-and-replace on our side.
See `docs/alert-component.md`.

## 5b. Three files on the `proposal` branch do not parse

Found while merging Elody's alert-visualisation component into the demonstrator
catalog (`docs/alert-component.md`), against
`DiSHACLed/demonstrator@proposal` (2768933, 2026-08-24). Each of these is a
one-line fix, and each of them makes a whole catalog file unreadable to any RDF
parser, so a validator run over the composed pipeline cannot include them:

* `catalogs/components/custom.ttl:106` — `loket:ErrorShape` with no `@prefix
  loket:`. It is the output shape of the threshold monitor, i.e. the one triple
  the alert chain is validated on.
* `catalogs/components/rdfc.ttl:22` — `dct:requires ... ;`, a literal `...`
  placeholder in object position (also `owl:imports ...`).
* `catalogs/data.ttl:31` — `dcat:mediaType iana:text/turtle`, where the local
  name contains a `/`. It needs `<...>` or a longer prefix.

Elody's local copy of these catalogs already carries the first two fixes (the
prefix and the media-type IRI, in the "local demo copy" block of
`shacl/catalog/contracts.ttl`) and skips `rdfc.ttl` altogether, which is why
the suite here is green on them — they were never reported back. Worth a small
PR on the branch, and it is a prerequisite for the "one pipeline definition
that validates clean end to end" the demo wants.

## 5c. No released orchestrator can start a processor with a path parameter

Found by running the published threshold monitor against the local stack
(`docs/alert-component.md`). `@rdfc/threshold-monitor-processor-ts` types its
`tm:path` parameter `sh:class rdfl:PathLens`, which is the name `rdf-lens`
actually defines (1.3.x: `cache[RDFL.PathLens] = ShaclPath`, and no `Path`).
`@rdfc/orchestrator-js` registers `rdfl:CBD`, `rdfl:Path`, `rdfl:TypedExtract`
and `rdfl:Context` — so a pipeline using that processor fails while the
orchestrator is building its arguments:

```
Failed at property { clazz: 'https://w3id.org/rdf-lens/ontology#PathLens', found: [ … rdfl:Path … ] }
TypeError: Cannot read properties of undefined (reading 'addToDocument')
```

Checked against every published orchestrator, 1.0.0 through 2.2.2: none of them
registers `PathLens`. The fix is one line
(`dtos[RDFL.PathLens] = new CBDDefinition(RDFL.PathLens)`), and it is not
specific to the monitor — it affects any processor with a SHACL-path parameter.

Two smaller findings from the same run, worth passing on with it:

* a *sequence* path (`( sosa:hasResult qudt:numericValue )`) is rejected as
  "Expected 1 or less objects for property tm:path", so only single-predicate
  paths work in practice — a chain over the demonstrator's own sample needs a
  mapping step to flatten the value onto the observation first;
* `sdsify`'s `metadataOutput` needs a reader, or the pipeline hangs silently
  before the first member reaches the monitor.

**What the blank-node alert actually costs** (§5, "a defect in the alert
output") is now measured rather than predicted: with the monitor writing alerts
on blank nodes, Elody's alert listing reported `count: 21, results: 4` — the
17 real alerts were in the store, counted, and unlistable. That was a defect on
our side too (the engine paged by subject IRI; it now pages by the identifying
property, so blank-node alerts list and open). What remains is that a blank
node cannot be linked to or cited from outside Elody, which is the reason the
processor should mint `<…/alerts/{uuid}>`.

## 5d. Compiling an Elody definition: what the generator says

The definition Elody exports now goes through the pipeline generator. It
compiles — `docker-compose.yml`, `Dockerfile`, `package.json`,
`pyproject.toml`, `pipeline.ttl`, `validation-report.ttl`, with the published
`@rdfc/threshold-monitor-processor-ts` among the installed packages — and the
validation report went from **8 violations to 3** as five defects on our side
were fixed:

* **Nested config nodes carried no class.** `rdfc:ingestConfig [ … ]` with no
  `a rdfc:IngestConfig`, and the same for `sdsify`'s `metadataConfig` and
  `HttpFetch`'s `options`. Reported as "Value does not have class
  rdfc:IngestConfig".
* **`dct:requires` pointed at a dataset.** Our alert visualisation required
  `demo:AlertStore`, a `dcat:Dataset`; the profile allows only a
  `tcs:PipelineComponent` or an `spdx:Package`.
* **IRI-valued parameters.** `xsd:iri` is not a datatype — it is how the
  RDF-Connect processors say "an IRI", and `catalog-rdfc.ttl` restates it as
  `sh:nodeKind sh:IRI ; tcs:upstreamDatatype xsd:iri`. Elody wrote such values
  as typed literals *and* restated the raw `sh:datatype xsd:iri` in its
  fragment, so the report contradicted itself depending on which side you
  looked at. Both sides now follow the harvester's convention. (Worth knowing:
  the runner accepts either spelling — we ran the same pipeline both ways and
  the monitor started and alerts arrived each time. It is the profile that
  settles it.)
* **Unbound namespaces.** `rdfine` compacts an IRI to a CURIE and interpolates
  the result into SPARQL, so an IRI in a namespace the document does not bind
  arrives bare and the query does not parse. That is arguably a defect there —
  an unbracketed IRI in a query is never right — but the document is ours to
  fix, and every namespace we emit is now bound. The same mechanism bites on
  IRIs that *do* compact but not to a legal CURIE: our step IRIs became
  `pipeline:<id>/step/<slug>`, a CURIE with slashes in it, so the step and
  channel namespaces get prefixes of their own.
* **A component that says nothing about being deployed.** The profile requires
  every `tcs:PipelineComponent` to reach a `tcs:DockerComposeConfig` along
  `dct:requires*`. Fair: a dashboard nobody deploys renders nothing. Elody's
  entry now carries one, the way the demonstrator's semantic.works services
  do, and the generated `docker-compose.yml` gains an `elody-dashboard`
  service alongside `rdfc`.

### What is left, and why it is a question rather than a fix

```
Cross-container channel has no EntryBoundaryComponent-typed reader step —
no bridge component available in the catalog to insert.        (×2)
Cross-container channel has no ExitBoundaryComponent-typed writer step —
no bridge component available in the catalog to insert.        (×1)
```

Declaring Elody deployable puts it in its own container, so the channel from
the threshold monitor to the dashboard now crosses a container boundary, and
the profile wants a bridge inserted. But **there is no channel**: Elody reads
the alerts out of the central store, which is the "implicit information flow"
of `extensions.md` — the same relation the demonstrator's own scenario-a draws
as a `tcs:Connection` between the monitor and `sw:loket-error-alert-service`,
with no channel and no bridge.

So the two spellings available to us each trip a different rule: with no
`tcs:DockerComposeConfig` the component is "not deployable"; with one, the edge
becomes a cross-container channel needing a bridge. Elody emits the generator's
channel vocabulary (`tcs:readsFrom`/`tcs:writesTo`) because that is what the
generator reads — which is §8's question in concrete form.

**Questions for Thomas / Koen:**

* How should a store-mediated consumer be written in the *generator's* input?
  A `tcs:Connection` the compiler understands as implicit, an
  `EntryBoundaryComponent` the store itself plays, or something else?
* Is a component that is deployed but never instantiated by a runner expected
  to appear in the generated `pipeline.ttl`? Ours does not (correctly, we
  think: nothing starts it), so the generated project deploys the dashboard
  and runs the RDF-Connect stages, and the two meet at the store.

## 6. Versioning

The generator is consumed as a git checkout — no release, no tag, no published
package, and the repository is a single squashed commit. "Elody's export
matches what the generator expects" is therefore pinned to a commit, and a
change to the application-profile shapes would break it silently on our side.

**Question:** is a tagged release plausible? Failing that we will re-run the
gated tests against a fresh clone periodically, which detects drift but only
after the fact.

## 7. Two defects found by running the generator's output

Reported here because they block anyone deploying a generated project, and
because the generator's own checked-in build
(`pipeline generator/out/dishacled-full/`) has both.

**The runner's own `owl:imports` is never emitted.** `catalog-rdfc.ttl`
declares `rdfc:NodeRunner ... owl:imports <./node_modules/@rdfc/js-runner/index.ttl>`,
but `RdfcConfigCompiler.describe_pipeline` collects imports only from
processors (`?processor dct:requires ?runner ; owl:imports ?import`), never
from the runner itself. Without it `rdfc:NodeRunner` is undefined, the
orchestrator logs `Starting  processors` with an empty count, and exits 0
having run nothing. The hand-built `demonstrator/RDFC/pipeline.ttl` imports it
first in its list.

**Emitted imports are absolute; the orchestrator only follows relative ones.**
The generator serializes `@base <file:///workspace/pipeline/>` plus
`owl:imports <file:///workspace/pipeline/node_modules/...>`. The orchestrator
does not resolve those -- the pipeline stays at 125 quads and no processor
definitions are found. Rewriting the same imports as `./node_modules/...`
(what the hand-built file uses) makes it expand all of them: 304 quads, the
runner is built, and the processor starts. Both forms denote the same IRI under
that base, so this is the importer doing string handling rather than IRI
resolution -- but the practical consequence is that generated projects do not
run as emitted.

**Question:** are these known? We can supply the exact pipeline.ttl before and
after, and the orchestrator logs for both.

## 8. Which pipeline form is the one an end user authors?

Koen's proposal of 2026-08-24 (`DiSHACLed/demonstrator`,
`pipelines/final-valid/scenario-a.ttl` + `extensions.md`) makes the *logical*
pipeline definition the end-user-authored form and demotes thcarsten's bridged
`pipeline_definition.ttl` — the file this export was written against — to a
compiler pre-processing stage. Elody is exactly the tool that authors the
end-user form, so if that lands, this is the form we publish
(`docs/toolchain-publication.md`) and read back
(`docs/pipeline-storage.md`), not the bridged one.

Three deltas against what `definition.ttl` emits today:

* **Connections replace channels.** A link becomes
  `[ a tcs:Connection ; tcs:from <...> ; tcs:to <...> ]`; there are no
  `tcs:Channel` subjects and no `tcs:readsFrom` / `tcs:writesTo` annotations.
  This is *closer* to how Elody already models the pipeline than the current
  export is: connections are authored consumer-side as
  `connections.<port>.from`, and the channel is a derived name
  (`channel_name_between`) the user may override. The current export
  materialises that channel because the bridged form needs one; the logical
  form would let us stop.
* **Configs are wrapped.** `p-plan:hasInputVar [ a tcs:WrappedPipelineConfig ;
  tcs:embedded [ ... ] ]` with a `wrappedConfigShape` / `unwrapConfig`
  mechanism, against today's `tcs:PipelineConfig`. Not a rename: whatever
  `unwrapConfig` is allowed to do is what decides whether Elody can keep
  emitting a component's config verbatim under its own config shape.
* **The catalog is split per framework** (`catalogs/components/*.ttl`).
  Elody publishes one named graph per component under `CATALOG_GRAPH`
  (§4), which is a store layout, not a file layout — but if "per framework"
  is a *grouping* consumers query by, we need the framework on the component
  description, and we do not emit one today.

**Questions for Koen / Thomas:**

* Is the logical form settled enough to retarget the serializer, or is it
  still a proposal? It reads as one — `tcs:do` looks like a typo for
  `tcs:to`, and the SHACL contracts are marked TODO.
* Do the toolchain services (generator, validator, the CLI demo script) read
  the logical form from the store, or the bridged form? If they read the
  bridged form, Elody publishing the logical one means somebody has to run the
  pre-processing stage between us and them, and it should be said where.
* Is the bridged form still a supported *input*, i.e. does retargeting mean
  emitting the logical form **instead of** or **as well as** today's?

**What we are doing meanwhile:** nothing. Retargeting touches the export, the
publisher, the store reader and their tests; doing it twice against a moving
proposal costs more than waiting for one answer. The mapping itself is small —
the entity model already carries connections, so it is the emitting side that
changes, not what Elody stores.
