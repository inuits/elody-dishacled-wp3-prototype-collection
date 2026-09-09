"""A step's identity is written down, not inferred from what happens to differ.

A pipeline step is a *use* of a component, so the same component can be used
twice -- the toolchain's own reference definition has two `LogProcessorJs`
steps. Until now the two were told apart only by accident: they differed
because the user had configured one of them, or because a store round-trip had
qualified their keys. Two freshly added, unconfigured steps of one component
are byte-identical relations, and a store cannot tell them apart -- the second
one was dropped before any pipeline code saw it, which is what made "add this
component again" look impossible.

So the pipeline's own configuration mints the identity on save: it is the one
place that knows a relation is a step. See
`object_configurations/pipeline_configuration.py`.
"""

from apps.dishacled.pipeline.connections import (
    INSTANCE_FIELD,
    INSTANCE_SEPARATOR,
    assign_step_instances,
    instances_of,
)


LOGGER = "local--logger"
POLLER = "local--http-poller-cm"


def component(identifier, name):
    return {
        "_id": identifier,
        "metadata": [{"key": "name", "value": name}],
        "data": {"componentIri": f"https://example.org/{identifier}"},
    }


COMPONENTS = {
    LOGGER: component(LOGGER, "Log processor"),
    POLLER: component(POLLER, "HTTP poller (cm)"),
}


def relation(key, metadata=None):
    return {"key": key, "type": "hasProcessor", "metadata": list(metadata or [])}


def instance_of(relation):
    for item in relation.get("metadata") or []:
        if item.get("key") == INSTANCE_FIELD:
            return item.get("value")
    return None


class TestARepeatedComponent:
    def test_each_use_gets_an_identity(self):
        relations = [relation(LOGGER), relation(LOGGER)]

        assigned = assign_step_instances(relations, COMPONENTS)

        assert assigned == 2
        assert [instance_of(r) for r in relations] == [
            "log-processor",
            "log-processor-2",
        ]

    def test_the_identities_are_distinct_so_the_relations_are(self):
        """Which is the whole point: a store can now keep both."""
        relations = [relation(LOGGER), relation(LOGGER)]
        assign_step_instances(relations, COMPONENTS)
        assert relations[0] != relations[1]

    def test_a_use_that_already_has_one_keeps_it(self):
        relations = [
            relation(LOGGER, [{"key": INSTANCE_FIELD, "value": "first"}]),
            relation(LOGGER),
        ]

        assign_step_instances(relations, COMPONENTS)

        assert [instance_of(r) for r in relations] == ["first", "log-processor"]

    def test_a_qualified_key_is_identity_enough(self):
        """After a store round-trip the key carries it, so nothing is rewritten."""
        relations = [
            relation(f"{LOGGER}{INSTANCE_SEPARATOR}log-processor"),
            relation(f"{LOGGER}{INSTANCE_SEPARATOR}log-processor-2"),
        ]

        assert assign_step_instances(relations, COMPONENTS) == 0
        assert [instance_of(r) for r in relations] == [None, None]

    def test_adding_a_third_continues_the_sequence(self):
        """And does not compound: `-3`, never `-2-2`."""
        relations = [
            relation(f"{LOGGER}{INSTANCE_SEPARATOR}log-processor"),
            relation(f"{LOGGER}{INSTANCE_SEPARATOR}log-processor-2"),
            relation(LOGGER),
        ]

        assign_step_instances(relations, COMPONENTS)

        assert instance_of(relations[-1]) == "log-processor-3"

    def test_the_identity_is_the_component_s_name_not_its_id(self):
        relations = [relation(POLLER), relation(POLLER)]
        assign_step_instances(relations, COMPONENTS)
        assert [instance_of(r) for r in relations] == [
            "http-poller-cm",
            "http-poller-cm-2",
        ]

    def test_it_falls_back_to_the_id_when_the_component_is_unknown(self):
        """An unreachable component must not stop a step from being saved."""
        relations = [relation("acme--mystery"), relation("acme--mystery")]

        assign_step_instances(relations, {})

        assert [instance_of(r) for r in relations] == [
            "acme-mystery",
            "acme-mystery-2",
        ]


class TestASingleUse:
    def test_nothing_is_written(self):
        """One use needs no identity: the key is unambiguous on its own.

        Left alone deliberately -- writing metadata on every save would make
        every save a change to the stored document.
        """
        relations = [relation(LOGGER), relation(POLLER)]

        assert assign_step_instances(relations, COMPONENTS) == 0
        assert [instance_of(r) for r in relations] == [None, None]

    def test_other_relation_types_are_untouched(self):
        relations = [
            relation(LOGGER),
            {"key": LOGGER, "type": "hasSomethingElse", "metadata": []},
        ]

        assert assign_step_instances(relations, COMPONENTS) == 0


class TestItAgreesWithHowStepsAreRead:
    def test_the_ids_are_the_ones_instances_of_derives(self):
        """One rule for a step's id, wherever it is computed."""
        relations = [relation(LOGGER), relation(LOGGER)]
        expected = [i.id for i in instances_of({"relations": relations}, COMPONENTS)]

        assign_step_instances(relations, COMPONENTS)

        assert [instance_of(r) for r in relations] == expected

    def test_reading_them_back_yields_the_same_steps(self):
        relations = [relation(LOGGER), relation(LOGGER)]
        assign_step_instances(relations, COMPONENTS)

        steps = instances_of({"relations": relations}, COMPONENTS)

        assert [s.id for s in steps] == ["log-processor", "log-processor-2"]
        assert len({s.id for s in steps}) == 2
