-- The document store's cutover fence. One row says which backend holds the application data, its epoch, and whether
-- writes are frozen. Written only by the migrate job (owner). Write units of work call kernel.assert_store_epoch(n) in
-- their prologue (vetted/assert_store_epoch.sql): it takes a SHARED transaction-scoped advisory lock (class 7340, object 1,
-- registered in kernel.lock_keys as docstore_fence) that is held to commit, so the migrator's freeze (the exclusive lock in the
-- same transaction as the UPDATE) waits for every writer in flight and every later writer is refused with 25006.
-- The functions are SECURITY DEFINER, owned by the vetted owner of schema kernel, which reads the row through the extension-read
-- allowance; the service roles hold no privilege on the table.
CREATE SCHEMA IF NOT EXISTS platform;

CREATE TABLE platform.store_epoch (
  one        boolean PRIMARY KEY DEFAULT true CHECK (one),
  backend    text NOT NULL CHECK (backend IN ('mongo', 'postgres')),
  epoch      int NOT NULL CHECK (epoch >= 1),
  readonly   boolean NOT NULL DEFAULT false,
  updated_at timestamptz NOT NULL DEFAULT now());
INSERT INTO platform.store_epoch (backend, epoch) VALUES ('postgres', 1);
SELECT kernel.register_internal('platform.store_epoch');

INSERT INTO kernel.vetted_extension_reads VALUES ('platform.store_epoch') ON CONFLICT DO NOTHING;
GRANT SELECT ON platform.store_epoch TO pipeshub_vetted_kernel;
