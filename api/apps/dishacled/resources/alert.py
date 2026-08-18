"""Demonstrator alerts, served from the SPARQL error graph.

All three routes go through the ordinary entity path: `AlertConfiguration`
declares `storage_type: "sparql"`, so the resources below route to the SPARQL
engine without knowing anything about it.

`/alerts/filter` is not redundant with `/alerts`: the filter path is the one
that forwards skip/limit to the storage engine, so it is what A2 should list
through. See docs/alert-ingestion.md.
"""

from apps.dishacled.resources.base_resource import DishacledBaseResource
from flask import Blueprint, request
from flask_restful import Api
from inuits_policy_based_auth import RequestContext
from policy_factory import apply_policies
from resources.filter import FilterGenericObjectsV2
from resources.generic_object import GenericObject, GenericObjectDetail

api_bp = Blueprint("alert", __name__)
api = Api(api_bp)


class DishacledAlert(DishacledBaseResource, GenericObject):
    @apply_policies(RequestContext(request))
    def get(self):
        return super().get(collection="alerts")


class DishacledAlertDetail(DishacledBaseResource, GenericObjectDetail):
    @apply_policies(RequestContext(request))
    def get(self, id):
        item = self.get_object_detail(collection="alerts", id=id)
        if not item:
            return {"message": f"Alert with id {id} does not exist"}, 404
        return item


class DishacledAlertFilter(DishacledBaseResource, FilterGenericObjectsV2):
    @apply_policies(RequestContext(request))
    def post(self):
        return super().post(collection="alerts")


api.add_resource(DishacledAlert, "/alerts")
api.add_resource(DishacledAlertFilter, "/alerts/filter")
api.add_resource(DishacledAlertDetail, "/alerts/<string:id>")
