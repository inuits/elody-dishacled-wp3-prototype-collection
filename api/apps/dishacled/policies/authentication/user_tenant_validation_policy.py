from apps.dishacled.resources.base_resource import DishacledBaseResource
from elody.policies.authentication.base_user_tenant_validation_policy import (
    BaseUserTenantValidationPolicy,
)
from flask import Request
from inuits_policy_based_auth import BaseAuthenticationPolicy
from inuits_policy_based_auth.contexts.user_context import UserContext

class UserTenantValidationPolicy(
    BaseAuthenticationPolicy, BaseUserTenantValidationPolicy, DishacledBaseResource
):
    def authenticate(self, user_context, request_context):
        if not user_context.auth_objects.get("token"):
            return self.build_user_context_for_anonymous_user(
                request_context.http_request, user_context
            )

        return self.build_user_context_for_authenticated_user(
            request_context.http_request,
            user_context,
            self.get_user(user_context.id, user_context),
        )

    def get_user(self, id: str, user_context: UserContext) -> dict:  # pyright: ignore
        user = super().get_user(id, user_context, self.storage)
        if not user:
            return self._create_user_from_idp(
                roles_per_tenant=self.__group_roles_from_idp_per_tenant(user_context)
            )
        return self._sync_roles_from_idp(
            user, roles_per_tenant=self.__group_roles_from_idp_per_tenant(user_context)
        )

    def promote_role(self, user_context: UserContext):
        return None

    def build_user_context_for_anonymous_user(self, request, user_context):
        return super().build_user_context_for_anonymous_user(request, user_context)

    def build_user_context_for_authenticated_user(
            self, request, user_context: UserContext, user: dict
    ):
        return super().build_user_context_for_authenticated_user(
            request, user_context, user
        )

    def _determine_tenant_id(self, request, user_context: UserContext):
        return "tenant:super"

    def __group_roles_from_idp_per_tenant(self, user_context: UserContext):
        roles_per_tenant = {"global": ["super_admin"]}
        return roles_per_tenant