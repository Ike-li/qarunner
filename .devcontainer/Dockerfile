# qarunner development container — Python 3.12 + Node 22 + Playwright + Allure
# Use: VS Code "Reopen in Container" or GitHub Codespaces.

FROM python:3.12-slim

# ── System dependencies ──────────────────────────────────────────────────
# Java 21 JRE for Allure CLI, plus general dev tools.
# openjdk-21 (not 17): python:3.12-slim tracks Debian trixie which dropped
# openjdk-17-jre-headless.
RUN apt-get update && apt-get install -y --no-install-recommends \
    openjdk-21-jre-headless \
    wget \
    unzip \
    git \
    curl \
    ca-certificates \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# ── Allure CLI ───────────────────────────────────────────────────────────
RUN wget -q https://github.com/allure-framework/allure2/releases/download/2.24.0/allure-2.24.0.zip \
    && unzip -q allure-2.24.0.zip -d /opt/ \
    && ln -s /opt/allure-2.24.0/bin/allure /usr/bin/allure \
    && rm allure-2.24.0.zip

# ── Node.js 22 (via nodesource) ─────────────────────────────────────────
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# ── Python tooling ───────────────────────────────────────────────────────
RUN pip install --no-cache-dir uv

# ── Playwright system deps (Chromium) ────────────────────────────────────
# Install OS-level libraries needed by Chromium via apt-get directly.
# Using apt-get instead of `npx playwright install-deps` because the
# Playwright npm package isn't installed yet at this stage.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libatspi2.0-0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    libwayland-client0 \
    && rm -rf /var/lib/apt/lists/*

# ── App setup ────────────────────────────────────────────────────────────
WORKDIR /app

# Pre-install Python dependencies from lockfile for faster postCreateCommand.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --all-extras --no-install-project \
    && rm -rf /root/.cache

# Pre-install Node dependencies for faster postCreateCommand.
COPY frontend/package.json frontend/package-lock.json ./frontend/
RUN cd frontend && npm ci && rm -rf /root/.npm

# Put the synced virtualenv first on PATH.
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app/src"

# Default: keep container alive for VS Code to attach.
CMD ["sleep", "infinity"]
