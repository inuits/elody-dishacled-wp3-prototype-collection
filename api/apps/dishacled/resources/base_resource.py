import os

from policy_factory import get_user_context
from resources.base_resource import BaseResource


class DishacledBaseResource(BaseResource):
    def _get_upload_bucket(self):
        return os.getenv("MINIO_BUCKET")

    def _get_upload_location(self, filename):
        return filename

    # def __get_roles_per_tenant_from_idp(self):
    #     user_context = get_user_context()
    #     roles_per_tenant = {}
    #     for idp_role in user_context.x_tenant.roles:
    #         idp_role_split = idp_role.split("__")
    #         if len(idp_role_split) == 2:
    #             tenant = self.storage.get_item_from_collection_by_id(
    #                 "entities", idp_role_split[0]
    #             )
    #             if not tenant:
    #                 continue
    #             tenant_id = f"tenant:{tenant['_id']}"
    #             role = idp_role_split[1]
    #         else:
    #             tenant_id = "tenant:super"
    #             role = idp_role
    #         if self.storage.get_item_from_collection_by_id("entities", tenant_id):
    #             if tenant_id not in roles_per_tenant:
    #                 roles_per_tenant[tenant_id] = []
    #             roles_per_tenant[tenant_id].append(role)
    #     return roles_per_tenant

    # def _sync_roles_from_idp(self, user, roles_per_tenant):
    #     return super()._sync_roles_from_idp(
    #         user, self.__get_roles_per_tenant_from_idp()
    #     )