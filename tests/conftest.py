"""Let the suite run outside the collection-api container.

The storage module imports three things from the Elody framework, which is
installed in the container image but not in this repository. When they are
absent, stand-ins are registered so the app's own code can still be exercised.
Inside the container the real modules import successfully and nothing here
takes effect.

The `serialize` stand-in dispatches exactly the way the container does -- via
the object configuration's `from_{a}_to_{b}` convention, which for
githubProcessors resolves to GithubSerializer -- so the code under test takes
the same path either way.
"""

import importlib.util
import sys
import types


def _missing(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is None
    except (ImportError, ValueError):
        return True


def _install_framework_stubs() -> None:
    if not _missing("storage"):
        return

    storage = types.ModuleType("storage")
    storage.__path__ = []
    httpstore = types.ModuleType("storage.httpstore")

    class HttpStorageManager:
        """Base class; DishacledHttpStorageManager overrides everything it uses."""

    httpstore.HttpStorageManager = HttpStorageManager
    storage.httpstore = httpstore

    configuration = types.ModuleType("configuration")

    def get_object_configuration_mapper():  # pragma: no cover - never called
        raise NotImplementedError("no object configuration mapper outside the app")

    configuration.get_object_configuration_mapper = get_object_configuration_mapper

    serialization = types.ModuleType("serialization")
    serialization.__path__ = []
    serialize_module = types.ModuleType("serialization.serialize")

    def serialize(document, type=None, to_format=None, from_format=None, **kwargs):
        from apps.dishacled.serializers.github_serializer import GithubSerializer

        method = getattr(GithubSerializer(), f"from_{from_format}_to_{to_format}")
        return method(document, **kwargs)

    serialize_module.serialize = serialize
    serialization.serialize = serialize_module

    sys.modules.update(
        {
            "storage": storage,
            "storage.httpstore": httpstore,
            "configuration": configuration,
            "serialization": serialization,
            "serialization.serialize": serialize_module,
        }
    )


_install_framework_stubs()


def _install_configuration_stub() -> None:
    """`get_storage_mapper`, for modules that reach for the http store.

    Registered alongside the object configuration mapper of the same module so
    an import of either resolves; both raise when actually called, since the
    suite passes components in explicitly rather than fetching them.
    """
    import configuration

    if not hasattr(configuration, "get_storage_mapper"):

        def get_storage_mapper():  # pragma: no cover - never called
            raise NotImplementedError("no storage mapper outside the app")

        configuration.get_storage_mapper = get_storage_mapper


def _install_elody_stub() -> None:
    """A stand-in for the object configuration base class.

    `PipelineConfiguration` derives from the framework's `ElodyConfiguration`,
    which lives in the container image. The stub carries only what the subclass
    calls through to -- the crud hooks and the pass-through accessors -- so the
    client's own hook logic is exercised on the host as well.
    """
    if not _missing("elody"):
        return

    elody = types.ModuleType("elody")
    elody.__path__ = []
    object_configurations = types.ModuleType("elody.object_configurations")
    object_configurations.__path__ = []
    module = types.ModuleType("elody.object_configurations.elody_configuration")

    class ElodyConfiguration:
        SCHEMA_TYPE = "elody"
        SCHEMA_VERSION = 1

        def crud(self):
            return {
                "post_crud_hook": lambda **kwargs: self._post_crud_hook(**kwargs),
                "pre_crud_hook": lambda **kwargs: self._pre_crud_hook(**kwargs),
            }

        def document_info(self):
            return {"object_lists": {"metadata": "key", "relations": "type"}}

        def logging(self, flat_document, **kwargs):
            return {}

        def migration(self):
            return {}

        def serialization(self, from_format, to_format):
            return lambda document, **kwargs: document

        def validation(self):
            return {}

        def _post_crud_hook(self, **kwargs):
            pass

        def _pre_crud_hook(self, *, crud, document={}, **kwargs):
            return document

    module.ElodyConfiguration = ElodyConfiguration
    elody.object_configurations = object_configurations
    object_configurations.elody_configuration = module

    sys.modules.update(
        {
            "elody": elody,
            "elody.object_configurations": object_configurations,
            "elody.object_configurations.elody_configuration": module,
        }
    )


_install_configuration_stub()
_install_elody_stub()
