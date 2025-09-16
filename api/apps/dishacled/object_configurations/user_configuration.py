from elody.object_configurations.elody_configuration import (
    ElodyConfiguration,
)
from uuid import uuid5, NAMESPACE_OID


class UserConfiguration(ElodyConfiguration):
    SCHEMA_TYPE = "elody"
    SCHEMA_VERSION = 1

    def crud(self):
        crud = {"collection": "entities"}
        return {**super().crud(), **crud}

    def document_info(self):
        return super().document_info()

    def logging(self, flat_document, **kwargs):
        return super().logging(flat_document, **kwargs)

    def migration(self):
        return super().migration()

    def serialization(self, from_format, to_format):
        return super().serialization(from_format, to_format)

    def validation(self):
        return super().validation()

    def _creator(self, user_context, **_):  # pyright: ignore
        return super()._creator(
            user_context,
            document_defaults={
                "_id": str(uuid5(NAMESPACE_OID, user_context.id)),
                "identifiers": list(
                    {
                        user_context.email,
                        user_context.id,
                        user_context.preferred_username,
                    }
                ),
                "metadata": [
                    {"key": "email", "value": user_context.email},
                    {
                        "key": "name",
                        "value": user_context.auth_objects.get("token").get("name"),
                    },
                    {
                        "key": "preferred_username",
                        "value": user_context.preferred_username,
                    },
                ],
                "relations": [],
                "type": "user",
            },
        )
