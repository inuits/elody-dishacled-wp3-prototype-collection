from elody.object_configurations.elody_configuration import (
    ElodyConfiguration,
)
from apps.dishacled.serializers.github_serializer import GithubSerializer


class GithubProcessorConfiguration(ElodyConfiguration):
    SCHEMA_TYPE = "github"
    SCHEMA_VERSION = 1

    def crud(self):
        crud = {
            "storage_type": "http",
            "collection": "githubProcessors",
            "type": "githubProcessor",
        }
        return {**super().crud(), **crud}

    def document_info(self):
        return {
            "object_lists": {"metadata": "key", "relations": "type"},
            "tenant_id_resolver": lambda _: "",
        }

    def logging(self, flat_document, **kwargs):
        return super().logging(flat_document, **kwargs)

    def migration(self):
        return super().migration()

    def serialization(self, from_format, to_format):
        return getattr(GithubSerializer(), f"from_{from_format}_to_{to_format}")

    def validation(self):
        return super().validation()
