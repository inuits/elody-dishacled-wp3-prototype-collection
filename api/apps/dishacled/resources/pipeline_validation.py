"""Report whether a pipeline's producer->consumer links are compatible.

The report is what the pipeline editor shows and what the export resource
consults before letting a pipeline out of the system.
"""

from apps.dishacled.pipeline.validation import validate_pipeline
from apps.dishacled.resources.base_resource import DishacledBaseResource
from apps.dishacled.resources.pipeline_components import load_pipeline_components
from flask import Blueprint, request
from flask_restful import Api
from inuits_policy_based_auth import RequestContext
from policy_factory import apply_policies


api_bp = Blueprint("pipeline_validation", __name__)
api = Api(api_bp)


class PipelineValidation(DishacledBaseResource):
    @apply_policies(RequestContext(request))
    def get(self, id):
        pipeline = self.storage.get_item_from_collection_by_id("entities", id)
        if not pipeline or pipeline.get("type") != "pipeline":
            return {"message": f"Pipeline with id {id} not found"}, 404

        components = load_pipeline_components(pipeline)
        return validate_pipeline(pipeline, components).to_dict(), 200


api.add_resource(PipelineValidation, "/pipelines/<string:id>/validation")
