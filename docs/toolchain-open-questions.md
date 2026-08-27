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
