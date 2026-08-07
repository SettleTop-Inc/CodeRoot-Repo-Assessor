# CodeRoot Repo Assessor — HTTP + MCP surfaces over one judgment core.
FROM python:3.12-slim

# git is required: acquisition is a partial clone, not a REST crawl.
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY assessor ./assessor
# --locked (not --frozen) fails the build if uv.lock has drifted from
# pyproject.toml, matching CI's `uv sync --locked` gate (ci.yml) so the graph
# CI validates and the graph that ships are the same. Exported to a
# requirements file and installed with `uv pip install --system` rather than
# `uv sync` so the image keeps its existing no-venv layout (global
# site-packages, CMD invokes `uvicorn` directly) instead of switching to a
# project-venv one. `--no-emit-project` + a separate `--no-deps .` install:
# the exported file pins every dependency at its exact locked version, and
# installing the local package with --no-deps afterward can't reintroduce
# unpinned resolution for its own dependencies.
RUN uv export --locked --no-dev --no-hashes --no-emit-project -o requirements.txt \
    && uv pip install --system --no-cache -r requirements.txt \
    && uv pip install --system --no-cache --no-deps .

# Non-root so a pod securityContext can enforce runAsNonRoot.
RUN useradd --uid 1000 --create-home --shell /bin/bash app && chown -R app:app /app
# Acquisition caches bare clones here. Create and own it as uid 1000 so a FRESH
# volume inherits that ownership — otherwise Docker mounts it root-owned, the
# non-root process cannot write, and every acquire fails with EACCES.
RUN mkdir -p /acquire-cache && chown app:app /acquire-cache
USER 1000

# Inside a container, binding all interfaces is correct — the published port is the
# operator's choice. Declared as ENV rather than hardcoded in CMD so ASSESSOR_BIND_ADDR
# is a real setting with a visible container-specific default, not dead config.
ENV ASSESSOR_BIND_ADDR=0.0.0.0
EXPOSE 8081
# exec form via sh so ${ASSESSOR_BIND_ADDR} expands; `exec` keeps uvicorn as PID 1 so
# it receives SIGTERM directly and shuts down cleanly.
CMD ["sh", "-c", "exec uvicorn assessor.app:create_app --factory --host ${ASSESSOR_BIND_ADDR} --port 8081"]
