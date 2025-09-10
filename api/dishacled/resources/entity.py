from apps.dishacled.resources.base_resource import DishacledBaseResource
from flask import Blueprint, request
from flask_restful import Api
from inuits_policy_based_auth import RequestContext
from policy_factory import apply_policies
from resources.entity import Entity, EntityMediafiles, EntityDetail

api_bp = Blueprint("entity", __name__)
api = Api(api_bp)


class DishacledEntity(DishacledBaseResource, Entity):
    @apply_policies(RequestContext(request))
    def get(self, filters=None):
        return super().get(filters=filters)

    @apply_policies(RequestContext(request))
    def post(self):
        return super().post()


class DishacledEntityDetail(DishacledBaseResource, EntityDetail):
    @apply_policies(RequestContext(request))
    def get(self, id):
        return super().get(id)

    @apply_policies(RequestContext(request))
    def put(self, id):
        return super().put(id)

    @apply_policies(RequestContext(request))
    def patch(self, id):
        return super().patch(id)

    @apply_policies(RequestContext(request))
    def delete(self, id):
        return super().delete(id)


api.add_resource(DishacledEntity, "/entities")
api.add_resource(DishacledEntityDetail, "/entities/<string:id>")