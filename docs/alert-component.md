# Elody's alert visualisation as a pipeline component

The dashboard is no longer something you open *next to* the pipeline. It is a
**component in the pipeline**, declared in the catalog with `ErrorShape` as its
input contract:

```turtle
elody:AlertVisualisation a tcs:PipelineComponent, dcat:DataService, dcat:Resource ;
    rdfs:label "Alert visualisation (Elody)" ;
    dcterms:requires demo:AlertStore ;
    dcat:qualifiedRelation
        [ a dcat:Relationship ; dcat:hadRole tcs:inputShape ;
          dcterms:relation lblodsh:ErrorShape ] ,
        [ a dcat:Relationship ; dcat:hadRole tcs:configShape ; dcterms:relation [ … ] ] .
```

Three things follow from that one entry, and none of them needed new runtime
code:

* **Discovery (Step 3) finds Elody.** A shape-guided search for "who can
  consume this alert data" is a query over the catalog, and until Elody was in
  it the answer could not include Elody.
  [`examples/discovery-error-shape.sparql`](examples/discovery-error-shape.sparql)
  is that query.
* **The pipeline definition (Step 4) ends in Elody**, as an ordinary
  `tcs:InstancePipelineComponent` that specialises the component.
* **Chain validation covers the last hop.** monitor → store → Elody is checked
  the same way every other link is, so the cm/mm story breaks *visibly all the
  way to the dashboard* instead of stopping at the store.

Rendering is [`alert-rendering.md`](alert-rendering.md); reading the alerts is
[`alert-ingestion.md`](alert-ingestion.md); the endpoint and its sample data are
[`alert-fixture.md`](alert-fixture.md). This page is only about the
*registration*.

## Why declarative is the right modelling, not a shortcut

The doc's comment thread (Niels/Ieben/redpencil) converged on "something the UI
can query" — a SPARQL store — rather than a push service aimed at Elody, and
that is also the option that survives alerts being cleared or kept as a
persistent log. Elody already consumes the store that way: the `sparql`
read-through engine queries it live and stores no copy.

So the component has **no processor to install**: no `spdx:Package`, no
`owl:imports`, no runner. This is exactly how the demonstrator's `proposal`
branch models the semantic.works services (`extensions.md`):

> input through reading of the central database only (at intervals/at deltas
> through the delta notifier) … So no direct flows; only implicit information
> flows. Validation just checks that connections agree upon the contracts.

A `tcs:Connection` into Elody is therefore a *logical* arrow: the monitor writes
alerts to the store, Elody reads them out of it, and what the validator has to
check is that both ends name the same contract. `ErrorShape` is a **fixed**
contract — it does not vary with how the dashboard is configured — so no
resolver function is involved either (the proposal branch's mechanism for
components whose contracts depend on their config).

The one fallback that would change this is transport: if the alerts arrive as an
**LDES feed** from redpencil instead of through a store, Elody needs a real
ingest step (an LDES client), and that is a component with code. Out of scope
until that decision is taken — it is on the progress-meeting list.

## The entry, in detail

`api/apps/dishacled/shacl/catalog/contracts.ttl`, next to the alert store it
reads.

| | |
| --- | --- |
| IRI | `https://elody.eu/components#AlertVisualisation` |
| Elody id | `local--alert-visualisation` |
| Types | `tcs:PipelineComponent`, `dcat:DataService`, `dcat:Resource` |
| Input shape | `<http://lblod.data.gift/shapes/ErrorShape>` |
| Output shape | **none** |
| Config | `sparqlEndpoint`, `namedGraph`, and the `alerts` reader port |
| Deployment | nothing — no package, no import, no runner |

**Its own namespace.** `elody:` rather than the demo namespace, for the same
reason the components in the demonstrator's catalogs sit in `sw:`, `ldio:` and
`tm:`: a component is named in the namespace of whoever provides it. It also
keeps the ownership rule of
[`component-catalog.md`](component-catalog.md) honest — the catalog Elody
publishes into is Elody's own, so what it contributes should not be named in
someone else's space.

**The producer it is checked against is real now.** `tm:ThresholdMonitorJs`
used to be an overlay in the catalog — shapes only, on the assumption that
GitHub discovery finds the repository and supplies its config shape and
coordinates. It does not: `rdf-connect/threshhold-monitor-processor` carries no
`rdfc-processor` topic, so discovery never returned it and the one component
that actually raises an alert could not be put in a pipeline at all. The entry
now describes it in full — the published package
(`@rdfc/threshold-monitor-processor-ts`), the `owl:imports` the runner follows,
and the config shape — exactly as `rdfc:RmlMapper` is described for a jvm
processor whose repository cannot supply coordinates either. Asking rdf-connect
for the topic would let it go back to being an overlay;
`tests/test_alert_producer.py` covers it either way.

**No output shape, on purpose.** A dashboard is a sink: it renders alerts and
emits no stream. It therefore gets no output port, the builder never offers it
as the producer for a further step, and a definition cannot accidentally chain
something behind it. The catalog invariant "every component has an output port"
was true only while every component was a processor in the middle of a chain;
it is now "every component that declares an output shape has an output port"
(`tests/test_connections.py`).

**Configuration is the two things Elody actually needs** to read the alerts —
the endpoint and the named graph — which are the same two the running service
takes as `ALERT_SPARQL_ENDPOINT` and `ALERT_GRAPH`. They are declared under
`tcs:configShape`, which is the role Elody's own reader understands. The
proposal branch's `tcs:wrappedConfigShape` / `tcs:unwrapConfig` pair is *not*
carried here: it is still a proposal (§8 of
[`toolchain-open-questions.md`](toolchain-open-questions.md)), and declaring a
second, unread spelling of the same config would be one more thing to keep in
step. The hand-off file below does carry it, because every component in the
demonstrator's own catalogs does.

**The `alerts` reader port** is what makes the input connectable in the
builder. Nothing writes to it at run time; the delivery is the store. This is
the same small fiction the semantic.works services live with, and it is what
lets one pipeline definition describe both halves.

## A step, but not a processor

Elody ships no processor, and the catalog now says so rather than implying
otherwise. A contract entry that declares no `rdfc:*ImplementationOf` and has
nothing to install is **not runnable** (`runnable` in `shacl/contracts.py`),
and three things follow:

* its synthesised processor file no longer claims `rdfc:jsImplementationOf
  rdfc:Processor` — there is no implementation to join a repository to;
* the published catalog description asks for **no runner**. It names what the
  catalog says the component needs instead (`dcterms:requires demo:AlertStore`,
  the store it reads through), the way the demonstrator's semantic.works
  entries require `sw:triple-store`;
* the runnable RDF-Connect export emits it as a stage that can be *wired* but
  is never handed to a runner to instantiate — precisely the treatment a
  `dcat:Dataset` already got, and for the same reason.

Without that, publishing this component would have told the generator "start
this with the Node runner", which is a step it cannot start; the pipeline
either fails to come up or comes up with a processor that does nothing. The
rule is deliberately about what the catalog *declares*, not about Elody: it
gives the same, more honest description to the LDIO and semantic.works entries
in the demo catalog, none of which are RDF-Connect processors either.

The definition export is unaffected in the part that matters — Elody is a
`tcs:InstancePipelineComponent` specialising the component, reading from the
channel the monitor writes to, and it is the chain's last step.

## What runs today, end to end

Composed in Elody, exported, run, and rendered — verified against the running
stack on 2026-09-07:

```
HttpFetch -> Sdsify -> ThresholdMonitorJs -> SPARQLIngest -> Fuseki -> /alerts
                                    (+ the alert visualisation as declared consumer)
```

The chain validation reports the two typed hops (monitor → SPARQLIngest,
monitor → alert visualisation) **valid** and the three untyped GitHub
processors as unverifiable warnings; the runnable export starts on `npx rdfc`;
the monitor raises one `oslc:Error` per measurement above the configured bound;
and `/alerts` lists them (`count: 21 | results: 21`, the 4 fixture alerts plus
17 real ones), each opening in the detail view.

Three things had to be fixed on our side to get there, and none of them was a
workaround:

* **The SPARQL engine paged by subject IRI** (`collection-api`,
  `api/storage/sparqlstore.py`). An alert from the monitor is written on a
  *blank node*; the page query returned it, the properties query then asked
  for it by IRI, and nothing matched — so the count said 21 and the listing
  returned 4, with no error anywhere. Paging and fetching now join on the
  identifying property, which is what the detail route always did.
* **IRI-valued config parameters were emitted as literals**
  (`serializers/pipeline_ttl_serializer.py`). `sh:datatype xsd:iri` is how the
  RDF-Connect catalogs say "this is an IRI" — the monitor's `creator` and
  `path`, `sdsify`'s `typeFilter` and `streamId`. A literal there is a string
  that looks like an IRI, and for a required parameter that is a processor
  that will not start.
* **The alert producer was unpickable**, as described above.

The definition also compiles through the toolchain **pipeline generator**: a
full project (`docker-compose.yml`, `pipeline.ttl`, `package.json`, …) with the
published threshold monitor among the installed packages and an
`elody-dashboard` service beside the `rdfc` one. Getting there fixed four more
defects in our export — untyped nested config nodes, a `dct:requires` pointing
at a dataset, IRI-valued parameters written inconsistently between the value
and the shape, and namespaces left unbound (which breaks the generator's
CURIE-into-SPARQL step) — and took the validation report from 8 violations to
3. The three that remain are one question rather than a defect: declaring the
dashboard deployable puts it in its own container, so the edge from the monitor
reads as a cross-container channel needing a bridge, when in truth there is no
channel at all — it is the store-mediated implicit flow. §5d of
[`toolchain-open-questions.md`](toolchain-open-questions.md) has the detail and
the question.

Two upstream blockers remain, and the run above needed a one-line local patch
for the first:

* **No released orchestrator can resolve a SHACL-path parameter.**
  `@rdfc/orchestrator-js` registers `rdfl:Path`; `rdf-lens` defines
  `rdfl:PathLens` and no `Path`. Every published version, 1.0.0 through 2.2.2,
  so any processor with a path-typed parameter dies before the first member
  (`Failed at property { clazz: '…#PathLens' }`). One line in the
  orchestrator fixes it; see §5c of
  [`toolchain-open-questions.md`](toolchain-open-questions.md).
* **Alerts are still blank nodes.** They now list and open, but a blank node
  cannot be linked to or deep-linked from anywhere outside Elody, so the ask
  in §5 stands — it is no longer a blocker, just a limitation.

## Hand-off to DiSHACLed/demonstrator

Two files, both in [`examples/`](examples), written in the demonstrator's own
prefixes and ready to drop onto the `proposal` branch:

| file | goes to |
| --- | --- |
| [`demonstrator-elody.ttl`](examples/demonstrator-elody.ttl) | `catalogs/components/elody.ttl` |
| [`demonstrator-scenario-elody.ttl`](examples/demonstrator-scenario-elody.ttl) | appended to `pipelines/final-valid/scenario-a.ttl` (and `scenario-b.ttl`, which continues the same tail) |

The step hangs off the **alert producer**, not off the email service:

```turtle
demo:ElodyAlertDashboard a tcs:InstancePipelineComponent ;
    prov:specializationOf elody:AlertVisualisation ;
    p-plan:isStepOfPlan demo:DishacledPipeline .

[ a tcs:Connection ; tcs:from demo:ThresholdMonitor ; tcs:to demo:ElodyAlertDashboard ] .
```

so the monitor fans out — store → email *and* store → dashboard — which is what
"one pipeline definition that runs end to end" means for Steps 5/6.

Three deliberate differences from Elody's own copy, all of them because the
demonstrator's files differ:

* **`tcs:wrappedConfigShape` + `tcs:unwrapConfig`**, as described above. The
  minimal thing a definer has to say is the named graph; the endpoint is the
  stack's own store and the reader port is wiring, so both are for the unwrap
  query to fill in.
* **Catalog membership** on `:DishacledCatalog`, the demonstrator's own catalog
  subject — a component that is a `dcat:resource` of no `tcs:Catalog` violates
  `tcs:SpecializedComponentIsCatalogedShape` on every step specialising it.
  Adding the triple in the fragment keeps it self-contained; the two documents
  union.
* **The `ErrorShape` IRI.** The demonstrator declares the alert shape as
  `loket:ErrorShape`
  (`http://lblod.data.gift/shapes/loket-error-alert-service/ErrorShape`), Elody
  as `http://lblod.data.gift/shapes/ErrorShape` — the IRI the processor
  publishes and lblod's own consumer uses. Two names for one shape, and a
  connection only validates if both ends name it identically, so the hand-off
  uses theirs. Unifying them is §5 of
  [`toolchain-open-questions.md`](toolchain-open-questions.md).

Elody's own catalog fragment is also *published* into the shared catalog graph
by the same machinery every other component uses
([`component-catalog.md`](component-catalog.md)), so a consumer reading the
store finds this component without the hand-off at all. Verified against the
running store: the component has a catalog graph of its own, and the discovery
query above returns it there alongside `sw:loket-error-alert-service` and
`rdfc:SPARQLIngest` — the two other components whose input contract is the
alert shape.

That took a fix of its own. The ownership rule asked whether a component was
described *outside Elody's catalog graphs*, and a published definition carries
the catalog fragment — so after the first pipeline save every component it
named looked like the toolchain's, publishing was skipped, and an existing
Elody description was **withdrawn**. Which components ended up in the catalog
came down to the order pipelines were saved in; the alert visualisation had no
catalog graph at all. The check now excludes every base Elody writes, the
pipeline-definition graphs included (`pipeline/catalog.py`,
`tests/test_component_catalog.py`). A sweep that had been reporting skips now
publishes all 64 components it finds. The hand-off exists
because the demonstrator's catalog is a checked-in file, not a store read.

## Where each piece lives

| Concern | Location |
| --- | --- |
| The catalog entry | `api/apps/dishacled/shacl/catalog/contracts.ttl` |
| Served as a component (id, form, ports) | `api/apps/dishacled/storage/local_component_source.py` |
| Ports and connections | `api/apps/dishacled/pipeline/connections.py` |
| Runnable vs. declarative (`runnable`) | `api/apps/dishacled/shacl/contracts.py` |
| Chain validation | `api/apps/dishacled/pipeline/validation.py` |
| Definition export / catalog publishing | `serializers/pipeline_definition_serializer.py`, `pipeline/catalog.py` |
| Discovery ranking (`suggest_for_shape`) | `api/apps/dishacled/storage/dishacled_httpstore.py` |
| Hand-off files, discovery query | `docs/examples/` |

Nothing in that list was added for this: the entry travels through the paths
that were already there, which is the point of it being a component.

## Still to agree

* **Transport**, with redpencil: SPARQL store (assumed here, and what the
  comment thread converged on) or an LDES feed. Only the second needs an ingest
  component with code.
* **One IRI for `ErrorShape`**, with Thomas/Koen — see above.
* **Where Elody-contributed components live** in the demonstrator's catalog
  layout: a file of our own (`catalogs/components/elody.ttl`, as the hand-off
  assumes) or an addition to `custom.ttl`. Either is a file move; it ties into
  the precedence rule in [`component-catalog.md`](component-catalog.md).
* **Whether the wrapped-config mechanism is settled** enough for Elody to read
  it as well as write it (§8 of the open questions).
* **Three unparseable files on the `proposal` branch** — `custom.ttl`,
  `rdfc.ttl`, `data.ttl` — which is what currently stops anyone validating the
  composed pipeline there at all, Elody or not. The defects and their one-line
  fixes are §5b of
  [`toolchain-open-questions.md`](toolchain-open-questions.md); Elody's local
  copy of those catalogs already carries two of the three.

## Tests

`tests/test_alert_component.py` — the catalog entry (contract, sink, config
parameters, no deployment, stable id), the component as it is served (listing,
detail, one typed input port, no output port, config form, text search), chain
validation for all three cases that matter (an alert producer is valid, a
measurement producer is a located violation naming the fields it lacks, and the
store-mediated monitor → ingest → dashboard chain validates end to end),
discovery (the dashboard is the only suggestion for a producer of alerts and
none for a producer of measurements, and the shipped SPARQL query returns it),
Elody's own definition export (the dashboard is the last step, reads from the
channel the monitor writes to, and the fragment carries `ErrorShape` in the
input role inside a catalog), the published description (no runner, no
implementation predicate, the store it requires, and a processor component
still getting its runner), the runnable export (wired, never instantiated), and
both hand-off files, parsed and checked against what this page says they
contain.

`tests/test_contracts.py` and `tests/test_connections.py` carry the two catalog
invariants this changed: "every component declares a processor class" and
"every component has an output port" now hold for the components they were
written about — a processor, and a producer — with the sink and the
non-installable component asserted as the cases they are.
