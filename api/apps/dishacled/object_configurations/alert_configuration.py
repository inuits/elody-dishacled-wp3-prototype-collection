from os import getenv

from apps.dishacled.serializers.alert_serializer import AlertSerializer
from elody.object_configurations.elody_configuration import (
    ElodyConfiguration,
)


class AlertConfiguration(ElodyConfiguration):
    """Demonstrator alerts, read live from a SPARQL endpoint.

    Nothing is persisted: `storage_type` routes every read to the SPARQL engine
    (collection-api/api/storage/sparqlstore.py), which queries the error graph
    on each request. There is deliberately no creator and no crud hook -- the
    error graph is written by the pipeline, not by Elody.

    The vocabulary below is the one `lblodsh:ErrorShape` declares in the
    contract catalog; the endpoint and graph come from the environment, so
    pointing this at redpencil's feed instead of the local fixture is a
    configuration change. See docs/alert-ingestion.md.
    """

    SCHEMA_TYPE = "sparql"
    SCHEMA_VERSION = 1

    def crud(self):
        crud = {
            "storage_type": "sparql",
            "collection": "alerts",
            "type": "alert",
            "sparql": {
                "endpoint": getenv("ALERT_SPARQL_ENDPOINT", ""),
                "graph": getenv("ALERT_GRAPH", ""),
                "target_class": "http://open-services.net/ns/core#Error",
                "identifier_predicate": "http://mu.semte.ch/vocabularies/core/uuid",
                "sort_predicate": "http://purl.org/dc/terms/created",
            },
        }
        return {**super().crud(), **crud}

    def document_info(self):
        return {
            "object_lists": {"metadata": "key", "relations": "type"},
            # Alerts come from the pipeline, not from a tenant's collection.
            "tenant_id_resolver": lambda _: "",
        }

    def logging(self, flat_document, **kwargs):
        return super().logging(flat_document, **kwargs)

    def migration(self):
        return super().migration()

    def serialization(self, from_format, to_format):
        return getattr(AlertSerializer(), f"from_{from_format}_to_{to_format}")

    def validation(self):
        return super().validation()
