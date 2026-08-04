from elody.object_configurations.elody_configuration import (
    ElodyConfiguration,
)

from apps.dishacled.pipeline.validation import apply_validation_state


class PipelineConfiguration(ElodyConfiguration):
    SCHEMA_TYPE = "elody"
    SCHEMA_VERSION = 1

    def crud(self):
        crud = {
            "collection": "entities",
            "pre_crud_hook": lambda **kwargs: self._pre_crud_hook(**kwargs),
        }
        return {**super().crud(), **crud}

    def document_info(self):
        return super().document_info()

    def logging(self, item):
        return super().logging(item)

    def migration(self):
        return super().migration()

    def serialization(self, from_format, to_format):
        return super().serialization(from_format, to_format)

    def validation(self):
        return super().validation()

    def _pre_crud_hook(self, *, crud, document={}, **kwargs):
        """Re-validate the chain whenever the pipeline changes.

        Connections are stored as metadata on the pipeline's hasProcessor
        relations, so every rewiring passes through here. Stamping the verdict
        back onto those same relations is what puts it in front of the user:
        the processor list renders a relation's metadata, so an incompatible
        link shows its reason next to the producer that caused it, with no
        separate validation step to remember to run.

        Validation must never be the reason a save fails -- it needs the
        component documents, which come from GitHub for real processors -- so
        any failure here leaves the document exactly as it was.
        """
        document = super()._pre_crud_hook(crud=crud, document=document, **kwargs)
        if crud == "delete" or not document:
            return document
        try:
            from apps.dishacled.resources.pipeline_components import (
                load_pipeline_components,
            )

            return apply_validation_state(
                document, load_pipeline_components(document)
            )
        except Exception:
            return document
