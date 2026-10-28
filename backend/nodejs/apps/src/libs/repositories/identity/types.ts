/**
 * Identity port: what happens before an organisation is known, and what belongs to a
 * person rather than to one organisation (the sign-in secrets and counters).
 *
 * Every question here answers with the least that lets the caller continue. The sign-in
 * lookup returns ids only; the caller then reads the account inside that organisation's own
 * unit of work. Nothing here lists or writes accounts outside the org the unit runs as.
 */
import type { CredentialRepository } from '../credential/types';

/** An attempt made before the person is known to an organisation (a code request, a wrong code). */
export interface PreOrgActivity {
  email: string;
  activityType: string;
  loginMode?: string;
  ipAddress: string;
}

export interface IdentityPort extends CredentialRepository {
  /** The oldest organisation that is not removed, where instance-wide clients and the single-organisation sign-in pages live. */
  oldestOrgId(): Promise<string | null>;
  /** How many organisations the install has (removed ones too unless `liveOnly`). */
  orgCount(liveOnly: boolean): Promise<number>;
  /** Records an attempt before the person is known to an organisation. */
  recordPreOrgActivity(entry: PreOrgActivity): Promise<void>;
}
