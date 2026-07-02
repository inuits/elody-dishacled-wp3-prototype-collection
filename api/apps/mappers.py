from apps.dishacled.object_configurations.entity_configuration import EntityConfiguration
from apps.dishacled.object_configurations.user_configuration import UserConfiguration
from apps.dishacled.object_configurations.tenant_configuration import TenantConfiguration
from apps.dishacled.object_configurations.channel_configuration import ChannelConfiguration
from apps.dishacled.object_configurations.runner_configuration import RunnerConfiguration
from apps.dishacled.object_configurations.pipeline_configuration import PipelineConfiguration
from apps.dishacled.object_configurations.github_processor_configuration import GithubProcessorConfiguration
from storage.arangostore import ArangoStorageManager
from storage.memorystore import MemoryStorageManager
from storage.mongostore import MongoStorageManager
from apps.dishacled.storage.dishacled_httpstore import DishacledHttpStorageManager


OBJECT_CONFIGURATION_MAPPER = {
    "entities": EntityConfiguration,
    "entity": EntityConfiguration,
    "pipeline": PipelineConfiguration,
    "channel": ChannelConfiguration,
    "runner": RunnerConfiguration,
    "jsRunner": RunnerConfiguration,
    "jvmRunner": RunnerConfiguration,
    "pyRunner": RunnerConfiguration,
    "tenant": TenantConfiguration,
    "user": UserConfiguration,
    "githubProcessor": GithubProcessorConfiguration,
    "githubProcessors": GithubProcessorConfiguration,
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
    "http": DishacledHttpStorageManager,
}

COLLECTION_MAPPER = {"tickets": "abstracts"}

FEATURES = {"specs": {"elody": {}}}