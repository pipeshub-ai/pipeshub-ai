import type { AdmissionQuotaCaps } from '../domain/admission-quotas';

/** Where admission reads an org's caps when its run has no pin (C4.10 `quotas.*`); none bound: the release's quotas. */
export interface AdmissionQuotaCapsSource {
  caps(orgId: string): Promise<AdmissionQuotaCaps>;
}
