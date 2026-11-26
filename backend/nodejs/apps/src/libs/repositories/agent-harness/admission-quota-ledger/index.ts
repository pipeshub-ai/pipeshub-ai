import { pickHarnessStore } from '../stores';
import type { AdmissionQuotaLedger } from './hold';
import { MongoAdmissionQuotaLedger } from './mongo';
import { PgAdmissionQuotaLedger } from './pg';

export { AdmissionQuotaHold } from './hold';
export type { AdmissionQuotaLedger, AdmissionQuotaMetrics } from './hold';

/** The org admission windows on the selected document store. */
export const createAdmissionQuotaLedger = (): AdmissionQuotaLedger =>
  pickHarnessStore<AdmissionQuotaLedger>(
    'agent-harness/admission-quota-ledger',
    {
      mongo: () => new MongoAdmissionQuotaLedger(),
      postgres: () => new PgAdmissionQuotaLedger(),
    },
  );
