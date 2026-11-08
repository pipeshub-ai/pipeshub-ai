/**
 * Mongo `userCredentials` documents as rows of `identity.user_credentials`: one row per account, keyed (organisation, user). A
 * credential of an account that is missing or removed has no row to hang on and is listed as skipped; so is a credential marked
 * deleted. When several credentials name one account the newest `updatedAt` wins (the others are listed as skipped).
 *
 * shortcut: "newest wins" holds inside one batch; across batches the row already written stays (the import is DO NOTHING). The bulk
 * copy offers only the winner of each account (see the migrator's credentials.ts), so a batch never has to decide.
 */
import type { Tx as KernelTx } from '../../db';
import {
  ImportStats,
  NeutralDoc,
  Row,
  emptyStats,
  idText,
  writePlan,
} from '../import';
import { bool, needId, stamps } from './import-util';

const time = (v: unknown): number =>
  typeof v === 'string' ? Date.parse(v) || 0 : 0;

/** The `identity.user_credentials` row of a credential document of the account (org, user). */
export function credentialRow(d: NeutralDoc, org: string, user: string): Row {
  return {
    org_id: org,
    user_id: user,
    password_hash: d.hashedPassword ?? null,
    wrong_count:
      typeof d.wrongCredentialCount === 'number' ? d.wrongCredentialCount : 0,
    is_blocked: bool(d.isBlocked),
    block_expires_at: d.blockExpiresAt ?? null,
    otp_hash: d.hashedOTP ?? null,
    otp_valid_until: typeof d.otpValidity === 'number' ? d.otpValidity : null,
    force_new_password: bool(d.forceNewPasswordGeneration),
    ip_address: d.ipAddress ?? null,
    ...stamps(d, needId(d, 'userCredentials')),
  };
}

export async function importCredentialDocs(
  k: KernelTx,
  collection: string,
  docs: NeutralDoc[],
): Promise<ImportStats> {
  if (collection !== 'userCredentials')
    throw new Error(
      `the credential repository does not import '${collection}'`,
    );
  const stats = emptyStats(docs.length);
  const keyed: Array<{ d: NeutralDoc; org: string; user: string }> = [];
  for (const d of docs) {
    const id = needId(d, 'userCredentials');
    const org = idText(d.orgId);
    const user = idText(d.userId);
    if (bool(d.isDeleted))
      stats.skipped.push({ id, reason: 'credential marked deleted' });
    else if (!org || !user)
      stats.skipped.push({ id, reason: 'credential without a usable account' });
    else keyed.push({ d, org, user });
  }
  const live = new Set<string>();
  if (keyed.length) {
    const { rows } = await k.query<{ org_id: string; id: string }>(
      `/* pipeshub:import.credential_users */ SELECT u.org_id, u.id FROM unnest($1::text[], $2::text[]) AS t(o, i)
         JOIN identity.users u ON u.org_id = t.o AND u.id = t.i WHERE NOT u.is_deleted`,
      [keyed.map((c) => c.org), keyed.map((c) => c.user)],
    );
    for (const r of rows) live.add(`${r.org_id}/${r.id}`);
  }
  const best = new Map<string, { d: NeutralDoc; org: string; user: string }>();
  for (const c of keyed) {
    const key = `${c.org}/${c.user}`;
    const id = String(c.d._id);
    if (!live.has(key)) {
      stats.skipped.push({
        id,
        reason: 'credential of a user that is missing or removed',
      });
      continue;
    }
    const prev = best.get(key);
    if (!prev || time(c.d.updatedAt) >= time(prev.d.updatedAt)) {
      if (prev)
        stats.skipped.push({
          id: String(prev.d._id),
          reason: `older credential of user ${c.user}`,
        });
      best.set(key, c);
    } else
      stats.skipped.push({ id, reason: `older credential of user ${c.user}` });
  }
  await writePlan(
    k,
    [
      {
        table: 'identity.user_credentials',
        rows: [...best.values()].map((c) => credentialRow(c.d, c.org, c.user)),
      },
    ],
    stats,
  );
  return stats;
}
