from apps.roles.admin import ADMIN
from apps.roles.anonymous import ANONYMOUS
from apps.roles.user import USER

PERMISSIONS = {
    **ADMIN,
    **ANONYMOUS,
    **USER,
}
