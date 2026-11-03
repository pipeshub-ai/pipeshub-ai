# syntax=docker/dockerfile:1
# PostgreSQL image for the opt-in `postgres` compose profile: pgvector, pg_trgm, pg_textsearch,
# and PgBouncer (the same image runs the `postgres` and `pgbouncer` services).
#   docker build --build-arg PG_MAJOR=17 -t pipeshubai/postgres:17-1 deployment/docker-compose/postgres
# Both bases are multi-arch (linux/amd64, linux/arm64) and pinned by digest. To bump one, resolve the
# new digest for the tag, update it here, and run tests/postgres_roles_test.sh and tests/postgres_persistence_test.sh on both majors.
ARG PG_MAJOR=18
FROM pgvector/pgvector:0.8.7-pg18-bookworm@sha256:2358fcba361ed2233a5ed81b5fe4ca779ccb304120ce531a3bf51c0ed7e2bc11 AS base-18
FROM pgvector/pgvector:0.8.7-pg17-bookworm@sha256:ac08538c6f8b9904c33c8224c5e5706dbe760aca29db1d096972b4052c22a75d AS base-17

# Versions this image is tested with. Source tarballs are verified by checksum.
# hadolint ignore=DL3006
FROM base-${PG_MAJOR} AS build
ARG PG_MAJOR
ARG PG_TEXTSEARCH_VERSION=1.5.1
ARG PG_TEXTSEARCH_SHA256=2e7fb76ed96176afc6d16cc5d095ced2cb233ff27fbeaa8c7bfffc82e02f7498
ARG PGBOUNCER_VERSION=1.26.1
ARG PGBOUNCER_SHA256=00886a2994b4844cd37110970df777772cfc42a74c400522c82fba509a3f42d9
# apt installs the current Debian/PGDG build tools; only the build stage uses them, and the ABI is stable within a major
# hadolint ignore=DL3008
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential ca-certificates curl \
      postgresql-server-dev-${PG_MAJOR} libevent-dev libssl-dev pkg-config \
 && rm -rf /var/lib/apt/lists/*
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
WORKDIR /src
RUN curl -fsSL -o pts.tgz "https://github.com/timescale/pg_textsearch/archive/refs/tags/v${PG_TEXTSEARCH_VERSION}.tar.gz" \
 && echo "${PG_TEXTSEARCH_SHA256}  pts.tgz" | sha256sum -c - \
 && tar xzf pts.tgz \
 && make -C "pg_textsearch-${PG_TEXTSEARCH_VERSION}" -j"$(nproc)" \
 && make -C "pg_textsearch-${PG_TEXTSEARCH_VERSION}" install
RUN curl -fsSL -o pgb.tgz "https://www.pgbouncer.org/downloads/files/${PGBOUNCER_VERSION}/pgbouncer-${PGBOUNCER_VERSION}.tar.gz" \
 && echo "${PGBOUNCER_SHA256}  pgb.tgz" | sha256sum -c - \
 && tar xzf pgb.tgz
WORKDIR /src/pgbouncer-${PGBOUNCER_VERSION}
# touch: the tarball ships the man pages, but make would rebuild them with pandoc, which is not installed
RUN ./configure --prefix=/opt/pgbouncer --with-openssl \
 && touch doc/pgbouncer.1 doc/pgbouncer.5 \
 && make -j"$(nproc)" && make install

# hadolint ignore=DL3006
FROM base-${PG_MAJOR}
ARG PG_MAJOR
# hadolint ignore=DL3008
RUN apt-get update \
 && apt-get install -y --no-install-recommends libevent-2.1-7 \
 && rm -rf /var/lib/apt/lists/*
# pg_textsearch files, then the PgBouncer binary
COPY --from=build /usr/lib/postgresql/${PG_MAJOR}/lib/pg_textsearch* /usr/lib/postgresql/${PG_MAJOR}/lib/
COPY --from=build /usr/share/postgresql/${PG_MAJOR}/extension/pg_textsearch* /usr/share/postgresql/${PG_MAJOR}/extension/
COPY --from=build /opt/pgbouncer/bin/pgbouncer /usr/local/bin/pgbouncer
COPY pgbouncer.ini /etc/pgbouncer/pgbouncer.ini
COPY initdb/ /docker-entrypoint-initdb.d/
COPY pg-entrypoint.sh /usr/local/bin/pg-entrypoint.sh
# /pgbouncer-auth carries the SCRAM verifier file from postgres (writer) to pgbouncer (reader)
RUN chmod 0755 /usr/local/bin/pg-entrypoint.sh /docker-entrypoint-initdb.d/*.sh \
 && install -d -o postgres -g postgres -m 0700 /pgbouncer-auth \
 && pgbouncer --version >/dev/null
ENTRYPOINT ["pg-entrypoint.sh"]
CMD ["postgres"]
