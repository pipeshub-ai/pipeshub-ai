# PostgreSQL image and PgBouncer (compose profile `postgres`)

Opt-in: nothing starts unless `postgres` is in `COMPOSE_PROFILES`, and nothing in the application connects to it yet. Without the profile `docker compose config` is unchanged.

- `Dockerfile`: pgvector 0.8.7 base for PostgreSQL 18 (default) or 17, both pinned by digest and multi-arch; adds `pg_textsearch` 1.5.1 and PgBouncer 1.26.1 (source tarballs checked by SHA-256). Build: `docker compose --profile postgres build postgres`. The `postgres` and `pgbouncer` services run the same image.
- `pgbouncer.ini`: transaction pooling with `server_reset_query` and `server_reset_query_always = 1`, so session state (role, settings, temp tables, cursors) never reaches the next client. Do not weaken these. One server pool per login; the sizes add up to at most `max_db_connections`.
- `initdb/10-roles.sh`: on the first start of an empty data volume it creates the login roles (one per service, plus jobs, migrate and backup) from the `PG_*_PASSWORD` variables, installs `vector` and `pg_trgm` into schema `ext`, and writes the SCRAM verifiers PgBouncer authenticates against to the `pgbouncer_auth` volume (mode 0600, owned by `postgres`; mounted read-only into PgBouncer). No role has SUPERUSER, CREATEROLE or BYPASSRLS, and a service login holds no privileges until its unit of work switches to its service role. Tables, policies and grants come with the migrations.
- `pg-entrypoint.sh`: server defaults (`shared_preload_libraries`, `max_connections`, `shared_buffers`), and a refusal to start on a volume whose cluster is not where `PGDATA` points.

Secrets: the passwords are read from the container environment at first initialisation only; they are not in the image, the build context or the logs. They remain in the container's environment (`docker inspect`) and in the stored role verifiers.

## Data volume

The cluster lives in the named volume `<project>_postgres_data`, mounted at `/var/lib/postgresql/data` with `PGDATA` set to that path, on both majors (a `tmpfs` over `/var/lib/postgresql` keeps the 18 image from creating an anonymous volume). `down` then `up` keeps the data; only `down -v` removes it. `PG_MAJOR` is fixed per volume and there is no in-place major upgrade: dump from the old stack and load into a new volume. The server refuses to start on a cluster of another major, and `pg-entrypoint.sh` exits 78 instead of initialising an empty cluster beside one it finds elsewhere on the volume.

Changing a password later means `ALTER ROLE` as the administrative login and regenerating `/pgbouncer-auth/userlist.txt`; the file is not refreshed on restart.

## Tests

`tests/pg_entrypoint_layout_test.sh` (no Docker), `tests/postgres_persistence_test.sh` (17 and 18: data survives `down`/`up`, no anonymous volume) and `tests/postgres_roles_test.sh` (roles, privileges and PgBouncer authentication). The `postgres-image` workflow runs all three.
