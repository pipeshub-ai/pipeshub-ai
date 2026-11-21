import type { SandboxSnapshotIndex } from '../../../../modules/agent-harness/ports/sandbox-snapshots';
import type { Tx } from '../../uow';
import { pickHarnessStore } from '../stores';
import { MongoSandboxSnapshotIndex } from './mongo';
import { PgSandboxSnapshotIndex } from './pg';

export const createSandboxSnapshotIndex = (): SandboxSnapshotIndex<Tx> =>
  pickHarnessStore<SandboxSnapshotIndex<Tx>>(
    'agent-harness/sandbox-snapshots',
    {
      mongo: () => new MongoSandboxSnapshotIndex(),
      postgres: () => new PgSandboxSnapshotIndex(),
    },
  );
