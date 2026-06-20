FROM python:3.12-slim

WORKDIR /app

# Pre-install standard testing utilities for zero-dependency execution
RUN pip install --no-cache-dir \
    pytest \
    pytest-cov \
    pytest-asyncio \
    allure-pytest

# Default command
CMD ["python", "-m", "pytest", "--help"]
