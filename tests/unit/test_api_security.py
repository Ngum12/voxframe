"""Local security for the web app (D-113, owner's decision 5).

A server on localhost is reachable by **every page the user visits** while it
runs, and by every other process on their machine. Each test here pins one
defence against that, and each defence exists because removing it opens a
specific hole named in the test's docstring.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voxframe.api.security import (
    AccessDenied,
    PathOutsideSandbox,
    SessionToken,
    assert_local_bind,
    host_is_loopback,
    resolve_within,
)


class TestSessionToken:
    def test_a_token_is_generated_when_none_is_given(self) -> None:
        assert SessionToken().value

    def test_two_tokens_differ(self) -> None:
        """A predictable token is no token at all."""
        assert SessionToken().value != SessionToken().value

    def test_a_token_is_long_enough_to_resist_guessing(self) -> None:
        # 32 bytes urlsafe-encoded is 43 characters.
        assert len(SessionToken().value) >= 40

    def test_the_right_token_matches(self) -> None:
        token = SessionToken()

        assert token.matches(token.value)

    def test_a_wrong_token_does_not_match(self) -> None:
        assert not SessionToken("abc").matches("abd")

    @pytest.mark.parametrize("presented", ["", None])
    def test_an_absent_token_does_not_match(self, presented: str | None) -> None:
        """An empty string must not be treated as "no check required"."""
        assert not SessionToken("abc").matches(presented)

    def test_a_prefix_does_not_match(self) -> None:
        assert not SessionToken("abcdef").matches("abc")

    def test_repr_does_not_leak_the_value(self) -> None:
        """Tokens end up in tracebacks and log lines."""
        token = SessionToken("supersecretvalue")

        assert "supersecretvalue" not in repr(token)
        assert "supersecretvalue" not in str(token)


class TestHostValidation:
    """Loopback binding does not stop DNS rebinding; the Host check does."""

    @pytest.mark.parametrize(
        "host",
        [
            "localhost",
            "localhost:8765",
            "127.0.0.1",
            "127.0.0.1:8765",
            "127.0.0.2",
            "127.1.2.3:9000",
            "[::1]",
            "[::1]:8765",
            "LOCALHOST:8765",
        ],
    )
    def test_loopback_hosts_are_accepted(self, host: str) -> None:
        assert host_is_loopback(host)

    @pytest.mark.parametrize(
        "host",
        [
            "evil.example.com",
            "evil.example.com:8765",
            # The rebinding case: the attacker's name resolving to loopback.
            "attacker.test:8765",
            "192.168.1.10:8765",
            "10.0.0.1",
            "0.0.0.0",
            "localhost.evil.com",
            "notlocalhost",
        ],
    )
    def test_other_hosts_are_refused(self, host: str) -> None:
        assert not host_is_loopback(host)

    @pytest.mark.parametrize("host", ["", None, "[::1", "::1", "a:b:c"])
    def test_unparseable_hosts_are_refused(self, host: str | None) -> None:
        """A header we cannot understand is not one to trust."""
        assert not host_is_loopback(host)


class TestBindRefusal:
    @pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
    def test_loopback_binds_are_allowed(self, host: str) -> None:
        assert_local_bind(host)

    @pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "::"])
    def test_non_loopback_binds_are_refused(self, host: str) -> None:
        """The app has no multi-user authentication, so this must be deliberate."""
        with pytest.raises(AccessDenied):
            assert_local_bind(host)

    def test_the_override_works_when_asked_for(self) -> None:
        assert_local_bind("0.0.0.0", allow_remote=True)

    def test_the_refusal_explains_itself(self) -> None:
        with pytest.raises(AccessDenied, match="authentication"):
            assert_local_bind("0.0.0.0")


class TestPathSandbox:
    def test_a_path_inside_is_returned(self, tmp_path: Path) -> None:
        inside = tmp_path / "jobs" / "a.mp4"
        inside.parent.mkdir()
        inside.touch()

        assert resolve_within(inside, (tmp_path,)) == inside.resolve()

    def test_the_root_itself_is_allowed(self, tmp_path: Path) -> None:
        assert resolve_within(tmp_path, (tmp_path,)) == tmp_path.resolve()

    def test_traversal_is_refused(self, tmp_path: Path) -> None:
        """The case this exists for: ../../.ssh/id_rsa."""
        with pytest.raises(PathOutsideSandbox):
            resolve_within(tmp_path / ".." / ".." / "secret.txt", (tmp_path,))

    def test_a_sibling_directory_is_refused(self, tmp_path: Path) -> None:
        """A prefix test would wrongly allow /jobs-evil next to /jobs."""
        allowed = tmp_path / "jobs"
        allowed.mkdir()
        sibling = tmp_path / "jobs-evil"
        sibling.mkdir()

        with pytest.raises(PathOutsideSandbox):
            resolve_within(sibling / "x.txt", (allowed,))

    def test_no_allowed_directories_refuses_everything(self, tmp_path: Path) -> None:
        """An empty allowlist must not mean "allow all"."""
        with pytest.raises(PathOutsideSandbox):
            resolve_within(tmp_path / "x", ())

    def test_several_roots_are_each_honoured(self, tmp_path: Path) -> None:
        jobs = tmp_path / "jobs"
        library = tmp_path / "library"
        jobs.mkdir()
        library.mkdir()

        assert resolve_within(library / "photo.jpg", (jobs, library))

    def test_the_error_does_not_reveal_the_allowed_roots(self, tmp_path: Path) -> None:
        """A traversal probe should not learn the layout it failed to reach."""
        secret_root = tmp_path / "very-private-directory-name"
        secret_root.mkdir()

        with pytest.raises(PathOutsideSandbox) as caught:
            resolve_within(tmp_path / "elsewhere", (secret_root,))

        assert "very-private-directory-name" not in str(caught.value)

    @pytest.mark.skipif(
        not hasattr(Path, "symlink_to"), reason="symlinks unsupported"
    )
    def test_a_symlink_pointing_outside_is_refused(self, tmp_path: Path) -> None:
        """A link inside an allowed directory can point anywhere.

        This is why containment cannot be a string prefix check: the path *looks*
        inside until it is resolved.
        """
        allowed = tmp_path / "jobs"
        allowed.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "secret.txt"
        target.write_text("secret", encoding="utf-8")

        link = allowed / "innocent.txt"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            pytest.skip("symlink creation not permitted on this machine")

        with pytest.raises(PathOutsideSandbox):
            resolve_within(link, (allowed,))
