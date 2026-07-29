"""T-M5-BROWSER-001 foundation: non-root Chromium sandbox profile.

Formalize the browser container hardening contract: no privileged, no
SYS_ADMIN, no host IPC; container-local bounded shm; non-root user; same
SEC-3 baseline as pytest (network=none, cap_drop ALL, no-new-privileges,
read-only root). ``validate_browser_container_create_kwargs`` is the
fail-closed gate DockerRunner (and any future Worker agent) must pass
before create.
"""

from __future__ import annotations

import pytest

from qarunner.domain import (
    BrowserSandboxProfile,
    DomainValidationError,
    browser_container_create_kwargs,
    validate_browser_container_create_kwargs,
)


def test_m5_browser_default_is_hardened_and_deterministic() -> None:
    profile = BrowserSandboxProfile.m5_browser_default()
    assert profile.network_mode == "none"
    assert profile.read_only_root is True
    assert profile.cap_drop == ("ALL",)
    assert profile.no_new_privileges is True
    assert profile.pids_limit == 512
    assert profile.mem_limit == "2g"
    assert profile.nano_cpus == 2_000_000_000
    assert profile.shm_size == "1g"
    assert profile.require_non_root_user is True
    assert profile.forbid_privileged is True
    assert profile.forbid_sys_admin is True
    assert profile.forbid_host_ipc is True
    # Same object shape every call — digest of policy for Evidence later.
    assert profile.profile_digest == BrowserSandboxProfile.m5_browser_default().profile_digest


def test_browser_create_kwargs_never_request_privileged_sys_admin_or_host_ipc() -> None:
    kwargs = browser_container_create_kwargs(
        profile=BrowserSandboxProfile.m5_browser_default(),
        user="1000:1000",
    )
    assert kwargs["network_mode"] == "none"
    assert kwargs["cap_drop"] == ["ALL"]
    assert kwargs["security_opt"] == ["no-new-privileges"]
    assert kwargs["read_only"] is True
    assert kwargs["pids_limit"] == 512
    assert kwargs["mem_limit"] == "2g"
    assert kwargs["nano_cpus"] == 2_000_000_000
    assert kwargs["shm_size"] == "1g"
    assert kwargs["user"] == "1000:1000"
    assert "privileged" not in kwargs or kwargs.get("privileged") is False
    assert "cap_add" not in kwargs
    assert kwargs.get("ipc_mode") not in {"host", "Host"}
    # tmpfs for /tmp is required under read-only root.
    assert "/tmp" in kwargs["tmpfs"]


def test_validate_accepts_hardened_browser_kwargs() -> None:
    kwargs = browser_container_create_kwargs(
        profile=BrowserSandboxProfile.m5_browser_default(),
        user="1000:1000",
    )
    # Must not raise.
    validate_browser_container_create_kwargs(kwargs)


def test_validate_rejects_privileged() -> None:
    kwargs = browser_container_create_kwargs(
        profile=BrowserSandboxProfile.m5_browser_default(),
        user="1000:1000",
    )
    kwargs["privileged"] = True
    with pytest.raises(DomainValidationError) as caught:
        validate_browser_container_create_kwargs(kwargs)
    assert caught.value.field == "privileged"
    assert caught.value.reason == "forbidden"


def test_validate_rejects_sys_admin_cap_add() -> None:
    kwargs = browser_container_create_kwargs(
        profile=BrowserSandboxProfile.m5_browser_default(),
        user="1000:1000",
    )
    kwargs["cap_add"] = ["SYS_ADMIN"]
    with pytest.raises(DomainValidationError) as caught:
        validate_browser_container_create_kwargs(kwargs)
    assert caught.value.field == "cap_add"
    assert caught.value.reason == "sys_admin_forbidden"


def test_validate_rejects_host_ipc() -> None:
    kwargs = browser_container_create_kwargs(
        profile=BrowserSandboxProfile.m5_browser_default(),
        user="1000:1000",
    )
    kwargs["ipc_mode"] = "host"
    with pytest.raises(DomainValidationError) as caught:
        validate_browser_container_create_kwargs(kwargs)
    assert caught.value.field == "ipc_mode"
    assert caught.value.reason == "host_ipc_forbidden"


def test_validate_rejects_missing_or_root_user() -> None:
    kwargs = browser_container_create_kwargs(
        profile=BrowserSandboxProfile.m5_browser_default(),
        user="1000:1000",
    )
    del kwargs["user"]
    with pytest.raises(DomainValidationError) as missing:
        validate_browser_container_create_kwargs(kwargs)
    assert missing.value.field == "user"

    # Mutate a valid fragment — create_kwargs itself already validates user.
    kwargs = browser_container_create_kwargs(
        profile=BrowserSandboxProfile.m5_browser_default(),
        user="1000:1000",
    )
    kwargs["user"] = "0:0"
    with pytest.raises(DomainValidationError) as root:
        validate_browser_container_create_kwargs(kwargs)
    assert root.value.field == "user"
    assert root.value.reason == "root_forbidden"

    kwargs["user"] = "root"
    with pytest.raises(DomainValidationError) as root_name:
        validate_browser_container_create_kwargs(kwargs)
    assert root_name.value.reason == "root_forbidden"

    with pytest.raises(DomainValidationError):
        browser_container_create_kwargs(
            profile=BrowserSandboxProfile.m5_browser_default(),
            user="0:0",
        )


def test_validate_rejects_missing_shm_or_weak_baseline() -> None:
    kwargs = browser_container_create_kwargs(
        profile=BrowserSandboxProfile.m5_browser_default(),
        user="1000:1000",
    )
    del kwargs["shm_size"]
    with pytest.raises(DomainValidationError) as shm:
        validate_browser_container_create_kwargs(kwargs)
    assert shm.value.field == "shm_size"

    kwargs = browser_container_create_kwargs(
        profile=BrowserSandboxProfile.m5_browser_default(),
        user="1000:1000",
    )
    kwargs["network_mode"] = "bridge"
    with pytest.raises(DomainValidationError) as net:
        validate_browser_container_create_kwargs(kwargs)
    assert net.value.field == "network_mode"

    kwargs = browser_container_create_kwargs(
        profile=BrowserSandboxProfile.m5_browser_default(),
        user="1000:1000",
    )
    kwargs["cap_drop"] = []
    with pytest.raises(DomainValidationError) as caps:
        validate_browser_container_create_kwargs(kwargs)
    assert caps.value.field == "cap_drop"


def test_profile_rejects_invalid_construction() -> None:
    base = dict(
        network_mode="none",
        read_only_root=True,
        cap_drop=("ALL",),
        no_new_privileges=True,
        pids_limit=512,
        mem_limit="2g",
        nano_cpus=2_000_000_000,
        shm_size="1g",
        require_non_root_user=True,
        forbid_privileged=True,
        forbid_sys_admin=True,
        forbid_host_ipc=True,
    )
    with pytest.raises(DomainValidationError):
        BrowserSandboxProfile(**{**base, "network_mode": ""})
    with pytest.raises(DomainValidationError):
        BrowserSandboxProfile(**{**base, "cap_drop": ()})
    with pytest.raises(DomainValidationError):
        BrowserSandboxProfile(**{**base, "read_only_root": "yes"})  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError):
        BrowserSandboxProfile(**{**base, "pids_limit": 0})
    with pytest.raises(DomainValidationError):
        BrowserSandboxProfile(**{**base, "mem_limit": " "})
    with pytest.raises(DomainValidationError):
        BrowserSandboxProfile(**{**base, "nano_cpus": True})  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError):
        BrowserSandboxProfile(**{**base, "shm_size": ""})


def test_create_kwargs_and_validate_reject_untyped_inputs() -> None:
    with pytest.raises(DomainValidationError):
        browser_container_create_kwargs(profile=object(), user="1000:1000")  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError):
        browser_container_create_kwargs(
            profile=BrowserSandboxProfile.m5_browser_default(),
            user=" ",
        )
    with pytest.raises(DomainValidationError):
        validate_browser_container_create_kwargs("bad")  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError):
        validate_browser_container_create_kwargs(
            browser_container_create_kwargs(
                profile=BrowserSandboxProfile.m5_browser_default(),
                user="1000:1000",
            ),
            profile=object(),  # type: ignore[arg-type]
        )


def test_validate_rejects_string_form_cap_add_sys_admin_and_security_opt() -> None:
    kwargs = browser_container_create_kwargs(
        profile=BrowserSandboxProfile.m5_browser_default(),
        user="1000:1000",
    )
    kwargs["cap_add"] = "SYS_ADMIN"
    with pytest.raises(DomainValidationError) as caught:
        validate_browser_container_create_kwargs(kwargs)
    assert caught.value.reason == "sys_admin_forbidden"

    kwargs = browser_container_create_kwargs(
        profile=BrowserSandboxProfile.m5_browser_default(),
        user="1000:1000",
    )
    kwargs["cap_drop"] = "ALL"  # string form still ok if contains ALL
    validate_browser_container_create_kwargs(kwargs)

    kwargs = browser_container_create_kwargs(
        profile=BrowserSandboxProfile.m5_browser_default(),
        user="1000:1000",
    )
    kwargs["security_opt"] = "no-new-privileges"
    validate_browser_container_create_kwargs(kwargs)

    kwargs = browser_container_create_kwargs(
        profile=BrowserSandboxProfile.m5_browser_default(),
        user="1000:1000",
    )
    kwargs["security_opt"] = ["apparmor=unconfined"]
    with pytest.raises(DomainValidationError) as sec:
        validate_browser_container_create_kwargs(kwargs)
    assert sec.value.field == "security_opt"

    kwargs = browser_container_create_kwargs(
        profile=BrowserSandboxProfile.m5_browser_default(),
        user="1000:1000",
    )
    kwargs["read_only"] = False
    with pytest.raises(DomainValidationError):
        validate_browser_container_create_kwargs(kwargs)

    kwargs = browser_container_create_kwargs(
        profile=BrowserSandboxProfile.m5_browser_default(),
        user="1000:1000",
    )
    kwargs["pids_limit"] = 1
    with pytest.raises(DomainValidationError):
        validate_browser_container_create_kwargs(kwargs)
    kwargs = browser_container_create_kwargs(
        profile=BrowserSandboxProfile.m5_browser_default(),
        user="1000:1000",
    )
    kwargs["mem_limit"] = "512m"
    with pytest.raises(DomainValidationError):
        validate_browser_container_create_kwargs(kwargs)
    kwargs = browser_container_create_kwargs(
        profile=BrowserSandboxProfile.m5_browser_default(),
        user="1000:1000",
    )
    kwargs["nano_cpus"] = 1
    with pytest.raises(DomainValidationError):
        validate_browser_container_create_kwargs(kwargs)
    kwargs = browser_container_create_kwargs(
        profile=BrowserSandboxProfile.m5_browser_default(),
        user="1000:1000",
    )
    kwargs["shm_size"] = "512m"
    with pytest.raises(DomainValidationError):
        validate_browser_container_create_kwargs(kwargs)


def test_validate_skips_optional_checks_when_profile_disables_them() -> None:
    """Branch coverage for profiles that intentionally relax no-new-privileges
    or non-root requirements (not the M5 default — only for complete coverage)."""
    relaxed = BrowserSandboxProfile(
        network_mode="none",
        read_only_root=True,
        cap_drop=("ALL",),
        no_new_privileges=False,
        pids_limit=512,
        mem_limit="2g",
        nano_cpus=2_000_000_000,
        shm_size="1g",
        require_non_root_user=False,
        forbid_privileged=True,
        forbid_sys_admin=True,
        forbid_host_ipc=True,
    )
    kwargs = {
        "user": "0:0",  # allowed because require_non_root_user is False
        "network_mode": "none",
        "cap_drop": ["ALL"],
        "security_opt": [],  # no no-new-privileges required
        "pids_limit": 512,
        "mem_limit": "2g",
        "nano_cpus": 2_000_000_000,
        "read_only": True,
        "tmpfs": {"/tmp": ""},
        "shm_size": "1g",
        "privileged": False,
    }
    validate_browser_container_create_kwargs(kwargs, profile=relaxed)
