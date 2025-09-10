from apps.dishacled-wp3-prototype.object_configurations.entity_configuration import EntityConfiguration
from apps.dishacled-wp3-prototype.object_configurations.user_configuration import UserConfiguration
from apps.dishacled-wp3-prototype.object_configurations.tenant_configuration import TenantConfiguration
from storage.arangostore import ArangoStorageManager
from storage.memorystore import MemoryStorageManager
from storage.mongostore import MongoStorageManager
from storage.httpstore import HttpStorageManager


OBJECT_CONFIGURATION_MAPPER = {
    "entities": EntityConfiguration,
    "entity": EntityConfiguration,
    "tenant": TenantConfiguration,
    "user": UserConfiguration,
}

ROUTE_MAPPER = {
    "FilterEntities": "/entities/filter_deprecated",
    "FilterMediafiles": "/mediafiles/filter_deprecated",
    "FilterEntitiesV2": "/entities/filter",
    "FilterMediafilesV2": "/mediafiles/filter",
    "FilterGenericObjectsV2": "/<string:collection>/filter",
    "FilterGenericObjects": "/<string:collection>/filter_deprecated",
}

STORAGE_MAPPER = {
    "arango": ArangoStorageManager,
    "memory": MemoryStorageManager,
    "mongo": MongoStorageManager,
    "http": HttpStorageManager,
}

COLLECTION_MAPPER = {"tickets": "abstracts"}