# Exporting a pipeline to the toolchain pipeline generator

Elody has two turtle exports of the same pipeline. They are different
artefacts, not two spellings of one:

| Route | Produces | Consumed by |
| --- | --- | --- |
| `GET /pipelines/<id>/export.ttl` | a runnable RDF-Connect `pipeline.ttl` | the RDF-Connect runner, directly |
| `GET /pipelines/<id>/definition.ttl` | a `tcs:PipelineDefinition` | the toolchain pipeline generator |

The pipeline generator ([thcarsten/toolchain-specification][spec], *pipeline
generator*) reads a definition plus a component catalog and compiles a whole
deployable project: `docker-compose.yml`, `rdfc/Dockerfile`,
`rdfc/package.json`, `rdfc/pyproject.toml` and the RDF-Connect `pipeline.ttl`.
Exporting the definition is what makes an Elody-composed pipeline *deployable*
through the toolchain rather than only runnable by hand.

Both routes share the chain validation: an incompatible pipeline answers
**409** with the violation list, and `?force=true` exports it anyway with the
violations prepended as turtle comments.

[spec]: https://github.com/thcarsten/toolchain-specification

## Exporting from the UI

The pipeline detail page has an **Export pipeline definition** entry in its
overflow (⋮) menu, next to Edit metadata and delete. It downloads exactly what
`GET /pipelines/<id>/definition.ttl` returns, named by the filename the API
sets (`pipeline-definition-<id>.ttl`).

It cannot call collection-api directly: the access token lives in the graphql
service's session, not in the page. So the click goes to
`GET /api/pipelines/<id>/definition.ttl` on the graphql service, which adds the
token and passes the upstream status, content type and filename straight
through — including the **409** for an invalid chain, whose JSON explanation
stays readable in the response.

| Piece | Where |
| --- | --- |
| The proxy | graphql-service `src/endpoints/pipelineExport.ts` |
| URL building and its guards | `src/endpoints/pipelineExportUrl.ts` (+ `.test.ts`) |
| The menu entry | `src/dishacledRoutes.ts`, `entityPageConfig.pipeline.actions` |

Only `export.ttl` and `definition.ttl` are proxied, and only `force` and
`catalog` are forwarded: both the id and the export name are interpolated into
an upstream URL, so neither is taken from the request unchecked.

The menu entry uses the framework's `downloadZip` action type, which is not
zip-specific — it means "call an endpoint and save the response as a file", and
takes the filename from `Content-Disposition`.

## What the definition contains

Per step:

```turtle
<...>/step/threshold-monitor-cm a tcs:InstancePipelineComponent ;
    rdfs:label "Threshold monitor (cm)" ;
    p-plan:isStepOfPlan pipeline:pipeline-1 ;
    prov:specializationOf demo:ThresholdMonitorCm ;
    p-plan:hasInputVar [ a tcs:PipelineConfig ;
        tcs:embedded [ demo:threshold 300.0 ; rdfc:input <...channel> ] ] ;
    tcs:readsFrom <...channel> .
```

`tcs:readsFrom` / `tcs:writesTo` is what the generator reasons over; the
matching `rdfc:input` / `rdfc:output` inside `tcs:embedded` is what the
framework itself reads at run time. Both are written from the one connection
the user drew, so they cannot drift.

Plus a **catalog fragment** for the components the pipeline uses — their
identity (`tcs:PipelineComponent, dcat:Resource`), their dependencies
(`dct:requires` a runner and any `spdx:Package`), the `owl:imports` that lets
the runner locate the processor, and their config / input / output shapes in
the discovery vocabulary. Elody knows components the toolchain catalog does
not, so the definition would otherwise name things the generator cannot
resolve.

`?catalog=false` drops the fragment. Use it when every component is already
declared upstream and re-declaring them would merge two descriptions of the
same component.

## Where deployment coordinates come from

A definition that names components nothing can install is accepted by the
generator but produces a project that will not build. Two sources, in
precedence order:

1. **The contract catalog**, when it declares them (`owl:imports`, `dct:requires`
   an `spdx:Package`). Curated, and wins.
2. **The repository's own manifest**, for anything discovered on GitHub:
   `package.json` gives an npm name and version, `pyproject.toml` a pip one.
   The `owl:imports` path is synthesised as
   `./node_modules/<package>/<repo-relative ttl path>` -- verified against the
   published `@rdfc` tarballs, which carry their TTL at the same path the
   repository does. Only TTL files that actually declare a processor are
   imported, so a repository's test fixtures stay out.

No import is synthesised for a Python package: its install path depends on the
interpreter version baked into the image, which is not knowable from here.

### Things that deliberately do not round-trip

* **Framework infrastructure is not emitted.** `rdfc:Orchestrator` and its
  `tcs:DockerComposeConfig` / `tcs:DockerImageConfig` live in the toolchain
  catalog. The export is meant to be *merged* with that catalog, not to
  replace it.
* **A `dcat:Dataset` is not a step.** It is not deployable, so it is declared
  as `dcterms:source` of the plan and keeps the `tcs:writesTo` annotation
  naming the channel it feeds — but nothing writes to that channel in the
  generated project.
* **`owl:imports` stays relative.** The generator parses with base
  `file:///workspace/pipeline/` (rdfine `GraphReader`), which is where the
  runner mounts the pipeline. Resolving the import in Elody would nail it to
  the wrong root.
* **The demo components carry placeholder package coordinates.** See the
  header of `api/apps/dishacled/shacl/catalog/contracts.ttl`. The manifest
  entries are shaped and routed correctly, but `@dishacled/demo-processors`
  is not published, so a `docker compose up` of a pipeline that includes one
  will fail at install time. This is specific to the four hand-authored demo
  components; a pipeline built from processors discovered on GitHub gets real
  coordinates and builds.

## Verifying against the real generator

`tests/test_pipeline_definition_serializer.py` has a
`TestToolchainPipelineGenerator` class that runs the toolchain's own
application-profile shapes, inference rules and compilers over the export. It
skips unless a checkout is available, so the suite has no external dependency:

```bash
git clone https://github.com/thcarsten/toolchain-specification.git /tmp/tcs
pip install rdflib pyld pandas PyYAML boltons glom validators
TOOLCHAIN_SPECIFICATION_PATH=/tmp/tcs PYTHONPATH=api python -m pytest \
    tests/test_pipeline_definition_serializer.py -q
```

The SHACL check is stated as a *delta*: the shipped catalog does not fully
conform on its own (`rdfc:HttpFetch` and `rdfc:LogProcessorPy` are listed in
`:DishacledCatalog` but never defined), so the bar is that the export
introduces no violation beyond that baseline.

### Redeclaring a component the toolchain already knows

The catalog fragment restates components that may already be in the toolchain
catalog. That merge is safe for imports: both sides express `owl:imports`
relative to the runner's mount point, so loaded the way the generator loads it
they resolve to the same IRI and collapse to one triple. Watch the base when
testing this by hand -- parsing the catalog files without
`publicID="file:///workspace/pipeline/"` resolves their imports against the
file path instead, and the same import then looks like two.

`?catalog=false` remains available for the case where a component's two
descriptions genuinely disagree.

## Verified end to end

A pipeline built from a processor discovered on GitHub
(`rdf-connect/sparql-ingest-processor-ts`) exports, compiles and **builds**:

```
resolved coordinates  @rdfc/sparql-ingest-processor-ts ^2.1.7   (from package.json)
generated project     docker-compose.yml, rdfc/{Dockerfile,pipeline.ttl,
                      package.json,pyproject.toml}
docker compose build  exit 0 -- npm install resolved, image rdf-connect:latest
docker compose up     orchestrator loads the pipeline, starts the NodeRunner,
                      instantiates the step, applies our config values
```

The coordinates Elody read off the repository match what the toolchain catalog
hand-authored for the same component, independently.

Running it needs two edits to the generator's output first -- both defects in
`RdfcConfigCompiler`, not in the definition Elody exports (see
`docs/toolchain-open-questions.md` §7):

1. add `owl:imports <./node_modules/@rdfc/js-runner/index.ttl>`, without which
   `rdfc:NodeRunner` is undefined and zero processors start;
2. rewrite the emitted `owl:imports` from absolute `file:///workspace/pipeline/...`
   back to relative `./node_modules/...`, which is the only form the
   orchestrator's importer follows.

With both applied the orchestrator reports `Starting 1 processors`, adds our
step to the runner, and initiates it with the `graphStoreUrl` and
`targetNamedGraph` typed into the Elody form. The remaining
`Reader ... has no linked Writer` is correct for a one-step pipeline with
nothing feeding it.

## Not verified

Reproducing the manual target build from `DiSHACLed/demonstrator` (branch
`development`) in full. That build spans three frameworks: Elody models
RDF-Connect only, LDIO would additionally need `ldio:type` and the
serial-pipeline constraints, and the semantic.works section is hand-configured
upstream too (the generator emits pre-baked demonstrator files for it -- see
§5.1 of the generator README). So "reproduces the manual target build" is
reachable for the RDF-Connect segment and not for the rest; the honest next
step is to model that segment and diff the generated `rdfc/pipeline.ttl`
against `demonstrator/RDFC/pipeline.ttl`.
