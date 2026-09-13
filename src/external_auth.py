"""Trusted external-identity input for CampusFlow authentication adapters.

This module does not read browser headers or validate provider tokens.  A
deployment-specific adapter must first authenticate the request, then create a
``VerifiedExternalIdentity``.  Keeping that boundary explicit prevents URL or
form values from being mistaken for authenticated identities.
"""

import re
from dataclasses import dataclass


_PROVIDER = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")


@dataclass(frozen=True)
class VerifiedExternalIdentity:
    provider: str
    subject: str

    def __post_init__(self):
        if not isinstance(self.provider, str) or not _PROVIDER.fullmatch(self.provider):
            raise ValueError("external identity provider invalid")
        if not isinstance(self.subject, str) or not self.subject:
            raise ValueError("external identity subject required")
        if self.subject != self.subject.strip() or len(self.subject) > 512:
            raise ValueError("external identity subject invalid")
        if any(ord(char) < 32 or ord(char) == 127 for char in self.subject):
            raise ValueError("external identity subject invalid")


def verified_external_identity(provider, subject):
    """Construct an assertion only after a trusted provider adapter verifies it."""
    return VerifiedExternalIdentity(str(provider or "").strip().lower(), subject)


def streamlit_oidc_identity(user_info, provider):
    """Adapt Streamlit's already-verified OIDC user claims.

    ``user_info`` must be the server-side ``st.user`` object from a Streamlit
    version with the public OIDC API.  The adapter deliberately keeps only the
    stable ``sub`` claim and never returns or persists provider tokens.
    """
    if user_info is None:
        return None
    try:
        logged_in = bool(user_info.is_logged_in)
    except (AttributeError, KeyError, TypeError):
        try:
            logged_in = bool(user_info.get("is_logged_in", False))
        except (AttributeError, TypeError):
            logged_in = False
    if not logged_in:
        return None
    try:
        subject = user_info.get("sub")
    except AttributeError:
        subject = getattr(user_info, "sub", None)
    if not isinstance(subject, str) or not subject:
        raise ValueError("authenticated OIDC identity has no stable subject")
    return verified_external_identity(provider, subject)
