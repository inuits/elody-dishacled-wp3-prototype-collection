from os import getenv

from apps.dishacled.pipeline.publication import pipeline_base_uri
from apps.dishacled.pipeline.validation import validate_pipeline
from apps.dishacled.resources.base_resource import DishacledBaseResource
from apps.dishacled.resources.pipeline_components import (
    load_pipeline,
    load_pipeline_components,
)
from apps.dishacled.serializers.pipeline_definition_serializer import (
    PipelineDefinitionSerializer,
)
from apps.dishacled.serializers.pipeline_ttl_serializer import (
    PipelineTtlSerializer,
)
from flask import Blueprint, make_response, request
from flask_restful import Api
from inuits_policy_based_auth import RequestContext
from policy_factory import apply_policies


api_bp = Blueprint("pipeline_export", __name__)
api = Api(api_bp)


def _is_true(value):
    return str(value).lower() in ("1", "true", "yes")


class _PipelineExportBase(DishacledBaseResource):
    """Shared plumbing for the two turtle exports of one pipeline.

    Both are gated on the same chain validation: a pipeline whose shapes do
    not line up would fail at run time in a way that is far harder to diagnose
    than here, so an incompatible chain does not leave the system unless it is
    asked for explicitly.
    """

    filename_prefix = "pipeline"

    def _export(self, id):
        self.unrepresented: list = []
        pipeline = load_pipeline(self, id)
        if not pipeline:
            return {"message": f"Pipeline with id {id} not found"}, 404

        processors = load_pipeline_components(pipeline)

        report = validate_pipeline(pipeline, processors)
        if not report.is_valid and not _is_true(request.args.get("force", "")):
            return {
                "message": (
                    f"Pipeline {id} cannot be exported: {report.summary} "
                    "Fix the connections, or repeat the request with "
                    "?force=true to export it anyway."
                ),
                **report.to_dict(),
            }, 409

        # the same prefix the published graph is built on, so a downloaded
        # definition and the one in the store name the same things
        ttl = self.serialize(pipeline, processors, base_uri=pipeline_base_uri(id))
        if not report.is_valid:
            ttl = report.as_turtle_comments() + ttl

        response = make_response(ttl)
        response.headers["Content-Type"] = "text/turtle"
        if self.unrepresented:
            # a download that quietly leaves a step out is a pipeline someone
            # will deploy and wonder about
            response.headers["X-Pipeline-Unrepresented"] = ",".join(
                self.unrepresented
            )
        response.headers["Content-Disposition"] = (
            f"attachment; filename={self.filename_prefix}-{id}.ttl"
        )
        if not report.is_valid:
            response.headers["X-Pipeline-Validation"] = "invalid"
        return response


class PipelineExport(_PipelineExportBase):
    """The runnable RDF-Connect pipeline.ttl."""

    filename_prefix = "pipeline"

    def serialize(self, pipeline, processors, base_uri):
        serializer = PipelineTtlSerializer(base_uri=base_uri)
        ttl = serializer.serialize(pipeline, processors)
        self.unrepresented = serializer.unrepresented
        return ttl

    @apply_policies(RequestContext(request))
    def get(self, id):
        return self._export(id)


class PipelineDefinitionExport(_PipelineExportBase):
    """The toolchain `tcs:PipelineDefinition` the pipeline generator compiles.

    `?catalog=false` drops the catalog fragment, for the case where every
    component is already declared in a catalog the reader has -- the toolchain's
    own, or the catalog graph Elody now publishes into
    (`pipeline/catalog.py`). Re-declaring them would then merge two
    descriptions of the same component.

    Which way round the default goes is `DEFINITION_INCLUDE_CATALOG`, because
    it depends on the reader rather than on the pipeline: an environment whose
    consumers all read the shared catalog graph wants the definition to name
    components and nothing more, and one that hands a file to someone wants the
    file to stand alone. It still defaults to shipping the fragment -- see
    `docs/component-catalog.md` for what has to be true before that flips.
    """

    filename_prefix = "pipeline-definition"

    def serialize(self, pipeline, processors, base_uri):
        catalog_param = request.args.get("catalog")
        if catalog_param is None:
            include_catalog = _is_true(
                getenv("DEFINITION_INCLUDE_CATALOG", "true")
            )
        else:
            include_catalog = _is_true(catalog_param)
        serializer = PipelineDefinitionSerializer(
            base_uri=base_uri, include_catalog=include_catalog
        )
        ttl = serializer.serialize(pipeline, processors)
        self.unrepresented = serializer.unrepresented
        return ttl

    @apply_policies(RequestContext(request))
    def get(self, id):
        return self._export(id)


api.add_resource(PipelineExport, "/pipelines/<string:id>/export.ttl")
api.add_resource(
    PipelineDefinitionExport, "/pipelines/<string:id>/definition.ttl"
)
