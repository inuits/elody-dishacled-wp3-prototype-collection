from inuits_policy_based_auth.authorization.base_authorization_policy import (
    BaseAuthorizationPolicy,
)


class AllAllowedPolicy(BaseAuthorizationPolicy):
    """
    An authorization policy that allows everything.
    """

    def authorize(self, policy_context, user_context, request_context):
        policy_context.access_verdict = True
        return policy_context