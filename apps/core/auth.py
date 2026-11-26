from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken
from rest_framework_simplejwt.settings import api_settings

from apps.accounts.mfa import mfa_required_for
from apps.accounts.models import User

# Endpoints reachable before the second factor is set up / verified.
MFA_EXEMPT_PREFIXES = ("/api/me/", "/api/mfa/", "/api/auth/")


class FastJWTAuthentication(JWTAuthentication):
    """One query per request (user + org). Roles that must use MFA are refused everywhere except the
    enrolment endpoints unless their token was issued after a verified one-time code."""

    def get_user(self, validated_token):
        try:
            user_id = validated_token[api_settings.USER_ID_CLAIM]
        except KeyError:
            raise InvalidToken("Token has no user id")
        try:
            user = User.objects.select_related("org").get(pk=user_id)
        except User.DoesNotExist:
            raise AuthenticationFailed("User not found", code="user_not_found")
        if not user.is_active:
            raise AuthenticationFailed("User inactive", code="user_inactive")
        return user

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None
        user, token = result
        # one session at a time: a token from an older login (or from another device) is refused
        if user.session_key and token.get("sid") != user.session_key:
            raise AuthenticationFailed("این حساب در جای دیگری وارد شده است؛ دوباره وارد شوید.", code="session_replaced")
        self._attach_kartabl(request, user)
        if mfa_required_for(user) and not token.get("mfa") and not request.path.startswith(MFA_EXEMPT_PREFIXES):
            raise AuthenticationFailed("MFA_REQUIRED", code="mfa_required")
        return result


def _attach_kartabl(self, request, user):
    """The school chosen in the kartabl (X-School-Id header or ?school=) narrows this request to that school,
    but only when the user really works there. The system administrator is never narrowed."""
    raw = request.headers.get("X-School-Id") or request.query_params.get("school")
    if not raw or not str(raw).isdigit() or user.role == User.Role.SUPER_ADMIN:
        return
    from apps.accounts.models import OrgUnit
    from apps.core.scope import scope_paths
    school = OrgUnit.objects.filter(pk=int(raw), kind="school").first()
    paths = list(scope_paths(user) or [])
    # a teacher works in every school where he teaches a class (even without a membership there)
    if user.role == User.Role.TEACHER:
        from apps.courses.models import Course
        paths += [p for p in Course.objects.filter(teacher=user).values_list("org__path", flat=True).distinct() if p]
    if school is not None and any(school.path.startswith(p) for p in paths):
        user._active_school_path = school.path


FastJWTAuthentication._attach_kartabl = _attach_kartabl
