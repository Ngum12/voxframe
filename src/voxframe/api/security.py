"""Local-only security for the web app.

The web app is a local tool, which is a weaker position than it sounds. A server
on localhost is reachable by **every page the user visits** while it runs, and by
anything else on their machine. The defences here are the ones that matter for
that threat model, each one cheap and each one load-bearing:

1. **Bind to 127.0.0.1.** A server on ``0.0.0.0`` is reachable from the local
   network — a café, a shared office. The default binds to loopback and
   :func:`assert_local_bind` refuses anything else without an explicit override.

2. **Validate the ``Host`` header.** Loopback binding alone does not stop *DNS
   rebinding*: an attacker's page resolves their domain to 127.0.0.1, the
   browser sends the request to our server, and the ``Origin`` check passes
   because it is their own origin. The defence is to reject any request whose
   ``Host`` is not a loopback name, because a rebound request carries the
   attacker's hostname.

3. **Require a per-session token**, generated at launch and never written to
   disk. This stops other local processes and other browser pages, which can
   reach the port but cannot read the token from the terminal.

4. **Confine file access** to the job and library directories. Every path the
   API accepts from a client is resolved and checked to be inside one of them,
   so ``../../.ssh/id_rsa`` is refused rather than served. Symlinks are
   resolved before the check, because a link inside an allowed directory can
   point outside it.

None of this makes the app safe to expose publicly, and it is not meant to. A
hosted multi-user version is a different project with different requirements
(owner's decision 2).
"""

from __future__ import annotations

import ipaddress
import secrets
from pathlib import Path

import structlog

__all__ = [
    "LOOPBACK_HOSTS",
    "AccessDenied",
    "PathOutsideSandbox",
    "SessionToken",
    "assert_local_bind",
    "host_is_loopback",
    "resolve_within",
]

log = structlog.get_logger(__name__)

#: Hostnames that legitimately mean "this machine". A request arriving with
#: anything else is either misconfigured or a rebinding attempt.
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


class AccessDenied(Exception):
    """A request failed an authentication or Host check."""


class PathOutsideSandbox(Exception):
    """A client-supplied path resolved outside every allowed directory."""


class SessionToken:
    """A secret generated at launch, compared in constant time.

    Not persisted. A token on disk outlives the process that needed it and
    becomes a credential any local process can read; regenerating per launch
    costs nothing and removes that.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str | None = None) -> None:
        # 32 bytes of urandom, URL-safe: it travels in a query string on the
        # first navigation, before any JavaScript can set a header.
        self._value = value or secrets.token_urlsafe(32)

    @property
    def value(self) -> str:
        return self._value

    def matches(self, candidate: str | None) -> bool:
        """Whether a presented token is the right one.

        Uses :func:`secrets.compare_digest`, so the comparison time does not
        depend on how many leading characters are correct.
        """
        if not candidate:
            return False
        return secrets.compare_digest(self._value, candidate)

    def __repr__(self) -> str:
        # Never the value: this object ends up in tracebacks and log lines.
        return "SessionToken(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"


def host_is_loopback(host_header: str | None) -> bool:
    """Whether a ``Host`` header names this machine.

    The port is stripped before comparison, and a bare IPv6 address is accepted
    in its bracketed form. Anything unparseable is refused: a header we cannot
    understand is not one we should trust.

    Args:
        host_header: The raw header value, e.g. ``127.0.0.1:8765``.

    Returns:
        ``True`` if the request is addressed to loopback.
    """
    if not host_header:
        return False

    host = host_header.strip()

    # Bracketed IPv6, with or without a port: [::1] or [::1]:8765
    if host.startswith("["):
        closing = host.find("]")
        if closing == -1:
            return False
        host = host[1 : closing]
    elif host.count(":") == 1:
        host = host.split(":", 1)[0]
    elif host.count(":") > 1:
        # Bare IPv6 with no brackets is not valid in a Host header.
        return False

    host = host.lower()
    if host in LOOPBACK_HOSTS:
        return True

    # 127.0.0.0/8 is all loopback, not just 127.0.0.1.
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def assert_local_bind(host: str, *, allow_remote: bool = False) -> None:
    """Refuse to start on a non-loopback interface unless explicitly allowed.

    Binding to ``0.0.0.0`` exposes an unauthenticated-by-design local tool to
    the network. The override exists because someone will have a real reason
    (a container, a remote dev box), but it must be typed deliberately.

    Raises:
        AccessDenied: If ``host`` is not loopback and ``allow_remote`` is unset.
    """
    if allow_remote:
        log.warning("api.bind.remote", host=host)
        return

    try:
        is_loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = host.lower() in LOOPBACK_HOSTS

    if not is_loopback:
        raise AccessDenied(
            f"Refusing to bind to {host}: the web app has no multi-user "
            f"authentication and must not be exposed. Pass allow_remote only "
            f"if you have your own access control in front of it."
        )


def resolve_within(candidate: str | Path, allowed: tuple[Path, ...]) -> Path:
    """Resolve a client-supplied path, requiring it to sit inside ``allowed``.

    Symlinks are resolved *before* the containment check, because a link inside
    an allowed directory can point anywhere. This is why the check cannot be a
    string prefix test.

    Args:
        candidate: The path a client asked for.
        allowed: Directories the client may reach into.

    Returns:
        The resolved absolute path.

    Raises:
        PathOutsideSandbox: If it lands outside every allowed directory, or if
            ``allowed`` is empty — which would otherwise permit everything.
    """
    if not allowed:
        raise PathOutsideSandbox("No directories are allowed, so nothing is readable")

    resolved = Path(candidate).expanduser().resolve()

    for directory in allowed:
        root = directory.expanduser().resolve()
        if resolved == root or root in resolved.parents:
            return resolved

    # The message names what was asked for but not the allowed roots: a path
    # traversal probe should not learn the layout it failed to reach.
    raise PathOutsideSandbox(f"Path is outside the allowed directories: {candidate}")
