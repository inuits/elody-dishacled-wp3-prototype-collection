from elody.object_configurations.elody_configuration import (
    ElodyConfiguration,
)
from object_configurations.relation_mirroring import RelationMirroring
from elody.schemas import entity_schema


# keeps has<X> / is<X>For mirrors, so SHACL UI inverse paths read and write from either entity
class EntityConfiguration(RelationMirroring, ElodyConfiguration):
    SCHEMA_TYPE = "elody"
    SCHEMA_VERSION = 1

    def crud(self):
        return super().crud()

    def document_info(self):
        return super().document_info()

    def logging(self, item):
        return super().logging(item)

    def migration(self):
        return super().migration()

    def serialization(self, from_format, to_format):
        return super().serialization(from_format, to_format)

    def validation(self):
        return "schema", entity_schema