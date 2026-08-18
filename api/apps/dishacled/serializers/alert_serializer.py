"""Turning an `oslc:Error` resource into an Elody entity.

The SPARQL storage engine is vocabulary-agnostic: it groups whatever the
endpoint returns by subject and hands each one over as

    {"iri": ..., "properties": {predicate: [value, ...]}}

leaving the interpretation here. Which predicate becomes which metadata key is
not written out below -- it is read from `lblodsh:ErrorShape` in the contract
catalog, the same shape the detail view is rendered from. That is what keeps the
data and the UI describing the same thing; a hand-kept copy of the mapping had
already drifted from the shape on one field. See docs/alert-rendering.md.
"""

from apps.dishacled.shacl.alert_shape import alert_field_names

MU = "http://mu.semte.ch/vocabularies/core/"

IDENTIFIER = f"{MU}uuid"


class AlertSerializer:
    def field_names(self) -> dict[str, str]:
        """`{predicate: metadata key}`, straight from the shape."""
        return alert_field_names()

    def from_sparql_to_elody(self, subject, **kwargs):
        properties = subject.get("properties", {})
        uuid = _first(properties, IDENTIFIER)
        if not uuid:
            # Without mu:uuid there is nothing to address the alert by. The
            # engine already skips these; refuse here too so the serializer is
            # safe to call on its own.
            return {}

        iri = subject.get("iri", "")
        # Every property the shape declares, including mu:uuid -- the detail
        # view is rendered from that same shape, so anything omitted here would
        # render as an empty field.
        metadata = [
            {"key": key, "value": value}
            for predicate, key in self.field_names().items()
            if (value := _first(properties, predicate)) is not None
        ]
        return {
            "_id": uuid,
            "identifiers": [uuid, iri] if iri else [uuid],
            "type": "alert",
            "metadata": metadata,
            # dct:references points at an SDS member that lives in the pipeline,
            # not in Elody, so it stays metadata rather than becoming a relation.
            "relations": [],
        }

    def from_elody_filter_to_sparql_filter(self, filters, **kwargs):
        restrictions = {}
        for filter in filters:
            if filter.get("type") != "selection":
                continue
            if "identifiers" not in str(filter.get("key", [])):
                continue
            value = filter.get("value")
            if value is None:
                ids = []
            elif isinstance(value, list):
                ids = value
            else:
                ids = [value]
            # A selection restricts the result to exactly these identifiers, so
            # an empty list means "no matches" rather than "no restriction" --
            # the key is set either way. Same rule as GithubSerializer.
            restrictions["ids"] = ids
        return restrictions

    def from_elody_to_sparql(self, entity, **kwargs):
        # Alerts are read-only; nothing writes back to the error graph yet.
        return entity


def _first(properties, predicate):
    """The single value of a property the shape declares as maxCount 1."""
    values = properties.get(predicate) or []
    return values[0] if values else None
