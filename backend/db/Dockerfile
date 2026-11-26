# pipeshub-migrate: runs the schema migrations (python -m db.migrate) and nothing else.
# psycopg is LGPL-3.0; it ships only in this tool image, never in the application images.
# Build from the repository root:  docker build -f backend/db/Dockerfile -t pipeshubai/pipeshub-migrate .
# Pinned by digest (SEC-SC-11; an OCI index: linux/amd64 and linux/arm64), the same base as Dockerfile.base. To bump,
# resolve the digest of the new tag and run the migration tests on a fresh database.
FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f

WORKDIR /app
COPY backend/db/requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --require-hashes --no-deps -r /tmp/requirements.lock && rm /tmp/requirements.lock

COPY backend/db /app/db
# The extra-migration-locations list is read from here by file path (see db/locations.py).
COPY backend/python/app/edition.py /app/python/app/edition.py

RUN useradd --uid 1000 --no-create-home migrate
USER 1000
CMD ["python", "-m", "db.migrate"]
