import type { CapabilityProbe } from '../../../../modules/agent-harness/ports/capability-probe';
import { docStore } from '../../store-select';
import { JournalDurabilityProbe } from './mongo';
import { PgJournalDurabilityProbe } from './pg';

/** The journal's durability probe for the selected document store. */
export const createJournalDurabilityProbe = (): CapabilityProbe =>
  docStore() === 'postgres'
    ? new PgJournalDurabilityProbe()
    : new JournalDurabilityProbe();
