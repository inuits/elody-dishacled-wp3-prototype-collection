"""A save must never lose a step.

The triple store is a pipeline's only home (`docs/pipeline-storage.md`), and a
save is a whole-graph PUT. So a definition that cannot represent one of the
pipeline's processors does not describe a smaller pipeline -- it *deletes* that
processor, permanently, with no error anywhere.

That is what happened: a step whose component could not be described was
skipped by `_resolve_stages` (`if not shape: continue`), the shortened
definition was PUT over the pipeline, and the step was gone. The trigger is
ordinary -- GitHub rate limited, offline, a repository renamed -- and the loss
outlives it.

So an incomplete definition is refused rather than written, and the store keeps
what it has.
"""

import pytest
from rdflib import Namespace, URIRef

from apps.dishacled.pipeline import publication
from apps.dishacled.pipeline.publication import IncompletePipeline
from apps.dishacled.serializers.pipeline_definition_serializer import (
    PipelineDefinitionSerializer,
)

from tests.test_pipeline_publication import (
    BASE_URI,
    COMPONENTS,
    ENDPOINT,
    GRAPH_BASE,
    PIPELINE_GRAPH,
    VALID,
    store,  # noqa: F401 -- the FakeStore fixture
)
from tests.test_validation import connected, make_pipeline, processor_relation


# A component the store could not describe: this is exactly the placeholder
# `DishacledHttpStorageManager` falls back to when GitHub cannot be reached.
UNRESOLVED_ID = "rdf-connect--rml-processor-jvm--RmlMapper"
UNRESOLVED = {
    "_id": UNRESOLVED_ID,
    "type": "githubProcessor",
    "metadata": [
        {"key": "name", "value": "RmlMapper"},
        {"key": "repository", "value": "rdf-connect/rml-processor-jvm"},
    ],
    "relations": [],
    "data": {"unresolved": True},
}

WITH_UNRESOLVED = [
    *VALID,
    connected(UNRESOLVED_ID, "mappings", "poller-cm|output"),
]
COMPONENTS_WITH_UNRESOLVED = {**COMPONENTS, UNRESOLVED_ID: UNRESOLVED}


def _serialize(relations, components):
    serializer = PipelineDefinitionSerializer(base_uri=f"{BASE_URI}/pipelines/pipeline-1/")
    ttl = serializer.serialize(make_pipeline(relations), components)
    return serializer, ttl


class TestTheSerializerSaysWhatItCouldNotRepresent:
    def test_a_component_without_a_shape_is_reported(self):
        serializer, _ = _serialize(WITH_UNRESOLVED, COMPONENTS_WITH_UNRESOLVED)
        assert serializer.unrepresented == [UNRESOLVED_ID]

    def test_a_component_that_could_not_be_fetched_at_all_is_reported(self):
        # not in `components`: `load_pipeline_components` leaves out what it
        # cannot fetch, so the relation has nothing behind it
        serializer, _ = _serialize(WITH_UNRESOLVED, COMPONENTS)
        assert serializer.unrepresented == [UNRESOLVED_ID]

    def test_a_complete_pipeline_reports_nothing(self):
        serializer, _ = _serialize(VALID, COMPONENTS)
        assert serializer.unrepresented == []

    def test_a_dataset_counts_as_represented(self):
        # a dcat:Dataset is not a step -- it is `dcterms:source` of the plan --
        # so it is not missing either
        dataset_id = "local--alert-store"
        dataset = {
            "_id": dataset_id,
            "type": "githubProcessor",
            "metadata": [{"key": "name", "value": "Alert store"}],
            "data": {
                "componentKind": "dataset",
                "componentIri": "https://dishacled.github.io/demo#AlertStore",
            },
        }
        serializer, ttl = _serialize(
            [*VALID, processor_relation(dataset_id)],
            {**COMPONENTS, dataset_id: dataset},
        )
        assert serializer.unrepresented == []
        assert "dcterms:source" in ttl or "dct:source" in ttl


class TestAnIncompleteDefinitionIsRefused:
    def test_building_one_for_the_store_raises(self):
        with pytest.raises(IncompletePipeline) as raised:
            publication.definition_for_store(
                make_pipeline(WITH_UNRESOLVED),
                components=COMPONENTS_WITH_UNRESOLVED,
            )
        assert UNRESOLVED_ID in raised.value.keys
        assert "RmlMapper" in str(raised.value) or UNRESOLVED_ID in str(raised.value)

    def test_publishing_one_touches_nothing(self, store):  # noqa: F811
        # the whole point: no PUT (which would drop the step) and no DELETE
        # (which would drop the pipeline). What is in the store stays.
        assert (
            publication.publish_pipeline(
                make_pipeline(WITH_UNRESOLVED),
                components=COMPONENTS_WITH_UNRESOLVED,
            )
            is False
        )
        assert store.calls == []

    def test_a_complete_pipeline_is_still_published(self, store):  # noqa: F811
        assert (
            publication.publish_pipeline(
                make_pipeline(VALID), components=COMPONENTS
            )
            is True
        )
        assert [call.method for call in store.calls] == ["PUT"]

    def test_an_invalid_chain_is_still_withdrawn(self, store):  # noqa: F811
        # unchanged, and deliberately different: an incompatible chain is a
        # decision about the pipeline, not a failure to read it
        from tests.test_pipeline_publication import INVALID

        assert (
            publication.publish_pipeline(
                make_pipeline(INVALID), components=COMPONENTS
            )
            is False
        )
        assert [call.method for call in store.calls] == ["DELETE"]

    def test_the_storage_engine_refuses_the_same_way(self, monkeypatch):
        # The editor's saves go through the serializer rather than
        # publish_pipeline, and the framework's sparql store *deletes the graph*
        # when the serializer answers with an empty document
        # (collection-api `storage/sparqlstore.py`). So it has to raise.
        from apps.dishacled.resources import pipeline_components
        from apps.dishacled.serializers.pipeline_serializer import PipelineSerializer

        monkeypatch.setattr(
            pipeline_components,
            "load_pipeline_components",
            lambda pipeline: COMPONENTS_WITH_UNRESOLVED,
        )

        with pytest.raises(IncompletePipeline):
            PipelineSerializer().from_elody_to_sparql(
                make_pipeline(WITH_UNRESOLVED)
            )


class TestTheRunnablePipelineReportsItToo:
    """The RDF-Connect export loses a stage the same way, and says so."""

    def test_the_rdf_connect_serializer_reports_what_it_dropped(self):
        from apps.dishacled.serializers.pipeline_ttl_serializer import (
            PipelineTtlSerializer,
        )

        serializer = PipelineTtlSerializer(base_uri="http://x/p1/")
        ttl = serializer.serialize(
            make_pipeline(WITH_UNRESOLVED), COMPONENTS_WITH_UNRESOLVED
        )
        assert serializer.unrepresented == [UNRESOLVED_ID]
        assert "RmlMapper" not in ttl

    def test_a_complete_pipeline_reports_nothing(self):
        from apps.dishacled.serializers.pipeline_ttl_serializer import (
            PipelineTtlSerializer,
        )

        serializer = PipelineTtlSerializer(base_uri="http://x/p1/")
        serializer.serialize(make_pipeline(VALID), COMPONENTS)
        assert serializer.unrepresented == []

    def test_it_is_reset_between_serializations(self):
        from apps.dishacled.serializers.pipeline_ttl_serializer import (
            PipelineTtlSerializer,
        )

        serializer = PipelineTtlSerializer(base_uri="http://x/p1/")
        serializer.serialize(
            make_pipeline(WITH_UNRESOLVED), COMPONENTS_WITH_UNRESOLVED
        )
        serializer.serialize(make_pipeline(VALID), COMPONENTS)
        assert serializer.unrepresented == []
