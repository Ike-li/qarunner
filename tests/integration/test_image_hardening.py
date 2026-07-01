"""Image-level hardening checks (DEP-1): both images run as a non-root user.

These run a throwaway container from each built image and read its effective
uid/user via ``id``. They assert the image's baked-in ``USER`` is a non-root
account — the executor image so untrusted test code never starts as root, the
server image as defence in depth.

Opt-in ``docker`` marker (needs a daemon + the images). The executor image is
built on demand by DockerRunner; the server image must be built first
(``docker build -f Dockerfile.server -t qarunner:latest .``) or the
server check skips. Run with ``uv run pytest -m docker --no-cov``.
"""

from __future__ import annotations

import pytest

docker = pytest.importorskip("docker")
from docker.errors import ImageNotFound  # noqa: E402

pytestmark = pytest.mark.docker


@pytest.fixture(scope="module")
def docker_client():
    try:
        client = docker.from_env()
        client.ping()
    except Exception as exc:  # daemon down / socket missing
        pytest.skip(f"Docker daemon unavailable: {exc}")
    yield client
    client.close()


def _id_output(client, image: str) -> str:
    """Run ``id`` in a fresh container of *image* and return its stdout."""
    raw = client.containers.run(
        image,
        ["id"],
        remove=True,
        network_mode="none",
    )
    return raw.decode().strip()


def _require_image(client, image: str) -> None:
    try:
        client.images.get(image)
    except ImageNotFound:
        pytest.skip(f"image {image} not built")


def test_executor_image_runs_as_non_root(docker_client):
    """The executor base image (DockerRunner's qarunner-executor:latest) bakes a
    non-root USER (DEP-1)."""
    _require_image(docker_client, "qarunner-executor:latest")
    out = _id_output(docker_client, "qarunner-executor:latest")
    assert "uid=0(" not in out, f"executor image runs as root: {out}"
    assert "uid=1000(runner)" in out, out


def test_server_image_runs_as_non_root(docker_client):
    """The server image bakes a non-root USER (DEP-1)."""
    _require_image(docker_client, "qarunner:latest")
    out = _id_output(docker_client, "qarunner:latest")
    assert "uid=0(" not in out, f"server image runs as root: {out}"
    assert "uid=1000(app)" in out, out
