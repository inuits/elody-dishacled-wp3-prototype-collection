# The local `oslc:Error` alert fixture

A self-contained SPARQL endpoint in the dishacled stack, seeded with sample
`oslc:Error` alerts. It stands in for redpencil's error graph so alert
ingestion (A1) and visualisation (A2) can be built and tested with **no
external dependency**, and it doubles as the contract handed to redpencil (Aad)
and IDLab (Arthur): *this is the shape and the endpoint Elody expects*.

## Where it is

| | Value |
|---|---|
| Endpoint (in-network) | `http://triplestore-dishacled-wp3-prototype-elody:3030/alerts/sparql` |
| Endpoint (browser) | `http://triplestore.dishacled-wp3-prototype-elody.localhost:8000/alerts/sparql` |
| Named graph | `http://mu.semte.ch/graphs/errors` |
| Query UI | `http://triplestore.dishacled-wp3-prototype-elody.localhost:8000/` |

Read queries need no credentials.

**What reads this:** Elody queries the endpoint live through a `sparql` storage
engine and serves the alerts as ordinary entities at `/alerts` — no copy is
stored. See [`alert-ingestion.md`](alert-ingestion.md).

Both URLs and the graph are in `.env.dist` as `ALERT_SPARQL_ENDPOINT`,
`ALERT_SPARQL_ENDPOINT_EXT` and `ALERT_GRAPH`, and the first and third are
passed into the `collection-api` container — so A1 reads them from the
environment rather than hardcoding anything.

> `.env` is gitignored and is only generated from `.env.dist` by
> `task create-env`. An existing checkout will not pick the three new variables
> up on its own: re-run `task create-env`, or append them to `.env` by hand.
> Compose interpolation fails loudly if they are missing.

## Starting it

`task start-client` brings it up with the rest of the stack (the service is in
the `backend` profile). On its own:

```bash
cd clients/dishacled-wp3-prototype-elody
docker compose --profile backend up -d triplestore
```

The dataset is **in memory**, loaded from `alerts.ttl` at start-up. There is no
volume: every restart rebuilds the graph from the file, so the endpoint can
never drift from the fixture, and anything a test writes is discarded. Editing
`alerts.ttl` and restarting the container is the whole update loop.

## The data

`api/apps/dishacled/shacl/catalog/alerts.ttl` — four alerts, deliberately not
all alike:

| # | Story | Optional properties |
|---|---|---|
| 1 | cm reading above the maximum | both present |
| 2 | mm reading below the minimum | both present |
| 3 | cm reading above the maximum, later timestamp | both present |
| 4 | reading outside bounds | **neither** `oslc:largePreview` nor `dct:references` |

The fourth exists so that a consumer assuming the optional properties are
always there fails in the test suite rather than in front of a user. Alert IRIs
end in their own `mu:uuid`, so a uuid can be turned into a resource without a
lookup.

## The shape

`http://lblod.data.gift/shapes/ErrorShape`, declared in
`api/apps/dishacled/shacl/catalog/contracts.ttl`.

It is **copied verbatim** from the threshold-monitor processor's own
`processor.ttl` — repo
[`rdf-connect/threshhold-monitor-processor`](https://github.com/rdf-connect/threshhold-monitor-processor)
(three `h`s; the two-`h` spelling is a different, non-existent repo), branch
`master` (there is no `main`). Two traps worth recording: in that file the
prefix `ex:` is bound to `http://lblod.data.gift/shapes/`, **not** to
`example.org` as the same README's pipeline snippet suggests; and the README
markets `oslc:largePreview` / `dct:references` as always present while the
shape declares them optional.

| Property | Type | Card. | Meaning |
|---|---|---|---|
| `mu:uuid` | `xsd:string` | 1..1 | stable identifier |
| `dct:subject` | `xsd:string` | 1..1 | fixed `"threshold-monitor"` |
| `oslc:message` | `xsd:string` | 1..1 | what went wrong |
| `dct:created` | `xsd:dateTime` | 1..1 | when it was observed |
| `dct:creator` | IRI | 1..1 | the agent that raised it |
| `oslc:largePreview` | `xsd:string` | 0..1 | member, value, violated bound |
| `dct:references` | IRI | 0..1 | the SDS member that violated |

`sh:targetClass oslc:Error`.

### Declared once, used four ways

The shape is not hand-copied into the fixture. It lives in the contract catalog
as a component's shape, so the same triples serve the chain validation, the
exported pipeline definition, the toolchain's shape matching, and the hand-off:

```
tm:ThresholdMonitorJs   outputShape ─┐
                                     ├─→ lblodsh:ErrorShape
rdfc:SPARQLIngest       inputShape  ─┤
demo:AlertStore         outputShape ─┘
```

The first two are *overlays*: they add shapes to processors Elody already
discovers on GitHub, and say so with `dcat:landingPage`. That marker is what
keeps them from also being listed as catalog-only components — and it is why
they carry no `spdx:Package`, so the coordinates read off each repository's own
`package.json` keep winning. Verified live:

```
rdf-connect--threshhold-monitor-processor--ThresholdMonitor  writer        out  -> ErrorShape
rdf-connect--sparql-ingest-processor-ts--SPARQLIngest        memberStream  in   -> ErrorShape
```

so `threshold-monitor → sparql-ingest` validates as a chain, while feeding
`sparql-ingest` measurements instead does not. `demo:AlertStore` has no
repository, so it stays a local component and gives A1 a pickable alert source.

## Example queries

Every query below is a constant in `tests/test_alert_fixture.py` and is run
verbatim — against the fixture file, and again over HTTP against the running
endpoint when `ALERT_SPARQL_ENDPOINT` is set. Copying one out of here and into
a client is safe; if the data or the endpoint stops answering it, a test fails.

**How many alerts (SELECT):**

```sparql
SELECT (COUNT(*) AS ?n)
FROM <http://mu.semte.ch/graphs/errors>
WHERE { ?s a <http://open-services.net/ns/core#Error> }
```

**Every alert (CONSTRUCT):**

```sparql
PREFIX oslc: <http://open-services.net/ns/core#>

CONSTRUCT { ?alert ?p ?o }
WHERE {
  GRAPH <http://mu.semte.ch/graphs/errors> {
    ?alert a oslc:Error ; ?p ?o .
  }
}
```

**One alert by `mu:uuid`** — note both optionals, since alert 4 has neither:

```sparql
PREFIX oslc: <http://open-services.net/ns/core#>
PREFIX mu:   <http://mu.semte.ch/vocabularies/core/>
PREFIX dct:  <http://purl.org/dc/terms/>

SELECT ?alert ?subject ?message ?created ?creator ?detail ?reference
WHERE {
  GRAPH <http://mu.semte.ch/graphs/errors> {
    ?alert a oslc:Error ;
           mu:uuid "2f8c1d94-5a3b-4e7f-9c21-6b0d8e4a1f37" ;
           dct:subject ?subject ;
           oslc:message ?message ;
           dct:created ?created ;
           dct:creator ?creator .
    OPTIONAL { ?alert oslc:largePreview ?detail }
    OPTIONAL { ?alert dct:references ?reference }
  }
}
```

**Most recent first**, which is what A2 will want:

```sparql
PREFIX oslc: <http://open-services.net/ns/core#>
PREFIX dct:  <http://purl.org/dc/terms/>

SELECT ?alert ?message ?created
WHERE {
  GRAPH <http://mu.semte.ch/graphs/errors> {
    ?alert a oslc:Error ; oslc:message ?message ; dct:created ?created .
  }
}
ORDER BY DESC(?created)
```

From a shell:

```bash
curl -sG http://triplestore.dishacled-wp3-prototype-elody.localhost:8000/alerts/sparql \
  --data-urlencode 'query=SELECT (COUNT(*) AS ?n) FROM <http://mu.semte.ch/graphs/errors> WHERE { ?s a <http://open-services.net/ns/core#Error> }' \
  -H 'Accept: application/sparql-results+json'      # -> 4
```

## Tests

```bash
cd clients/dishacled-wp3-prototype-elody/client-collection-module
PYTHONPATH=api python -m pytest tests/test_alert_fixture.py -q
```

The default run is offline. It validates the sample data against the shape with
pyshacl, and includes a **negative control** — an alert with `oslc:message`
removed must fail — so a vacuous pass is visible.

Two gated classes, skipped unless pointed at something:

```bash
# against the running container
ALERT_SPARQL_ENDPOINT=http://triplestore.dishacled-wp3-prototype-elody.localhost:8000/alerts/sparql \
  PYTHONPATH=api python -m pytest tests/test_alert_fixture.py -q

# against the published shape, to detect drift
curl -sLo /tmp/processor.ttl \
  https://raw.githubusercontent.com/rdf-connect/threshhold-monitor-processor/master/processor.ttl
THRESHOLD_MONITOR_PROCESSOR_TTL=/tmp/processor.ttl \
  PYTHONPATH=api python -m pytest tests/test_alert_fixture.py -q
```

The second asserts graph isomorphism against upstream, which is what makes
"verbatim" a checkable claim rather than a promise.

## Known deviations from what the processor emits today

**Alerts here are IRIs; the processor writes blank nodes.** `src/index.ts` does
`const id = blankNode()`. But lblod's own consumer,
[`loket-error-alert-service`](https://github.com/lblod/loket-error-alert-service),
retrieves an alert by URI (`VALUES ?uri { … }`), and the processor's own README
example inserts `<http://example.org/errors/1>`. A blank node cannot be linked
to, cited, or deep-linked from a UI, and the processor already generates a
`mu:uuid` it could mint an IRI from. The fixture therefore uses IRIs, and the
discrepancy is raised upstream — see `docs/toolchain-open-questions.md` §5.

`ErrorShape` constrains properties, not node kind, so it accepts both forms;
the shape is not what will settle this.

## Implementation notes

`stain/jena-fuseki:5.1.0`. There is **no** `apache/jena-fuseki` image — Apache
publishes only a build-it-yourself toolkit — so the community image is the
prebuilt option, alongside `secoresearch/fuseki` (TDB+Lucene, wrong shape for a
file-on-boot fixture).

The config (`docker-compose/triplestore/fuseki-config.ttl`) is mounted at
`/staging` and passed with `--conf`, **not** placed in
`/fuseki/configuration/`. That directory is inside the image's declared
`VOLUME` and must stay writable by uid 100; a bind mount there is created
root-owned and Fuseki aborts with `Not writable: /fuseki/configuration`. `ja:data`
is resolved as a literal filesystem path, hence absolute.

The seed file lives in `client-collection-module` rather than beside the
compose file so there is exactly one copy: the container mounts that path, the
image ships it via `COPY api /app/api`, and the tests reach it through
`DEFAULT_ALERTS_PATH` instead of a relative path out of `api/`.
