# pipeshub-migrate: runs the schema migrations (python -m db.migrate) and nothing else.
# psycopg is LGPL-3.0; it ships only in this tool image, never in the application images.
# Build from the repository root:  docker build -f backend/db/Dockerfile -t pipeshubai/pipeshub-migrate .
# Pinned by digest (an OCI index: linux/amd64 and linux/arm64, among others). To bump, resolve the digest of the new tag and run the
# migration tests on a fresh database.
FROM python:3.12-slim@sha256:b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30

WORKDIR /app
COPY backend/db/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

COPY backend/db /app/db
# The extra-migration-locations list is read from here by file path (see db/locations.py).
COPY backend/python/app/edition.py /app/python/app/edition.py

RUN useradd --uid 1000 --no-create-home migrate
USER 1000
CMD ["python", "-m", "db.migrate"]
