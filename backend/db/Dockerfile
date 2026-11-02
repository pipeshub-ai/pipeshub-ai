# pipeshub-migrate: runs the schema migrations (python -m db.migrate) and nothing else.
# psycopg is LGPL-3.0; it ships only in this tool image, never in the application images.
# Build from the repository root:  docker build -f backend/db/Dockerfile -t pipeshubai/pipeshub-migrate .
FROM python:3.12-slim

WORKDIR /app
COPY backend/db/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

COPY backend/db /app/db
# The extra-migration-locations list is read from here by file path (see db/locations.py).
COPY backend/python/app/edition.py /app/python/app/edition.py

RUN useradd --uid 1000 --no-create-home migrate
USER 1000
CMD ["python", "-m", "db.migrate"]
