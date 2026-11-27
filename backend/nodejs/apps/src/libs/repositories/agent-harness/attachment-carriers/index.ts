import type { AttachmentCarriers } from '../../../../modules/agent-harness/ports/session-erasure';
import { pickHarnessStore } from '../stores';
import { HarnessAttachmentCarriers } from './mongo';
import { PgHarnessAttachmentCarriers } from './pg';

export const createAttachmentCarriers = (): AttachmentCarriers =>
  pickHarnessStore<AttachmentCarriers>('agent-harness/attachment-carriers', {
    mongo: () => new HarnessAttachmentCarriers(),
    postgres: () => new PgHarnessAttachmentCarriers(),
  });
