"""The whole ingestion chain, against the running triplestore.

Unlike test_alert_serializer.py, this exercises the real SPARQL storage engine
and the real AlertConfiguration, so it needs both the Elody framework and a
reachable endpoint. It is therefore gated and normally runs in the container:

    docker cp tests <collection-api-container>:/app/tests
    docker exec -w /app <collection-api-container> \\
        sh -c 'PYTHONPATH=/app/api python -m pytest tests/test_alert_ingestion.py -q'

Skipped by default so the suite keeps working offline, the same way
TestLiveEndpoint in test_alert_fixture.py is.
"""

import os

import pytest

ENDPOINT = os.getenv("ALERT_SPARQL_ENDPOINT")

pytestmark = pytest.mark.skipif(
    not ENDPOINT,
    reason="set ALERT_SPARQL_ENDPOINT to check ingestion against a live endpoint",
)

# The first fixture alert; see api/apps/dishacled/shacl/catalog/alerts.ttl.
KNOWN_UUID = "2f8c1d94-5a3b-4e7f-9c21-6b0d8e4a1f37"


@pytest.fixture(scope="module")
def store():
    from configuration import init_mappers

    init_mappers()
    from storage.sparqlstore import SparqlStorageManager

    return SparqlStorageManager()


def metadata(entity):
    return {item["key"]: item["value"] for item in entity["metadata"]}


class TestTheConfigurationIsWiredUp:
    def test_the_alert_collection_routes_to_the_sparql_engine(self, store):
        from configuration import get_object_configuration_mapper, init_mappers
        from storage.routing import uses_external_storage

        init_mappers()
        crud = get_object_configuration_mapper().get("alerts").crud()
        assert crud["storage_type"] == "sparql"
        assert uses_external_storage(crud["storage_type"])

    def test_the_endpoint_and_graph_come_from_the_environment(self, store):
        config = store._sparql_config("alerts")
        assert config["endpoint"] == ENDPOINT
        assert config["graph"] == os.getenv("ALERT_GRAPH")


class TestListing:
    def test_the_fixture_alerts_come_back_as_entities(self, store):
        result = store.get_items_from_collection("alerts")
        assert result["count"] == 4
        assert len(result["results"]) == 4
        for entity in result["results"]:
            assert entity["type"] == "alert"
            values = metadata(entity)
            # The five properties the shape makes mandatory.
            assert values["subject"] == "threshold-monitor"
            assert values["message"]
            assert values["created"]
            assert values["creator"]
            assert entity["_id"] in entity["identifiers"]

    def test_the_most_recent_alert_comes_first(self, store):
        result = store.get_items_from_collection("alerts", asc=False)
        created = [metadata(entity)["created"] for entity in result["results"]]
        assert created == sorted(created, reverse=True)

    def test_the_alert_without_optional_properties_still_arrives(self, store):
        result = store.get_items_from_collection("alerts")
        bare = [
            entity
            for entity in result["results"]
            if "detail" not in metadata(entity)
            and "references" not in metadata(entity)
        ]
        # Alert 4 exists precisely so a consumer assuming the optionals fails
        # here rather than in front of a user.
        assert len(bare) == 1

    def test_paging_does_not_cut_an_alert_in_half(self, store):
        # Every returned alert must be complete even when the page boundary
        # falls in the middle of the result set.
        first = store.get_items_from_collection("alerts", skip=0, limit=2)
        second = store.get_items_from_collection("alerts", skip=2, limit=2)
        assert len(first["results"]) == 2
        assert len(second["results"]) == 2
        assert first["count"] == second["count"] == 4
        for entity in first["results"] + second["results"]:
            assert set(metadata(entity)) >= {
                "subject",
                "message",
                "created",
                "creator",
            }
        ids = {e["_id"] for e in first["results"]} | {
            e["_id"] for e in second["results"]
        }
        assert len(ids) == 4

    def test_an_identifiers_filter_restricts_the_result(self, store):
        result = store.get_items_from_collection("alerts", filters={"ids": [KNOWN_UUID]})
        assert [entity["_id"] for entity in result["results"]] == [KNOWN_UUID]


class TestByIdentifier:
    def test_an_alert_is_retrievable_by_its_uuid(self, store):
        entity = store.get_item_from_collection_by_id("alerts", KNOWN_UUID)
        assert entity["_id"] == KNOWN_UUID
        values = metadata(entity)
        assert values["subject"] == "threshold-monitor"
        assert values["detail"]
        assert values["references"]

    def test_an_unknown_uuid_returns_nothing(self, store):
        assert store.get_item_from_collection_by_id("alerts", "no-such-alert") == {}

    def test_an_injection_attempt_returns_nothing(self, store):
        assert (
            store.get_item_from_collection_by_id(
                "alerts", '" } INSERT DATA { <http://x> <http://y> "z'
            )
            == {}
        )


class TestNothingIsPersisted:
    def test_reading_alerts_writes_nothing_to_the_database(self, store):
        from storage.storagemanager import StorageManager

        store.get_items_from_collection("alerts")
        store.get_item_from_collection_by_id("alerts", KNOWN_UUID)

        database = StorageManager().get_db_engine()
        stored = database.get_items_from_collection("alerts", limit=100)
        assert not (stored or {}).get("results"), (
            "alerts must stay in the SPARQL store, not be copied into the database"
        )
