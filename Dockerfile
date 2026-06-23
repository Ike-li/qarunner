FROM python:3.12-slim

WORKDIR /app

# Pre-install standard testing utilities for zero-dependency execution
RUN pip install --no-cache-dir \
    pytest \
    pytest-cov \
    pytest-asyncio \
    allure-pytest

# Run untrusted test code as a non-root uid (DEP-1). docker_runner overrides the
# uid at runtime (user=<host uid>:<gid>, SEC-3); this keeps the image non-root
# even if run directly. Pair with --read-only / --cap-drop ALL / --network none.
RUN useradd --create-home --uid 1000 runner
USER runner

# Default command
CMD ["python", "-m", "pytest", "--help"]
