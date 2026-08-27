"""Publish Elody's component catalog fragment into the central triple store.

Components are discovered live -- GitHub repositories under the processor topic
plus the interim contract catalog -- so there is no save to hang the catalog
write off, the way a pipeline's write is its publication. Two things trigger it
instead:

* saving a pipeline publishes the components that pipeline names
  (`pipeline/publication.py`), which is what makes its definition resolvable;
* this route publishes **everything Elody can see**, which is what a discovery
  service needs -- a component nobody has used in a pipeline yet is still a
  component the catalog should list.

`GET` reports what a publish would do without writing anything, so the
configuration can be checked against a store before a dry run.
"""

from os import getenv

from apps.dishacled.pipeline import catalog
from apps.dishacled.resources.base_resource import DishacledBaseResource
from apps.dishacled.serializers.component_catalog_serializer import (
    component_iri_of,
)
from flask import Blueprint, request
from flask_restful import Api
from inuits_policy_based_auth import RequestContext
from policy_factory import apply_policies


api_bp = Blueprint("component_catalog", __name__)
api = Api(api_bp)


class ComponentCatalog(DishacledBaseResource):
    @apply_policies(RequestContext(request))
    def get(self):
        components = catalog.discovered_components()
        return {
            "configured": catalog.is_configured(),
            "graph": getenv("CATALOG_GRAPH", "").strip(),
            "components": [
                {
                    "id": document.get("_id"),
                    "iri": str(component_iri_of(document)),
                    "graph": catalog.graph_for_document(document),
                }
                for document in components
            ],
        }, 200

    @apply_policies(RequestContext(request))
    def post(self):
        if not catalog.is_configured():
            return {
                "message": (
                    "No catalog graph is configured; set CATALOG_GRAPH and "
                    "CATALOG_GSP_ENDPOINT (or PIPELINE_GSP_ENDPOINT)."
                )
            }, 409

        components = catalog.discovered_components()
        outcomes = catalog.publish_components(components)
        return {"components": len(components), "outcomes": outcomes}, 200


api.add_resource(ComponentCatalog, "/pipeline-components/catalog")
