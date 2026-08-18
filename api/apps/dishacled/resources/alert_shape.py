"""The alert shape, as the UI consumes it and as an inspectable artifact.

The detail view for an alert is generated from `lblodsh:ErrorShape` rather than
hand-written per field, so the field set has to be fetchable. `/shapes/alert` is
what the graphql service reads; `/shapes/alert/shui.ttl` is the SHACL 1.2 UI
Turtle, giving alerts the same inspectable artifact processors already have via
`/processors/<id>/shui.ttl`.

These live under `/shapes/` rather than `/alerts/` deliberately: they describe
the shape, not an alert, and it keeps them from competing with
`/alerts/<string:id>` for a path segment.
"""

from apps.dishacled.resources.base_resource import DishacledBaseResource
from apps.dishacled.shacl.alert_shape import alert_form_fields, error_shape_ttl
from apps.dishacled.shacl.shui import ShuiFormBuilder
from flask import Blueprint, make_response, request
from flask_restful import Api
from inuits_policy_based_auth import RequestContext
from policy_factory import apply_policies

api_bp = Blueprint("alert_shape", __name__)
api = Api(api_bp)


class AlertShapeFields(DishacledBaseResource):
    """The Elody form fields derived from ErrorShape, in display order.

    GET /shapes/alert -> {"fields": {...}, "order": [...]}
    """

    @apply_policies(RequestContext(request))
    def get(self):
        fields = alert_form_fields()
        return {"fields": fields, "order": list(fields)}


class AlertShuiShape(DishacledBaseResource):
    """The generated SHACL 1.2 UI shape for alerts.

    GET /shapes/alert/shui.ttl            -> generated shui: shape (Turtle)
    GET /shapes/alert/shui.ttl?download=1 -> same, as a file download
    """

    @apply_policies(RequestContext(request))
    def get(self):
        shui_ttl = ShuiFormBuilder(error_shape_ttl()).to_ttl()

        response = make_response(shui_ttl)
        response.headers["Content-Type"] = "text/turtle; charset=utf-8"
        disposition = "attachment" if request.args.get("download") else "inline"
        response.headers["Content-Disposition"] = (
            f"{disposition}; filename=shui-alert.ttl"
        )
        return response


api.add_resource(AlertShapeFields, "/shapes/alert")
api.add_resource(AlertShuiShape, "/shapes/alert/shui.ttl")
