"""Entity routes.

Pipelines are addressed through these same routes but are not stored here: the
pipeline configuration declares `storage_type: "sparql"`, so the framework
routes their reads and writes to the triple store. That routing is what
replaced the publish-after-save this module used to do -- writing *is*
publishing now, so there is nothing left to mirror. See
`docs/pipeline-storage.md`.
"""

from apps.dishacled.pipeline.publication import IncompletePipeline
from apps.dishacled.resources.base_resource import DishacledBaseResource
from flask import Blueprint, request
from flask_restful import Api
from inuits_policy_based_auth import RequestContext
from policy_factory import apply_policies
from resources.entity import Entity, EntityDetail
from resources.generic_object import GenericObjectDetail

api_bp = Blueprint("entity", __name__)
api = Api(api_bp)


class DishacledEntity(DishacledBaseResource, Entity):
    @apply_policies(RequestContext(request))
    def get(self, filters=None):
        return super().get(filters=filters)

    @apply_policies(RequestContext(request))
    def post(self):
        return super().post()


def _refused(incomplete: IncompletePipeline):
    """409 rather than 500, and say what could not be described.

    A pipeline whose components cannot all be read is not a bad request and not
    a server fault -- it is a save that must not go through, because the store
    holds the only copy and writing a shorter definition would delete a step.
    The client gets the reason and can retry; the pipeline is untouched.
    """
    return {
        "message": str(incomplete),
        "unrepresented": incomplete.keys,
    }, 409


class DishacledEntityDetail(DishacledBaseResource, EntityDetail):
    @apply_policies(RequestContext(request))
    def get(self, id):
        return super().get(id)

    @apply_policies(RequestContext(request))
    def put(self, id):
        try:
            return super().put(id)
        except IncompletePipeline as incomplete:
            return _refused(incomplete)

    @apply_policies(RequestContext(request))
    def patch(self, id):
        try:
            return super().patch(id)
        except IncompletePipeline as incomplete:
            return _refused(incomplete)

    @apply_policies(RequestContext(request))
    def delete(self, id):
        return super().delete(id)


class DishacledGithubProcessorDetail(DishacledBaseResource, GenericObjectDetail):
    @apply_policies(RequestContext(request))
    def get(self, id):
        item = self.get_object_detail(collection="githubProcessors", id=id)
        if not item:
            return {"message": "Not found"}, 404
        return item


api.add_resource(DishacledEntity, "/entities")
api.add_resource(DishacledEntityDetail, "/entities/<string:id>")
api.add_resource(DishacledGithubProcessorDetail, "/githubProcessors/<string:id>")
