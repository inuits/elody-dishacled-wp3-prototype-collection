from os import getenv

from apps.dishacled.resources.base_resource import DishacledBaseResource
from apps.dishacled.serializers.pipeline_ttl_serializer import (
    PipelineTtlSerializer,
)
from configuration import get_storage_mapper
from flask import Blueprint, make_response, request
from flask_restful import Api
from inuits_policy_based_auth import RequestContext
from policy_factory import apply_policies


api_bp = Blueprint("pipeline_export", __name__)
api = Api(api_bp)


class PipelineExport(DishacledBaseResource):
    @apply_policies(RequestContext(request))
    def get(self, id):
        pipeline = self.storage.get_item_from_collection_by_id("entities", id)
        if not pipeline or pipeline.get("type") != "pipeline":
            return {"message": f"Pipeline with id {id} not found"}, 404

        http_storage = get_storage_mapper().get("http")()
        processors = {}
        for relation in pipeline.get("relations", []):
            if relation.get("type") != "hasProcessor":
                continue
            key = relation.get("key")
            if key in processors:
                continue
            processor = http_storage.get_item_from_collection_by_id(
                "githubProcessors", key
            )
            if processor:
                processors[key] = processor

        base_uri = getenv(
            "PIPELINE_EXPORT_BASE_URI", request.url_root
        ).rstrip("/")
        serializer = PipelineTtlSerializer(base_uri=f"{base_uri}/pipelines/{id}/")
        ttl = serializer.serialize(pipeline, processors)

        response = make_response(ttl)
        response.headers["Content-Type"] = "text/turtle"
        response.headers[
            "Content-Disposition"
        ] = f"attachment; filename=pipeline-{id}.ttl"
        return response


api.add_resource(PipelineExport, "/pipelines/<string:id>/export.ttl")
