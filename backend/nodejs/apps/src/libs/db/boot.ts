import {
  SchemaEpochRow,
  SchemaRequirements,
  schemaProblems,
} from './schema-assert';
import { SchemaNotReady } from './errors';
import { PgRuntime } from './uow';

interface RawEpochRow {
  branch: string;
  epoch: number | string;
  min_code_epoch: number | string;
}

export async function readSchemaEpochs(
  rt: PgRuntime,
): Promise<SchemaEpochRow[]> {
  // The service login holds no privileges of its own, so this runs as the service role, like any unit.
  const r = await rt.withUow({ preOrg: true }, (tx) =>
    tx.query<RawEpochRow>(
      '/* pipeshub:kernel.schema_epoch */ SELECT branch, epoch, min_code_epoch FROM kernel.schema_epoch',
    ),
  );
  return r.rows.map((x) => ({
    branch: x.branch,
    epoch: Number(x.epoch),
    min_code_epoch: Number(x.min_code_epoch),
  }));
}

export interface WaitOptions {
  attempts?: number;
  delayMs?: number;
  sleep?: (ms: number) => Promise<void>;
}

/** Wait for migrate to finish. Throws SchemaNotReady after the last attempt; the caller decides to exit. */
export async function waitForSchema(
  rt: PgRuntime,
  requirements: SchemaRequirements,
  o: WaitOptions = {},
): Promise<void> {
  const attempts = o.attempts ?? 30;
  const sleep =
    o.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
  let problems: string[] = [];
  for (let i = 0; i < attempts; i++) {
    try {
      problems = schemaProblems(await readSchemaEpochs(rt), requirements);
      if (problems.length === 0) return;
    } catch (e) {
      problems = [`schema epoch unreadable: ${(e as Error).message}`];
    }
    if (i < attempts - 1) await sleep(o.delayMs ?? 2000);
  }
  throw new SchemaNotReady(problems);
}

/** The deployment profile epoch this process was started with; write units pass it to the fence. */
export async function readProfileEpoch(rt: PgRuntime): Promise<number> {
  const r = await rt.withUow({ preOrg: true }, (tx) =>
    tx.query<{ epoch: number | null }>(
      '/* pipeshub:platform.profile_epoch */ SELECT kernel.profile_epoch() AS epoch',
    ),
  );
  const epoch = r.rows[0]?.epoch;
  if (r.rows.length !== 1 || epoch == null)
    throw new SchemaNotReady([
      'the deployment profile must hold exactly one row',
    ]);
  return epoch;
}

/** The document store epoch this process starts with; throws SchemaNotReady unless the fence row names postgres. Write units pass it to `kernel.assert_store_epoch`. */
export async function readStoreEpoch(rt: PgRuntime): Promise<number> {
  const r = await rt.withUow({ preOrg: true }, (tx) =>
    tx.query<{ epoch: number | null }>(
      '/* pipeshub:platform.store_epoch */ SELECT kernel.postgres_store_epoch() AS epoch',
    ),
  );
  const epoch = r.rows[0]?.epoch;
  if (r.rows.length !== 1 || epoch == null)
    throw new SchemaNotReady([
      'platform.store_epoch must hold exactly one row that names postgres as the document store',
    ]);
  return epoch;
}
