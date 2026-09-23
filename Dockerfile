FROM python:3.12-slim

WORKDIR /app

# Pre-install standard testing utilities for zero-dependency execution.
# Pinned to the platform's locked versions (uv.lock) for reproducible executor
# images (DEP-5); bump these together with uv.lock so executor and platform stay
# in lockstep.
RUN pip install --no-cache-dir \
    pytest==9.1.0 \
    pytest-cov==7.1.0 \
    pytest-asyncio==1.4.0 \
    allure-pytest==2.16.0

# T-M4-PYTEST-ADAPTER-001: the canonical result plugin is baked into the image
# at a fixed PYTHONPATH location (not injected alongside the copied test
# source) so `-p qarunner_canonical_plugin` resolves reliably regardless of
# whether the sandboxed command is invoked as `python -m pytest` or bare
# `pytest` — see qarunner_canonical_plugin.py's own module docstring.
COPY src/qarunner/pytest_plugin/qarunner_canonical_plugin.py /opt/qarunner-adapter/qarunner_canonical_plugin.py
ENV PYTHONPATH=/opt/qarunner-adapter

# Run untrusted test code as a non-root uid (DEP-1). docker_runner runs every
# sandbox as 1000:1000 (SEC-3, its _SANDBOX_UID); this keeps the image non-root
# even if run directly. Pair with --read-only / --cap-drop ALL / --network none.
# /workspace is owned by that uid: the per-run named volume docker_runner mounts
# there inherits this ownership on first mount, making the workdir writable.
RUN useradd --create-home --uid 1000 runner \
    && mkdir /workspace && chown 1000:1000 /workspace
USER runner

# Default command
CMD ["python", "-m", "pytest", "--help"]
