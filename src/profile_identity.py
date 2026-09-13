"""Small identity boundary for profile-scoped CampusFlow state.

The planner only needs an opaque ``user_id`` and whether persistence is
enabled.  A student identifier is handled by the local profile directory and
is deliberately absent from this object, model prompts, and business facts.
"""

import re
import uuid
from dataclasses import dataclass
from typing import Optional


CURRENT_USER_CONTEXT_KEY = "campusflow_current_user_context"
AUTHENTICATION_CONTEXT_KEY = "campusflow_authentication_context"
PROFILE_ENABLED_DRAFT_KEY = "campusflow_profile_enabled_draft"
STUDENT_IDENTIFIER_DRAFT_KEY = "campusflow_student_identifier_draft"
PROFILE_IMPORT_LEGACY_DRAFT_KEY = "campusflow_profile_import_legacy_draft"
PROFILE_SWITCH_STATUS_KEY = "campusflow_profile_switch_status"
LEGACY_USER_ID = "legacy-local"

_STUDENT_IDENTIFIER = re.compile(r"^[0-9]{4,32}$")


@dataclass(frozen=True)
class ProfileIdentity:
    user_id: str
    persistent: bool
    kind: str

    def __post_init__(self):
        if not isinstance(self.user_id, str) or not self.user_id.strip():
            raise ValueError("user_id required")
        if self.kind not in ("anonymous", "legacy", "student"):
            raise ValueError("profile identity kind invalid")
        if self.persistent != (self.kind != "anonymous"):
            raise ValueError("profile persistence flag invalid")


@dataclass(frozen=True)
class AuthenticationContext:
    """Authentication boundary consumed before entering CampusFlow business code.

    It intentionally contains neither credentials nor a student identifier.
    A future trusted adapter resolves provider claims to ``internal_user_id``;
    planners continue to receive only the resulting ``ProfileIdentity``.
    """

    authenticated: bool
    internal_user_id: str
    identity_provider: Optional[str]
    identity_link_id: Optional[str]
    persistent: bool

    def __post_init__(self):
        if not isinstance(self.internal_user_id, str) or not self.internal_user_id.strip():
            raise ValueError("internal_user_id required")
        if self.authenticated:
            if not self.persistent:
                raise ValueError("authenticated identity must be persistent")
            if not isinstance(self.identity_provider, str) or not self.identity_provider.strip():
                raise ValueError("authenticated identity provider required")
            if not isinstance(self.identity_link_id, str) or not self.identity_link_id.strip():
                raise ValueError("authenticated identity link required")
        else:
            if self.identity_provider not in (None, "local_profile_selector"):
                raise ValueError("unauthenticated identity provider invalid")
            if self.identity_link_id is not None:
                raise ValueError("unauthenticated identity link invalid")

    def profile_identity(self):
        kind = (
            "legacy" if self.internal_user_id == LEGACY_USER_ID
            else "student" if self.persistent
            else "anonymous"
        )
        return ProfileIdentity(self.internal_user_id, self.persistent, kind)


def new_anonymous_identity():
    return ProfileIdentity("anon-" + uuid.uuid4().hex, False, "anonymous")


def legacy_identity():
    return ProfileIdentity(LEGACY_USER_ID, True, "legacy")


def authenticated_context(internal_user_id, identity_provider, identity_link_id):
    """Create a context after a trusted adapter has verified external claims."""
    return AuthenticationContext(
        True, str(internal_user_id or "").strip(),
        str(identity_provider or "").strip(),
        str(identity_link_id or "").strip(), True,
    )


def authentication_context_for_profile(identity):
    if not isinstance(identity, ProfileIdentity):
        raise TypeError("profile identity required")
    provider = "local_profile_selector" if identity.persistent else None
    return AuthenticationContext(
        False, identity.user_id, provider, None, identity.persistent
    )


def activate_authentication_context(store, context):
    """Publish a verified identity without leaking provider details downstream."""
    if not isinstance(context, AuthenticationContext):
        raise TypeError("authentication context required")
    store[AUTHENTICATION_CONTEXT_KEY] = context
    store[CURRENT_USER_CONTEXT_KEY] = context.profile_identity()
    return store[CURRENT_USER_CONTEXT_KEY]


def normalize_student_identifier(value):
    """Validate a local profile label without pretending it authenticates."""
    normalized = str(value or "").strip()
    if not _STUDENT_IDENTIFIER.fullmatch(normalized):
        raise ValueError("学号应为4至32位数字；这里只用于区分档案，不验证身份。")
    return normalized


def current_identity(store):
    authentication = store.get(AUTHENTICATION_CONTEXT_KEY)
    if isinstance(authentication, AuthenticationContext) and authentication.authenticated:
        identity = authentication.profile_identity()
        store[CURRENT_USER_CONTEXT_KEY] = identity
        return identity
    value = store.get(CURRENT_USER_CONTEXT_KEY)
    if isinstance(value, ProfileIdentity):
        return value
    value = new_anonymous_identity()
    store[CURRENT_USER_CONTEXT_KEY] = value
    return value


def current_authentication_context(store):
    value = store.get(AUTHENTICATION_CONTEXT_KEY)
    identity = current_identity(store)
    if isinstance(value, AuthenticationContext):
        if value.internal_user_id == identity.user_id:
            return value
    value = authentication_context_for_profile(identity)
    store[AUTHENTICATION_CONTEXT_KEY] = value
    return value
