"""Shared lookup for the resources that reason about a whole pipeline.

Both the export and the validation resource need the same thing: the pipeline
entity plus every component it holds, fetched from the http store (GitHub
repositories and the interim contract catalog alike).
"""

from configuration import get_storage_mapper

from apps.dishacled.pipeline.connections import PROCESSOR_RELATION


def load_pipeline_components(pipeline) -> dict:
    """The `githubProcessor` documents a pipeline's hasProcessor relations point at.

    A component that cannot be fetched is left out rather than raising: a
    single unreachable repository should degrade the report, not deny it.
    """
    http_storage = get_storage_mapper().get("http")()
    components = {}
    for relation in (pipeline or {}).get("relations", []) or []:
        if relation.get("type") != PROCESSOR_RELATION:
            continue
        key = relation.get("key")
        if not key or key in components:
            continue
        try:
            component = http_storage.get_item_from_collection_by_id(
                "githubProcessors", key
            )
        except Exception:
            component = None
        if component:
            components[key] = component
    return components
