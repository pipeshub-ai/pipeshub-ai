import type { ChangeEvent } from '../../../../modules/control-plane/contracts/generated';
import type { ChangeOutbox } from '../../../../modules/control-plane/ports/config-audit-log';
import { pickHarnessStore } from '../../agent-harness/stores';
import { MongoChangeOutbox } from './mongo';
import { PgChangeOutbox } from './pg';

export const createChangeOutbox = (options: {
  announce: (event: ChangeEvent) => void;
}): ChangeOutbox =>
  pickHarnessStore<ChangeOutbox>('control-plane/change-outbox', {
    mongo: () => new MongoChangeOutbox(options),
    postgres: () => new PgChangeOutbox(options),
  });
