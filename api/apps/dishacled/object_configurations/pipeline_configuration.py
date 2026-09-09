from logging import getLogger
from os import getenv

from apps.dishacled.pipeline.connections import (
    assign_step_instances,
    autoconnect_new_relations,
    split_component_key,
)
from apps.dishacled.serializers.pipeline_serializer import PipelineSerializer
from elody.object_configurations.elody_configuration import (
    ElodyConfiguration,
)


log = getLogger(__name__)


class PipelineConfiguration(ElodyConfiguration):
    """Pipelines, kept in the central triple store rather than in Mongo.

    A pipeline is the one thing Elody and the toolchain both hold an opinion
    about, so it has one home: the store. `storage_type: "sparql"` routes every
    read and write for this type to the graph-scoped SPARQL engine
    (collection-api/api/storage/sparqlstore.py), which addresses one pipeline as
    one named graph and hands it to `PipelineSerializer` both ways. Nothing is
    persisted on our side -- Koen's CLI and Elody read the same triples, and
    editing in one is visible in the other.

    `collection` is `pipelines`, not `entities`, and that is what makes the
    routing work: a route names a collection, `entities` holds every type a
    client has, so an externally stored type is recognised by declaring a
    collection of its own. The routes stay `/entities/...`; the engine is
    addressed as `pipelines`. See `docs/pipeline-storage.md`.

    The one crud hook wires new components up: a component added to the
    pipeline whose input shape matches the output of a component already
    there gets its connection written on the spot
    (`autoconnect_new_relations`), so picking from the shape-scoped picker is
    the whole gesture. Publishing used to be a hook too -- the store was a mirror
    of Mongo, kept in step after the fact -- and now the write *is* the
    publication, so there is nothing left to mirror. The chain validation that
    lived in the pre-hook moved with it: a verdict is Elody's reading of a
    shared definition, so it is computed on demand (`GET
    /pipelines/<id>/validation`) instead of being stamped onto stored data.
    """

    # A pipeline *document* is an ordinary elody document -- metadata and
    # relations, the same shape the form saves -- and that is what SCHEMA_TYPE
    # names: the framework converts incoming content to it before validating.
    # The turtle is the *storage* format, which the engine asks the serializer
    # for by name; the two are deliberately not the same thing.
    SCHEMA_TYPE = "elody"
    SCHEMA_VERSION = 1

    def crud(self):
        crud = {
            "storage_type": "sparql",
            "collection": "pipelines",
            "type": "pipeline",
            # Stored under `pipelines`, reached through the `/entities` routes.
            # A blind `GET /entities/<id>` has no type to route on, so this is
            # what tells the framework to ask the store when the database has
            # nothing.
            "routed_through": "entities",
            "sparql": {
                # The read endpoint and the write endpoint of one store: a
                # query endpoint answers CONSTRUCT, and the Graph Store
                # Protocol endpoint is what a whole-graph replace addresses.
                "endpoint": getenv("SPARQL_ENDPOINT", ""),
                "gsp_endpoint": getenv("PIPELINE_GSP_ENDPOINT", ""),
                "graph": getenv("PIPELINE_GRAPH", ""),
                "graph_per_item": True,
                "user": getenv("PIPELINE_STORE_USER", ""),
                "password": getenv("PIPELINE_STORE_PASSWORD", ""),
                "target_class": "https://w3id.org/toolchain#PipelineDefinition",
                "identifier_predicate": "http://purl.org/dc/terms/identifier",
            },
        }
        return {**super().crud(), **crud}

    def _pre_crud_hook(self, *, crud, unpatched_document={}, **kwargs):
        document = super()._pre_crud_hook(
            crud=crud, unpatched_document=unpatched_document, **kwargs
        )
        if document and crud in ("create", "update"):
            # A step of a repeated component gets an identity of its own before
            # anything downstream sees the document. Not a convenience: two
            # uses of one component are identical relations until one is
            # configured, and a store keeps one of two identical entries --
            # the second step was lost on the way in. This is the layer that
            # knows a relation is a step, so it is the layer that says which.
            try:
                assign_step_instances(
                    document.get("relations") or [], self.__components_of(document)
                )
            except Exception:
                log.exception("Step identities not assigned")
            # never fail the save over a convenience: an unreachable component
            # catalog just means the user connects by hand, as before
            try:
                autoconnect_new_relations(
                    document.get("relations") or [],
                    (unpatched_document or {}).get("relations") or [],
                    self.__ports_of,
                )
            except Exception:
                log.exception("Auto-connect skipped")
        return document

    def identify_sub_items(self, *, sub_item, existing, content):
        """What tells two of a pipeline's relations apart, for the store.

        The engine treats equal entries as one entry, which is right for a
        retried add and wrong for a step: a step is a *use* of a component, so
        two uses of one component are identical relations until one of them is
        configured. The framework adds a new document's relations in a write of
        their own, which runs no crud hook, so this is where a step being added
        gets the identity `_pre_crud_hook` gives it on a save.

        Existing entries are handed over too, and are identified along with the
        new ones: whichever of them is repeated has to say which step it is,
        and the answer has to be the same for all of them.
        """
        if sub_item != "relations":
            return content
        combined = [*(existing or []), *(content or [])]
        try:
            assign_step_instances(
                combined, self.__components_of({"relations": combined})
            )
        except Exception:
            log.exception("Step identities not assigned")
        return combined[len(existing or []) :]

    @staticmethod
    def __components_of(document):
        """The component behind each `hasProcessor` relation, by relation key.

        Addressed by *component* id rather than by the relation's key, because
        a step-keyed lookup answers with the step's own name ("... (step-2)",
        `storage/dishacled_httpstore.py::_as_step`) and a step id derived from
        that would carry the previous one inside it.
        """
        from configuration import get_storage_mapper

        storage = get_storage_mapper().get("http")()
        components = {}
        for relation in document.get("relations") or []:
            key = relation.get("key")
            if not key or relation.get("type") != "hasProcessor":
                continue
            component_id, _ = split_component_key(key)
            try:
                component = storage.get_item_from_collection_by_id(
                    "githubProcessors", component_id or key
                )
            except Exception:
                component = None
            if component:
                components[key] = component
        return components

    @staticmethod
    def __ports_of(relation_key):
        """One component's ports, from the same source the suggestions read."""
        from configuration import get_storage_mapper

        component_id, _ = split_component_key(relation_key or "")
        storage = get_storage_mapper().get("http")()
        document = storage.get_item_from_collection_by_id(
            "githubProcessors", component_id or relation_key
        )
        return ((document or {}).get("data") or {}).get("ports") or []

    def document_info(self):
        return {
            "object_lists": {"metadata": "key", "relations": "type"},
            # The store is not partitioned by tenant, and giving the impression
            # that it is would be worse than not claiming it: a definition is
            # shared with the toolchain services, which have no tenant at all.
            # Multi-tenant SPARQL is explicitly out of scope for the
            # demonstrator.
            "tenant_id_resolver": lambda _: "",
        }

    def logging(self, item, **kwargs):
        return super().logging(item, **kwargs)

    def migration(self):
        return super().migration()

    def serialization(self, from_format, to_format):
        return getattr(PipelineSerializer(), f"from_{from_format}_to_{to_format}")

    def validation(self):
        return super().validation()
