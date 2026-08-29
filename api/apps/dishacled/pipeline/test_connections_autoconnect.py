"""Auto-connect: a component added to a pipeline is wired up when the wiring
is unambiguous enough to write down.

The pipeline configuration's pre-crud hook calls this on every save: a *newly
added* hasProcessor relation whose input shape matches the output shape of a
component already in the pipeline gets its `connections.<port>.from` metadata
written, so picking a component from the (already shape-scoped) picker is the
whole gesture -- no separate connect step. Existing relations are never
touched: a deliberately disconnected component stays disconnected.
"""

from apps.dishacled.pipeline.connections import autoconnect_new_relations


CM = "https://x/shapes/MeasurementsInCm"
ERROR = "https://x/shapes/ErrorShape"

PORTS = {
    "water": [{"name": "output", "direction": "out", "shapeIri": CM}],
    "monitor": [
        {"name": "input", "direction": "in", "shapeIri": CM},
        {"name": "output", "direction": "out", "shapeIri": ERROR},
    ],
    "monitor-2": [
        {"name": "input", "direction": "in", "shapeIri": CM},
        {"name": "output", "direction": "out", "shapeIri": ERROR},
    ],
    "dashboard": [{"name": "alerts", "direction": "in", "shapeIri": ERROR}],
    "dataset": [{"name": "output", "direction": "out", "shapeIri": CM}],
}


def ports_of(key):
    return PORTS.get(key, [])


def relation(key, metadata=None):
    return {"key": key, "type": "hasProcessor", "metadata": metadata or []}


def connection_values(rel):
    return {
        entry["key"]: entry["value"]
        for entry in rel.get("metadata", [])
        if entry["key"].startswith("connections.")
    }


def test_new_consumer_connects_to_the_matching_producer():
    relations = [relation("water"), relation("monitor")]

    connected = autoconnect_new_relations(relations, [relation("water")], ports_of)

    assert connected == 1
    assert connection_values(relations[1]) == {
        "connections.input.from": "water|output"
    }


def test_existing_unconnected_relations_stay_untouched():
    relations = [relation("water"), relation("monitor")]
    previous = [relation("water"), relation("monitor")]

    connected = autoconnect_new_relations(relations, previous, ports_of)

    assert connected == 0
    assert connection_values(relations[1]) == {}


def test_no_matching_shape_leaves_the_new_relation_unconnected():
    relations = [relation("water"), relation("dashboard")]

    connected = autoconnect_new_relations(relations, [relation("water")], ports_of)

    assert connected == 0
    assert connection_values(relations[1]) == {}


def test_the_most_recently_added_matching_producer_wins():
    relations = [relation("water"), relation("dataset"), relation("monitor")]
    previous = [relation("water"), relation("dataset")]

    autoconnect_new_relations(relations, previous, ports_of)

    assert connection_values(relations[2]) == {
        "connections.input.from": "dataset|output"
    }


def test_a_connection_already_written_is_respected():
    already = relation(
        "monitor",
        [{"key": "connections.input.from", "value": "dataset|output"}],
    )
    relations = [relation("water"), relation("dataset"), already]

    connected = autoconnect_new_relations(
        relations, [relation("water"), relation("dataset")], ports_of
    )

    assert connected == 0
    assert connection_values(relations[2]) == {
        "connections.input.from": "dataset|output"
    }


def test_a_component_never_feeds_itself():
    relations = [relation("monitor")]

    connected = autoconnect_new_relations(relations, [], ports_of)

    assert connected == 0
    assert connection_values(relations[0]) == {}


def test_a_pure_producer_has_nothing_to_connect():
    relations = [relation("monitor"), relation("water")]

    connected = autoconnect_new_relations(relations, [relation("monitor")], ports_of)

    assert connected == 0
    assert connection_values(relations[1]) == {}


def test_two_new_components_chain_in_order():
    # created in one save (e.g. the guided flow): water then monitor then
    # dashboard, every link resolvable within the same batch
    relations = [relation("water"), relation("monitor"), relation("dashboard")]

    connected = autoconnect_new_relations(relations, [], ports_of)

    assert connected == 2
    assert connection_values(relations[1]) == {
        "connections.input.from": "water|output"
    }
    assert connection_values(relations[2]) == {
        "connections.alerts.from": "monitor|output"
    }
