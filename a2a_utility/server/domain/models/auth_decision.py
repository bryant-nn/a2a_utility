"""AuthDecision — the three answers the gate can give.

Modelled as data rather than exceptions because the caller has to *do*
something different with each, and the difference is visible to the A2A
caller as a task state:

    Allow        -> run the handler
    AuthRequired -> task ends AUTH_REQUIRED — "I don't know who you are;
                    send credentials and try again". Recoverable: the caller
                    can retry the same task with a token.
    Reject       -> task ends REJECTED — "I know who you are, and no".
                    Not recoverable by retrying.

The distinction matters. Collapsing both failures into one state would tell a
caller with an expired token to give up, and a caller without permission to
keep retrying.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union

from .user_context import UserContext


@dataclass(frozen=True)
class Allow:
    """The caller is authenticated and entitled. Carries the identity the
    handler will run under."""

    user: UserContext


@dataclass(frozen=True)
class AuthRequired:
    """No usable credential was presented. The caller should authenticate and
    retry."""

    reason: str = "authentication required"


@dataclass(frozen=True)
class Reject:
    """A credential was presented but the request is not permitted."""

    reason: str = "not permitted"


AuthDecision = Union[Allow, AuthRequired, Reject]
