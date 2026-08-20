from os import getenv

from apps.dishacled.serializers.pipeline_serializer import PipelineSerializer
from elody.object_configurations.elody_configuration import (
    ElodyConfiguration,
)


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

    There is no crud hook. Publishing used to be one -- the store was a mirror
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
