from apps.dishacled.object_configurations.entity_configuration import EntityConfiguration
from apps.dishacled.object_configurations.user_configuration import UserConfiguration
from apps.dishacled.object_configurations.tenant_configuration import TenantConfiguration
from apps.dishacled.object_configurations.processor_configuration import ProcessorConfiguration
from apps.dishacled.object_configurations.channel_configuration import ChannelConfiguration
from apps.dishacled.object_configurations.runner_configuration import RunnerConfiguration
from apps.dishacled.object_configurations.pipeline_configuration import PipelineConfiguration
from storage.arangostore import ArangoStorageManager
from storage.memorystore import MemoryStorageManager
from storage.mongostore import MongoStorageManager
from storage.httpstore import HttpStorageManager


OBJECT_CONFIGURATION_MAPPER = {
    "entities": EntityConfiguration,
    "entity": EntityConfiguration,
    "processor": ProcessorConfiguration,
    "pipeline": PipelineConfiguration,
    "channel": ChannelConfiguration,
    "runner": RunnerConfiguration,
    "tenant": TenantConfiguration,
    "user": UserConfiguration,
}

ROUTE_MAPPER = {
    "FilterGenericObjects": "/deprecated/v1/<string:collection>/filter",
    "FilterGenericObjectsV2": "/<string:collection>/filter",
    "GenericObject": "/deprecated/v1/<string:collection>",
    "GenericObjectDetail": "/deprecated/v1/<string:collection>/<string:id>",
    "GenericObjectDetailV2": "/deprecated/v2/<string:collection>/<string:id>",
    "GenericObjectV2": "/deprecated/v2/<string:collection>",
}

STORAGE_MAPPER = {
    "arango": ArangoStorageManager,
    "memory": MemoryStorageManager,
    "mongo": MongoStorageManager,
    "http": HttpStorageManager,
}

COLLECTION_MAPPER = {"tickets": "abstracts"}

FEATURES = {"specs": {"elody": {}}}