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
            self.build_user_context_for_anonymous_user(
                user_context, self.get_user("anonymous_user")
            )
            return user_context

        self.build_user_context_for_authenticated_user(
            request_context.http_request, user_context, self.get_user(user_context.id)
        )
        return user_context

    def get_user(self, id: str) -> dict:
        user = self.storage.get_item_from_collection_by_id("entities", id)
        if not user:
            return self._create_user_from_idp()
        return self._sync_roles_from_idp(user, None)

    def build_user_context_for_authenticated_user(
        self, request: Request, user_context: UserContext, user: dict
    ):
        super().build_user_context_for_authenticated_user(request, user_context, user)
        if request.path != "/tenants":
            user_context.bag["tenant_defining_entity_id"] = (
                self._get_tenant_defining_entity_id(
                    user_context.x_tenant.id, user_context.x_tenant.raw
                )
            )

    def build_user_context_for_anonymous_user(
        self, user_context: UserContext, user: dict
    ):
        super().build_user_context_for_anonymous_user(user_context, user)

    def _determine_tenant_id(self, request, user):
        return "tenant:super"