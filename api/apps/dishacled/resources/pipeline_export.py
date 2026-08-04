from os import getenv

from apps.dishacled.pipeline.validation import validate_pipeline
from apps.dishacled.resources.base_resource import DishacledBaseResource
from apps.dishacled.resources.pipeline_components import load_pipeline_components
from apps.dishacled.serializers.pipeline_ttl_serializer import (
    PipelineTtlSerializer,
)
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

        processors = load_pipeline_components(pipeline)

        # A pipeline whose shapes do not line up would fail at run time in a
        # way that is far harder to diagnose than here, so an incompatible
        # chain does not leave the system unless it is asked for explicitly.
        report = validate_pipeline(pipeline, processors)
        force = request.args.get("force", "").lower() in ("1", "true", "yes")
        if not report.is_valid and not force:
            return {
                "message": (
                    f"Pipeline {id} cannot be exported: {report.summary} "
                    "Fix the connections, or repeat the request with "
                    "?force=true to export it anyway."
                ),
                **report.to_dict(),
            }, 409

        base_uri = getenv(
            "PIPELINE_EXPORT_BASE_URI", request.url_root
        ).rstrip("/")
        serializer = PipelineTtlSerializer(base_uri=f"{base_uri}/pipelines/{id}/")
        ttl = serializer.serialize(pipeline, processors)
        if not report.is_valid:
            ttl = report.as_turtle_comments() + ttl

        response = make_response(ttl)
        response.headers["Content-Type"] = "text/turtle"
        response.headers[
            "Content-Disposition"
        ] = f"attachment; filename=pipeline-{id}.ttl"
        if not report.is_valid:
            response.headers["X-Pipeline-Validation"] = "invalid"
        return response


api.add_resource(PipelineExport, "/pipelines/<string:id>/export.ttl")
