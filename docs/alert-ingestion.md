# Reading the demonstrator alerts into Elody

The `oslc:Error` alerts are **queried live** and never stored: Elody holds no
copy of them in Mongo or Arango, and the SPARQL store stays the single source
of truth. Every request to `/alerts` reaches the endpoint.

This is the ingestion half of "Elody for alert visualisations"; rendering is
A2. For the endpoint itself and its sample data see
[`alert-fixture.md`](alert-fixture.md); for the catalog entry that makes this
consumption a declared step of the pipeline see
[`alert-component.md`](alert-component.md).

## The path an alert takes

```
Fuseki (or redpencil's error graph)
  │   SPARQL over HTTP
  ▼
SparqlStorageManager            collection-api/api/storage/sparqlstore.py
  │   {"iri": ..., "properties": {predicate: [value]}}
  ▼
AlertSerializer                 apps/dishacled/serializers/alert_serializer.py
  │   Elody entity
  ▼
/alerts, /alerts/filter, /alerts/<uuid>
```

The engine is generic: it knows nothing about alerts. It learns the endpoint,
the named graph and which properties identify and order a resource from the
collection's own object configuration, and hands each subject it finds to that
collection's serializer. Any other client can back a type with SPARQL by
writing those two pieces — no changes to the engine.

## Endpoints

| Route | Purpose |
|---|---|
| `GET /alerts` | list |
| `POST /alerts/filter` | list, **with paging** and identifier filters |
| `GET /alerts/<uuid>` | one alert, by `mu:uuid` |

Prefer `/alerts/filter` for listing. `GET /alerts` does not forward `skip` and
`limit` to the engine — see [Known limitations](#known-limitations) — so it
always returns the first page.

```bash
# every alert, most recent first
curl -X POST $API/alerts/filter?limit=20\&order_by=created\&asc=0 \
  -H 'Content-Type: application/json' \
  -d '[{"type": "type", "value": "alert"}]'

# just these two
curl -X POST $API/alerts/filter -H 'Content-Type: application/json' -d '[
  {"type": "selection", "key": ["identifiers"],
   "value": ["2f8c1d94-5a3b-4e7f-9c21-6b0d8e4a1f37"]}
]'
```

## Configuration

`AlertConfiguration.crud()` is the whole of it
(`apps/dishacled/object_configurations/alert_configuration.py`):

```python
"storage_type": "sparql",          # routes to the SPARQL engine
"collection": "alerts",
"type": "alert",
"sparql": {
    "endpoint": getenv("ALERT_SPARQL_ENDPOINT", ""),
    "graph": getenv("ALERT_GRAPH", ""),
    "target_class": "http://open-services.net/ns/core#Error",
    "identifier_predicate": "http://mu.semte.ch/vocabularies/core/uuid",
    "sort_predicate": "http://purl.org/dc/terms/created",
},
```

**Pointing this at redpencil's feed is two environment variables.** Set
`ALERT_SPARQL_ENDPOINT` and `ALERT_GRAPH`, restart `collection-api`, and
nothing else changes — no rebuild, no code, and A2 stays source-agnostic
because it only ever sees Elody entities. Verified by running with the endpoint
overridden; an unreachable one degrades to an empty list rather than a 500, so
a dead triplestore cannot take the API down.

## What an alert looks like as an entity

`mu:uuid` becomes `_id`; the alert's IRI is kept as a second identifier so it
can also be resolved by the address the pipeline knows it under.

| Predicate | Metadata key | |
|---|---|---|
| `dct:subject` | `subject` | always `"threshold-monitor"` |
| `oslc:message` | `message` | |
| `dct:created` | `created` | `xsd:dateTime` |
| `dct:creator` | `creator` | IRI |
| `oslc:largePreview` | `detail` | **optional** |
| `dct:references` | `references` | **optional**, the SDS member IRI |

These keys are not written out in the serializer: they are read from the shape's
own `sh:name` values, so the data and the shape-driven detail view
([`alert-rendering.md`](alert-rendering.md)) can never disagree about a field
name. That is also why the last one is `references` and not `reference`.

The two optional properties are **omitted rather than blanked** when absent, so
a consumer can tell "no detail" from "empty detail". Fixture alert 4 has
neither, which is what keeps that honest.

`oslc:largePreview` is ingested **raw, as one string** ("Member <…> has value
412.5 cm, above the configured maximum of 300 cm"). Splitting it into value,
bound and unit is a change we want from the processor rather than a guess made
by parsing prose here — pending with Arthur (IDLab).

`relations` is empty: `dct:references` points at an SDS member that lives in
the pipeline, not in Elody, so it stays metadata.

## How the queries work

Listing is three queries, and the reason is worth recording.

1. `SELECT (COUNT(DISTINCT ?s) …)` — the total, which the response envelope
   needs and a paged query cannot give.
2. `SELECT ?s ?sort … ORDER BY … OFFSET … LIMIT …` — **which** subjects are on
   this page, in order.
3. `CONSTRUCT { ?s ?p ?o } WHERE { VALUES ?s { … } GRAPH <g> { ?s ?p ?o } }` —
   every property of exactly those subjects.

Two traps this avoids:

* **`LIMIT` on a CONSTRUCT bounds triples, not resources.** A single paged
  CONSTRUCT would cut the last alert off mid-way through its properties.
  Selecting subjects first is what keeps each one whole.
* **A CONSTRUCT returns a graph, which has no order at all.** The sequence has
  to be carried over from step 2 and re-applied. This was found by running
  against the live endpoint, not by the mocked unit tests — `asc=False` came
  back ascending.

The sort property is `OPTIONAL`, so an alert lacking `dct:created` still
appears rather than vanishing from the list.

Identifiers are interpolated into a query string, so they are first held to a
conservative character set (`[A-Za-z0-9._:@-]`). Anything containing a quote,
brace, backslash or whitespace is refused before any request is made, rather
than escaped — a URL path segment must not be able to close a literal and open
a new clause.

## Known limitations

**No write-back.** The engine implements only the two read methods; clearing or
resolving an alert would need SPARQL UPDATE, which does not exist here. Whether
alerts are cleared in Elody or upstream by redpencil is still open — see
`toolchain-open-questions.md` §5.

**`GET /alerts` ignores paging.** `GenericObject.get` calls externally stored
engines with only the collection name, dropping `skip`/`limit`/`filters`, while
still advertising `skip`/`limit` and building `next`/`previous` links from
them. So paging that route silently returns the first page. This is a
pre-existing defect in `collection-api`
(`api/resources/generic_object.py`, where a comment marks it) affecting every
externally stored type, not just alerts — `/githubProcessors` has it too. It
was left unfixed deliberately, to avoid changing listing behaviour for other
clients as part of this work. `POST /alerts/filter` forwards paging correctly,
which is why it is the recommended listing route. Fixing it means forwarding
the arguments in that branch and re-checking the vliz vocab listing.

**No caching.** Every request hits the endpoint. That is what "live" means
here, and with a demonstrator-sized error graph it is not worth changing; a
large production graph might want a short TTL, as the GitHub store uses.

## Tests

```bash
# offline: the RDF -> entity mapping
PYTHONPATH=api python -m pytest tests/test_alert_serializer.py -q

# the whole chain against the running endpoint (needs the framework, so in the
# container); gated on ALERT_SPARQL_ENDPOINT the same way test_alert_fixture is
docker cp tests <collection-api-container>:/app/tests
docker exec -w /app <collection-api-container> \
  sh -c 'PYTHONPATH=/app/api python -m pytest tests/test_alert_ingestion.py -q'
```

The engine itself is tested in `collection-api`:

```bash
python -m pytest api/tests/unit/storage -q     # routing predicate + SPARQL engine
```

`test_alert_ingestion.py` includes a check that nothing lands in the database
after a list and a detail read, so "no second copy" is asserted rather than
assumed.
