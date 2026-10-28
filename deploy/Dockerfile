# BAEC production web image (V0.4 Phase 5; IMPLEMENTATION). Build from the repository root:
#
#     docker build -f deploy/Dockerfile -t baec-web:<release> .
#
# * Python pinned by image digest; dependencies installed only from deploy/requirements.lock (exact versions, every
#   file's SHA-256, --no-deps), then `pip check`.
# * Runs as an unprivileged user (uid 10001) that owns nothing in /app; the code is read-only to it. Works with a
#   read-only root filesystem (HOME and Streamlit's scratch space are under /tmp, a tmpfs in deploy/compose.yaml).
# * Contains no secret, certificate, .env, local data, test, MCP server, or git history (.dockerignore is an
#   allowlist). Database URLs, the sign-in configuration, and the database CA are mounted at run time as secrets.
# * Production only: BAEC_DEPLOYMENT_MODE is fixed to production, so this image never opens SQLite or the
#   local-development principal.
# * The same image runs the release job (python -m baec_console.postgres.migrate) and the provisioning command, each
#   with only its own credential (deploy/compose.yaml); the web service never receives those credentials.
FROM python:3.14-slim@sha256:cd89c30eda26ee911a4611b18680086e0ea42e6f5f8cbe653d07fa7958c9cec8

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

COPY deploy/requirements.lock /tmp/requirements.lock
RUN pip install --root-user-action=ignore --require-hashes --no-deps -r /tmp/requirements.lock \
    && pip check \
    && rm /tmp/requirements.lock

RUN groupadd --system --gid 10001 baec \
    && useradd --system --uid 10001 --gid baec --no-create-home --home-dir /tmp --shell /usr/sbin/nologin baec

WORKDIR /app
COPY baec_app /app/baec_app
COPY baec_console /app/baec_console
COPY deploy/streamlit/config.toml /app/.streamlit/config.toml
RUN python -m compileall -q /app/baec_app /app/baec_console

ENV HOME=/tmp \
    BAEC_DEPLOYMENT_MODE=production

USER 10001:10001
EXPOSE 8501

# Liveness only: the Streamlit process answers. Readiness (database, schema, roles, sign-in configuration) is
# `python -m baec_console.readiness`, run by the orchestrator (deploy/compose.yaml).
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=4).read() != b'ok')"]

CMD ["python", "-m", "streamlit", "run", "baec_console/app.py"]
