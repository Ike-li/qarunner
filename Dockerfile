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

# Run untrusted test code as a non-root uid (DEP-1). docker_runner overrides the
# uid at runtime (user=<host uid>:<gid>, SEC-3); this keeps the image non-root
# even if run directly. Pair with --read-only / --cap-drop ALL / --network none.
RUN useradd --create-home --uid 1000 runner
USER runner

# Default command
CMD ["python", "-m", "pytest", "--help"]
