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
