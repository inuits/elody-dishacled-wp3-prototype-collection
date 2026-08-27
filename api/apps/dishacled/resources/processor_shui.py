from apps.dishacled.resources.base_resource import DishacledBaseResource
from apps.dishacled.shacl.shui import ShuiFormBuilder
from configuration import get_storage_mapper
from flask import Blueprint, make_response, request
from flask_restful import Api
from inuits_policy_based_auth import RequestContext
from policy_factory import apply_policies


api_bp = Blueprint("processor_shui", __name__)
api = Api(api_bp)


class ProcessorShuiShape(DishacledBaseResource):
    """Return the SHACL 1.2 UI shape derived from a processor's SHACL.

    The processor declares its config as SHACL; ShuiFormBuilder derives a
    standard `shui:`-annotated shape (the portable UI definition) from it. This
    endpoint exposes that generated shape so it can be inspected or presented.

    GET /processors/<id>/shui.ttl            -> generated shui: shape (Turtle)
    GET /processors/<id>/shui.ttl?download=1 -> same, as a file download
    """

    @apply_policies(RequestContext(request))
    def get(self, id):
        http_storage = get_storage_mapper().get("http")()
        processor = http_storage.get_item_from_collection_by_id(
            "githubProcessors", id
        )
        if not processor:
            return {"message": f"Processor with id {id} not found"}, 404

        data = processor.get("data") or {}
        raw_ttl = data.get("rawTtl")
        if not raw_ttl:
            return {
                "message": f"Processor {id} has no SHACL shape to derive a UI from"
            }, 404

        # the class this component is, so a repository declaring several
        # processors presents the UI of the one that was asked for
        shui_ttl = ShuiFormBuilder(raw_ttl, data.get("componentIri")).to_ttl()

        response = make_response(shui_ttl)
        response.headers["Content-Type"] = "text/turtle; charset=utf-8"
        disposition = "attachment" if request.args.get("download") else "inline"
        response.headers["Content-Disposition"] = (
            f"{disposition}; filename=shui-{id}.ttl"
        )
        return response


api.add_resource(ProcessorShuiShape, "/processors/<string:id>/shui.ttl")
