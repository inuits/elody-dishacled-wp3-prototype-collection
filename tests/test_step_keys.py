"""A step's identity lives in the relation key, so the UI can address it.

Everything the framework does with a related entity is keyed by `relation.key`:
the processor list fetches one row per key, and a config modal writes back to
the relation whose key matches the row it was opened on
(`useFormHelper.parseRelationMetadataForFormSubmit`: `if (relation.key === id)`).

So a step that the UI can show and configure separately has to *be* a distinct
key. It is the component's id with the step appended:

    rdf-connect--log-processor-ts--LogProcessorJs~logprocessorjs-2

The store resolves such a key to the component it names, so the row renders and
its config form is the component's shape -- while the key stays distinct, which
is what gives two loggers two rows and two configurations.
"""

import base64
from unittest.mock import MagicMock

from apps.dishacled.pipeline.connections import (
    INSTANCE_SEPARATOR,
    instances_of,
    split_component_key,
)
from apps.dishacled.serializers.pipeline_definition_serializer import (
    PipelineDefinitionSerializer,
)
from apps.dishacled.serializers.pipeline_serializer import PipelineSerializer
from apps.dishacled.storage.dishacled_httpstore import DishacledHttpStorageManager

from tests.test_step_instances import COMPONENTS, LOGGER, TICKER, LOGGER_TTL
from rdflib import Graph
def _step_slugs(ttl: str) -> set[str]:
    """The step IRIs a definition declares, by their last path segment.

    Read off the parsed graph rather than the text: a step compacts to
    `step:<slug>` now that the document binds a prefix for the step namespace
    (the generator interpolates compacted IRIs into SPARQL, so they have to be
    legal CURIEs), and what these tests are about is which steps exist.
    """
    from rdflib import Graph, URIRef
    from rdflib.namespace import RDF

    graph = Graph()
    graph.parse(data=ttl, format="turtle")
    return {
        str(step).rsplit("/", 1)[-1]
        for step in graph.subjects(
            RDF.type, URIRef("https://w3id.org/toolchain#InstancePipelineComponent")
        )
    }


QUALIFIED = f"{LOGGER}{INSTANCE_SEPARATOR}logprocessorjs-2"


class TestKeysCarryTheStep:
    def test_a_bare_key_names_no_step(self):
        assert split_component_key(LOGGER) == (LOGGER, None)

    def test_a_qualified_key_names_one(self):
        assert split_component_key(QUALIFIED) == (LOGGER, "logprocessorjs-2")

    def test_the_separator_is_url_safe(self):
        # ids travel in paths (`/processors/<id>/shui.ttl`) and in filters
        from urllib.parse import quote

        assert quote(INSTANCE_SEPARATOR, safe="") == INSTANCE_SEPARATOR

    def test_an_instance_from_the_key_wins(self):
        pipeline = {
            "relations": [
                {"key": QUALIFIED, "type": "hasProcessor", "metadata": []},
            ]
        }
        instances = instances_of(pipeline, {QUALIFIED: COMPONENTS[LOGGER]})
        assert [i.id for i in instances] == ["logprocessorjs-2"]

    def test_the_component_behind_a_qualified_key_is_reachable(self):
        pipeline = {
            "relations": [{"key": QUALIFIED, "type": "hasProcessor", "metadata": []}]
        }
        instance = instances_of(pipeline, {QUALIFIED: COMPONENTS[LOGGER]})[0]
        assert instance.component_id == LOGGER

    def test_a_bare_key_still_has_a_component(self):
        pipeline = {
            "relations": [{"key": LOGGER, "type": "hasProcessor", "metadata": []}]
        }
        instance = instances_of(pipeline, COMPONENTS)[0]
        assert instance.component_id == LOGGER


def _github(ttl=LOGGER_TTL):
    repo = {
        "owner": {"login": "rdf-connect"},
        "name": "log-processor-ts",
        "full_name": "rdf-connect/log-processor-ts",
        "html_url": "https://github.com/rdf-connect/log-processor-ts",
        "default_branch": "main",
        "language": "TypeScript",
    }

    def _response(payload, code=200):
        response = MagicMock()
        response.status_code = code
        response.json.return_value = payload
        return response

    def _get(url, **kwargs):
        if "/git/trees/" in url:
            return _response({"tree": [{"path": "processor.ttl"}]})
        if url.endswith((".json", ".toml")):
            return _response({}, 404)
        if "/contents/" in url:
            return _response(
                {
                    "content": base64.b64encode(ttl.encode()).decode(),
                    "encoding": "base64",
                }
            )
        return _response(repo)

    store = DishacledHttpStorageManager()
    store.session = MagicMock()
    store.session.get.side_effect = _get
    return store


REAL_KEY = "rdf-connect--log-processor-ts--LogProcessorJs"


class TestTheStoreResolvesAStepKey:
    def test_it_resolves_to_the_component_it_names(self):
        item = _github().get_item_from_collection_by_id(
            "githubProcessors", f"{REAL_KEY}{INSTANCE_SEPARATOR}logprocessorjs-2"
        )
        assert item["data"]["componentIri"] == (
            "https://w3id.org/rdf-connect#LogProcessorJs"
        )

    def test_the_row_keeps_the_step_as_its_id(self):
        # distinct ids are what give two steps two rows in the processor list
        key = f"{REAL_KEY}{INSTANCE_SEPARATOR}logprocessorjs-2"
        item = _github().get_item_from_collection_by_id("githubProcessors", key)
        assert item["_id"] == key
        assert item["identifiers"] == [key]

    def test_it_says_which_component_it_is(self):
        item = _github().get_item_from_collection_by_id(
            "githubProcessors", f"{REAL_KEY}{INSTANCE_SEPARATOR}logprocessorjs-2"
        )
        assert item["data"]["componentId"] == REAL_KEY
        assert item["data"]["instance"] == "logprocessorjs-2"

    def test_the_name_tells_the_two_apart(self):
        item = _github().get_item_from_collection_by_id(
            "githubProcessors", f"{REAL_KEY}{INSTANCE_SEPARATOR}logprocessorjs-2"
        )
        name = next(m["value"] for m in item["metadata"] if m["key"] == "name")
        assert "logprocessorjs-2" in name

    def test_it_still_carries_the_config_form(self):
        item = _github().get_item_from_collection_by_id(
            "githubProcessors", f"{REAL_KEY}{INSTANCE_SEPARATOR}logprocessorjs-2"
        )
        assert set(item["data"]["formFields"]) >= {"reader", "label", "level"}

    def test_a_bare_key_is_unchanged(self):
        item = _github().get_item_from_collection_by_id("githubProcessors", REAL_KEY)
        assert item["_id"] == REAL_KEY
        assert item["data"].get("instance") is None


class TestTheStoredPipelineUsesStepKeys:
    """So the next read gives the UI one addressable row per step."""

    def _read_back(self):
        pipeline = {
            "_id": "pipeline-1",
            "type": "pipeline",
            "identifiers": ["pipeline-1"],
            "metadata": [{"key": "name", "value": "Two loggers"}],
            "relations": [
                {"key": TICKER, "type": "hasProcessor", "metadata": []},
                {"key": LOGGER, "type": "hasProcessor", "metadata": [
                    {"key": "label", "value": "report"}]},
                {"key": LOGGER, "type": "hasProcessor", "metadata": [
                    {"key": "label", "value": "output"}]},
            ],
        }
        ttl = PipelineDefinitionSerializer(
            base_uri="http://elody.local/pipelines/pipeline-1/"
        ).serialize(pipeline, COMPONENTS)
        graph = Graph()
        graph.parse(data=ttl, format="turtle")
        return PipelineSerializer().from_sparql_to_elody({"graph": graph})

    def test_each_relation_has_a_key_of_its_own(self):
        keys = [
            r["key"] for r in self._read_back()["relations"]
            if r["type"] == "hasProcessor"
        ]
        assert len(set(keys)) == 3

    def test_the_keys_name_the_component_and_the_step(self):
        keys = {
            r["key"] for r in self._read_back()["relations"]
            if r["key"].startswith(LOGGER)
        }
        assert keys == {
            f"{LOGGER}{INSTANCE_SEPARATOR}logprocessorjs",
            f"{LOGGER}{INSTANCE_SEPARATOR}logprocessorjs-2",
        }

    def test_the_configurations_stay_with_their_step(self):
        by_key = {
            r["key"]: {m["key"]: m["value"] for m in r["metadata"]}
            for r in self._read_back()["relations"]
        }
        assert by_key[f"{LOGGER}{INSTANCE_SEPARATOR}logprocessorjs"]["label"] == "report"
        assert by_key[f"{LOGGER}{INSTANCE_SEPARATOR}logprocessorjs-2"]["label"] == "output"

    def test_re_exporting_from_step_keys_is_stable(self):
        entity = self._read_back()
        components = {
            r["key"]: COMPONENTS[split_component_key(r["key"])[0]]
            for r in entity["relations"]
        }
        ttl = PipelineDefinitionSerializer(
            base_uri="http://elody.local/pipelines/pipeline-1/"
        ).serialize(entity, components)
        assert {"logprocessorjs", "logprocessorjs-2"} <= _step_slugs(ttl)
        assert '"report"' in ttl and '"output"' in ttl

    def test_the_catalog_fragment_names_the_component_not_the_step(self):
        # `dct:identifier` is how a definition says which component a step
        # specializes; qualifying it would make the step unresolvable
        entity = self._read_back()
        components = {
            r["key"]: COMPONENTS[split_component_key(r["key"])[0]]
            for r in entity["relations"]
        }
        ttl = PipelineDefinitionSerializer(
            base_uri="http://elody.local/pipelines/pipeline-1/"
        ).serialize(entity, components)
        assert f'"{LOGGER}"' in ttl
        assert f'"{LOGGER}{INSTANCE_SEPARATOR}' not in ttl


class TestKeysDoNotCompound:
    """Saving twice must not grow the keys.

    A step's document is the component addressed as that step, so its `_id` is
    `component~step`. Writing *that* into the fragment as the component's
    `dct:identifier` makes the next read append the step again, and the key
    grows by one segment per save:

        LogProcessorJs~logprocessorjs
        LogProcessorJs~logprocessorjs~logprocessorjs
        LogProcessorJs~logprocessorjs~logprocessorjs~logprocessorjs

    The identifier names the *component*; the step is the IRI's slug.
    """

    def _save_and_read(self, entity):
        components = {}
        for relation in entity["relations"]:
            component_id, instance = split_component_key(relation["key"])
            document = dict(COMPONENTS[component_id])
            if instance:
                # what the store serves for a step key
                document = {
                    **document,
                    "_id": relation["key"],
                    "data": {
                        **document["data"],
                        "componentId": component_id,
                        "instance": instance,
                    },
                }
            components[relation["key"]] = document

        ttl = PipelineDefinitionSerializer(
            base_uri="http://elody.local/pipelines/pipeline-1/"
        ).serialize(entity, components)
        graph = Graph()
        graph.parse(data=ttl, format="turtle")
        return PipelineSerializer().from_sparql_to_elody({"graph": graph}), ttl

    def _pipeline(self):
        return {
            "_id": "pipeline-1",
            "type": "pipeline",
            "identifiers": ["pipeline-1"],
            "metadata": [{"key": "name", "value": "Two loggers"}],
            "relations": [
                {"key": LOGGER, "type": "hasProcessor", "metadata": [
                    {"key": "label", "value": "report"}]},
                {"key": LOGGER, "type": "hasProcessor", "metadata": [
                    {"key": "label", "value": "output"}]},
            ],
        }

    def test_the_keys_are_the_same_after_three_saves(self):
        entity = self._pipeline()
        generations = []
        for _ in range(3):
            entity, _ttl = self._save_and_read(entity)
            generations.append([r["key"] for r in entity["relations"]])
        assert generations[0] == generations[1] == generations[2]

    def test_they_name_the_step_once(self):
        entity = self._pipeline()
        for _ in range(3):
            entity, _ttl = self._save_and_read(entity)
        for relation in entity["relations"]:
            assert relation["key"].count(INSTANCE_SEPARATOR) == 1

    def test_the_identifier_in_the_fragment_is_the_component(self):
        entity, ttl = self._save_and_read(self._pipeline())
        entity, ttl = self._save_and_read(entity)
        assert f'dct:identifier "{LOGGER}"' in ttl.replace("\n", " ").replace(
            "  ", " "
        ) or f'"{LOGGER}"' in ttl

    def test_the_configurations_survive_three_saves(self):
        entity = self._pipeline()
        for _ in range(3):
            entity, _ttl = self._save_and_read(entity)
        labels = {
            m["value"]
            for r in entity["relations"]
            for m in r["metadata"]
            if m["key"] == "label"
        }
        assert labels == {"report", "output"}
