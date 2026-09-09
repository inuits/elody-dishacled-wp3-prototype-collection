"""Adding a step to a pipeline that already has one of the same component.

The framework writes a new document's relations in an operation of its own,
which runs no crud hook -- so `_pre_crud_hook` never sees two steps added
together, and the engine's "equal entries are one entry" rule dropped the
second. The engine asks the collection what tells its entries apart
(`sparqlstore._identified_sub_items`); this is the pipeline's answer.
"""

from unittest.mock import MagicMock, patch

from apps.dishacled.object_configurations.pipeline_configuration import (
    PipelineConfiguration,
)
from apps.dishacled.pipeline.connections import INSTANCE_FIELD, INSTANCE_SEPARATOR


LOGGER = "local--logger"
COMPONENT = {
    "_id": LOGGER,
    "metadata": [{"key": "name", "value": "Log processor"}],
    "data": {"ports": []},
}


def relation(key, metadata=None):
    return {"key": key, "type": "hasProcessor", "metadata": list(metadata or [])}


def instance_of(relation):
    for item in relation.get("metadata") or []:
        if item.get("key") == INSTANCE_FIELD:
            return item.get("value")
    return None


def identify(existing, content):
    """Call the configuration the way the engine does, with storage stubbed."""
    storage = MagicMock()
    storage.get_item_from_collection_by_id.return_value = COMPONENT
    mapper = MagicMock()
    mapper.get.return_value = lambda: storage
    with patch("configuration.get_storage_mapper", return_value=mapper):
        return PipelineConfiguration().identify_sub_items(
            sub_item="relations", existing=existing, content=content
        )


class TestAddingASecondUse:
    def test_the_new_step_is_identified_against_the_existing_one(self):
        existing = [relation(LOGGER)]
        added = identify(existing, [relation(LOGGER)])

        assert len(added) == 1
        assert instance_of(added[0]) == "log-processor-2"
        # and the one already there is told which step it is, since it is now
        # one of two -- the store writes both back
        assert instance_of(existing[0]) == "log-processor"

    def test_two_added_at_once_are_two_steps(self):
        added = identify([], [relation(LOGGER), relation(LOGGER)])

        assert [instance_of(r) for r in added] == ["log-processor", "log-processor-2"]
        assert added[0] != added[1]

    def test_an_add_of_a_different_component_is_left_alone(self):
        added = identify([relation(LOGGER)], [relation("local--poller")])

        assert instance_of(added[0]) is None

    def test_a_repeat_of_an_already_qualified_step_continues_the_sequence(self):
        existing = [
            relation(f"{LOGGER}{INSTANCE_SEPARATOR}log-processor"),
            relation(f"{LOGGER}{INSTANCE_SEPARATOR}log-processor-2"),
        ]
        added = identify(existing, [relation(LOGGER)])

        assert instance_of(added[0]) == "log-processor-3"

    def test_other_sub_items_are_passed_through(self):
        metadata = [{"key": "name", "value": "x"}]
        assert (
            PipelineConfiguration().identify_sub_items(
                sub_item="metadata", existing=[], content=metadata
            )
            is metadata
        )
