# Open questions for the toolchain team

Coordination points between Elody's pipeline export and the toolchain
specification (Thomas) / the alert feed (Aad, redpencil; Arthur, IDLab).
Everything here is a decision we cannot take alone, with what we did in the
meantime and why.

Artefacts to put alongside these questions:

* a sample `GET /pipelines/<id>/definition.ttl` export,
* the project the generator compiles from it (`docker compose build` passes),
* `docs/toolchain-export.md` for the modelling decisions.

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

## 4. Should Elody ship a catalog fragment at all?

A pipeline definition only names its components; their identity, dependencies
and shapes live in the catalog. Elody knows components the toolchain catalog
does not — anything discovered on GitHub — so today the export carries a
fragment describing them, and `?catalog=false` turns it off.

The alternative is that Elody-discovered components get pushed into the central
catalog and the export names them only.

**Question:** which way is intended? The fragment works and merges cleanly, but
it means the same component can be described in two places.

## 5. The `oslc:Error` alert contract

The alert feed Elody will ingest and visualise is the output of the pipeline it
exports: the demonstrator ends `ThresholdMonitor → … → SparqlIngest`, with
`rdfc:typeFilter oslc:Error` and `graphStoreUrl "http://identifier/sparql"`.

Elody's next task builds a local SPARQL endpoint seeded with sample
`oslc:Error` alerts, doubling as the contract handed to redpencil and IDLab:
*"this is the shape and the endpoint Elody expects"*.

That contract is a SHACL shape, which is exactly what the contract catalog
holds and what the chain validation already checks against. Our proposal is to
declare it once as the output shape of the alert-producing component, so the
same definition serves the chain validation, the exported pipeline definition,
the toolchain's shape-matching suite, and the hand-off to redpencil — rather
than hand-authoring a second copy in the fixture.

**Question for Aad / Arthur:** does the shape belong in the component catalog
in that form, and is `http://identifier/sparql` the endpoint to standardise on?

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
