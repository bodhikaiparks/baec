# BAEC operations image (V0.4 Phase 5): PostgreSQL 16 client tools (pg_dump, pg_restore, psql) and the backup tool.
# Separate from the web image, which has no database client tools and never receives a backup or restore credential.
# Build from the repository root:  docker build -f deploy/ops/Dockerfile -t baec-ops:<release> deploy/ops
FROM postgres:16-alpine@sha256:0cefaabd882e42bcb2a2f3dc56d3ce4770560ba62fd7f197f1a2e008fd5731ab

RUN apk add --no-cache python3
COPY baec_backup.py /usr/local/bin/baec_backup.py
USER postgres
ENTRYPOINT ["python3", "/usr/local/bin/baec_backup.py"]
