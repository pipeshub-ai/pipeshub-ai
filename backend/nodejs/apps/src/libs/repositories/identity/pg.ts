/**
 * PostgreSQL implementation of the identity port (`DOC_STORE=postgres`). The sign-in secrets and counters of an account are a
 * row of `identity.user_credentials`, read and written in the account's organisation like every other row. Before an organisation
 * is bound, the only questions are which one is the install's organisation and how many there are; both are vetted functions.
 */
import { ValidationError } from '../../errors/validation.error';
import { stripNul } from '../nul';
import { assertOrg, inPreOrgUnit, inUnit, oidOf } from '../pg-util';
import { recordBeforeOrg } from '../activity/pg';
import type { CredentialDto } from '../credential/types';
import type { IdentityPort, PreOrgActivity } from './types';

interface CredentialRow {
  user_id: string;
  password_hash: string | null;
  wrong_count: number;
  is_blocked: boolean;
  block_expires_at: Date | null;
  otp_hash: string | null;
  otp_valid_until: string | null;
  force_new_password: boolean;
  ip_address: string | null;
  created_at: Date;
  updated_at: Date;
}

const COLS =
  'c.user_id, c.password_hash, c.wrong_count, c.is_blocked, c.block_expires_at, c.otp_hash, c.otp_valid_until, c.force_new_password, c.ip_address, c.created_at, c.updated_at';

function credential(r: CredentialRow, orgId: string): CredentialDto {
  const c: Record<string, unknown> = {
    _id: r.user_id,
    userId: r.user_id,
    orgId,
    ipAddress: r.ip_address ?? '',
    forceNewPasswordGeneration: r.force_new_password,
    wrongCredentialCount: r.wrong_count,
    isBlocked: r.is_blocked,
    blockExpiresAt: r.block_expires_at,
    isDeleted: false,
    createdAt: r.created_at,
    updatedAt: r.updated_at,
  };
  if (r.otp_valid_until !== null) c.otpValidity = Number(r.otp_valid_until);
  if (r.otp_hash !== null) c.hashedOTP = r.otp_hash;
  if (r.password_hash !== null) c.hashedPassword = r.password_hash;
  return c as unknown as CredentialDto;
}

export class PgIdentityPort implements IdentityPort {
  // ---- before an organisation is bound ------------------------------------------------------------------------------------

  async oldestOrgId(): Promise<string | null> {
    return inPreOrgUnit({ write: false }, async (k) => {
      const { rows } = await k.query<{ id: string | null }>(
        '/* pipeshub:identity.oldest_org */ SELECT identity.oldest_org() AS id',
      );
      return rows[0]?.id ?? null;
    });
  }

  async orgCount(liveOnly: boolean): Promise<number> {
    return inPreOrgUnit({ write: false }, async (k) => {
      const { rows } = await k.query<{ n: string }>(
        '/* pipeshub:identity.org_count */ SELECT identity.org_count($1) AS n',
        [liveOnly],
      );
      return Number(rows[0]?.n ?? 0);
    });
  }

  recordPreOrgActivity(entry: PreOrgActivity): Promise<void> {
    return recordBeforeOrg(entry);
  }

  // ---- credentials, in the account's organisation -------------------------------------------------------------------------

  /** Runs one statement for an account; `null` when the id is not well formed (it matches nothing). */
  private async one(
    write: boolean,
    userId: string,
    orgId: string,
    text: string,
    extra: unknown[] = [],
  ): Promise<CredentialDto | null> {
    const uid = oidOf(userId);
    if (uid === undefined) return null;
    return inUnit(undefined, { write }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<CredentialRow>(text, [
        k.orgId,
        uid,
        ...extra,
      ]);
      return rows[0] ? credential(rows[0], k.orgId) : null;
    });
  }

  /** Same, answering how many rows the statement changed. */
  private async changed(
    userId: string,
    orgId: string,
    text: string,
    extra: unknown[] = [],
  ): Promise<number> {
    const uid = oidOf(userId);
    if (uid === undefined) return 0;
    return inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      return (await k.query(text, [k.orgId, uid, ...extra])).rowCount ?? 0;
    });
  }

  find(userId: string, orgId: string): Promise<CredentialDto | null> {
    return this.one(
      false,
      userId,
      orgId,
      `/* pipeshub:identity.credential_find */ SELECT ${COLS} FROM identity.user_credentials c WHERE c.org_id = $1 AND c.user_id = $2::text`,
    );
  }

  async userIdsWithPassword(
    orgId: string,
    userIds: string[],
  ): Promise<string[]> {
    const ids = userIds.flatMap((i) => oidOf(i) ?? []);
    if (ids.length === 0) return [];
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<{ user_id: string }>(
        `/* pipeshub:identity.credential_with_password */ SELECT c.user_id FROM identity.user_credentials c
          WHERE c.org_id = $1 AND c.user_id = ANY($2::text[]) AND COALESCE(c.password_hash, '') <> ''`,
        [k.orgId, ids],
      );
      return rows.map((r) => r.user_id);
    });
  }

  async clearBlock(userId: string, orgId: string): Promise<void> {
    await this.changed(
      userId,
      orgId,
      `/* pipeshub:identity.credential_clear_block */ UPDATE identity.user_credentials SET is_blocked = false, wrong_count = 0,
         block_expires_at = NULL, updated_at = now() WHERE org_id = $1 AND user_id = $2::text`,
    );
  }

  async setBlocked(userId: string, orgId: string, until: Date): Promise<void> {
    await this.changed(
      userId,
      orgId,
      `/* pipeshub:identity.credential_set_blocked */ UPDATE identity.user_credentials SET is_blocked = true, block_expires_at = $3,
         updated_at = now() WHERE org_id = $1 AND user_id = $2::text`,
      [until],
    );
  }

  reserveAttempt(
    userId: string,
    orgId: string,
    maxAttempts: number,
  ): Promise<CredentialDto | null> {
    return this.one(
      true,
      userId,
      orgId,
      `/* pipeshub:identity.credential_reserve_attempt */ UPDATE identity.user_credentials c SET wrong_count = wrong_count + 1, updated_at = now()
        WHERE c.org_id = $1 AND c.user_id = $2::text AND NOT c.is_blocked AND c.wrong_count < $3 RETURNING ${COLS}`,
      [maxAttempts],
    );
  }

  async releaseAttempt(
    userId: string,
    orgId: string,
    reservedCount: number,
  ): Promise<void> {
    await this.changed(
      userId,
      orgId,
      `/* pipeshub:identity.credential_release_attempt */ UPDATE identity.user_credentials SET wrong_count = 0, updated_at = now()
        WHERE org_id = $1 AND user_id = $2::text AND NOT is_blocked AND wrong_count = $3`,
      [reservedCount],
    );
  }

  incrementWrongCount(
    userId: string,
    orgId: string,
  ): Promise<CredentialDto | null> {
    return this.one(
      true,
      userId,
      orgId,
      `/* pipeshub:identity.credential_increment_wrong */ UPDATE identity.user_credentials c SET wrong_count = wrong_count + 1, updated_at = now()
        WHERE c.org_id = $1 AND c.user_id = $2::text RETURNING ${COLS}`,
    );
  }

  claimOtp(
    userId: string,
    orgId: string,
    hashedOTP: string,
  ): Promise<CredentialDto | null> {
    return this.one(
      true,
      userId,
      orgId,
      `/* pipeshub:identity.credential_claim_otp */ UPDATE identity.user_credentials c SET otp_hash = NULL, otp_valid_until = NULL, updated_at = now()
        WHERE c.org_id = $1 AND c.user_id = $2::text AND c.otp_hash = $3 RETURNING ${COLS}`,
      [stripNul(hashedOTP, 'identity', 'otp')],
    );
  }

  async saveOtp(
    userId: string,
    orgId: string,
    otp: { hashedOTP: string; otpValidity: number; ipAddress: string },
  ): Promise<void> {
    await this.changed(
      userId,
      orgId,
      `/* pipeshub:identity.credential_save_otp */ INSERT INTO identity.user_credentials AS c (org_id, user_id, otp_hash, otp_valid_until, ip_address)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (org_id, user_id) DO UPDATE SET otp_hash = excluded.otp_hash, otp_valid_until = excluded.otp_valid_until, updated_at = now()`,
      [
        stripNul(otp.hashedOTP, 'identity', 'otp'),
        otp.otpValidity,
        stripNul(otp.ipAddress, 'identity', 'ip'),
      ],
    );
  }

  async savePassword(
    userId: string,
    orgId: string,
    hashedPassword: string,
    ipAddress?: string,
  ): Promise<void> {
    const ip =
      ipAddress === undefined || ipAddress === ''
        ? null
        : stripNul(ipAddress, 'identity', 'ip');
    const updated = await this.changed(
      userId,
      orgId,
      `/* pipeshub:identity.credential_save_password */ UPDATE identity.user_credentials SET password_hash = $3,
         ip_address = COALESCE($4, ip_address), updated_at = now() WHERE org_id = $1 AND user_id = $2::text`,
      [hashedPassword, ip],
    );
    if (updated > 0) return;
    if (ip === null) throw new ValidationError('ipAddress is required', []);
    await this.createWithPassword({
      userId,
      orgId,
      hashedPassword,
      ipAddress: ip,
    });
  }

  async createWithPassword(input: {
    userId: string;
    orgId: string;
    hashedPassword: string;
    ipAddress: string | undefined;
  }): Promise<void> {
    if (input.ipAddress === undefined || input.ipAddress === '')
      throw new ValidationError('ipAddress is required', []);
    await this.changed(
      input.userId,
      input.orgId,
      `/* pipeshub:identity.credential_create */ INSERT INTO identity.user_credentials (org_id, user_id, password_hash, ip_address)
        VALUES ($1, $2, $3, $4)`,
      [input.hashedPassword, stripNul(input.ipAddress, 'identity', 'ip')],
    );
  }

  touchOnRefresh(
    userId: string,
    orgId: string,
    ipAddress: string | undefined,
  ): Promise<CredentialDto | null> {
    return this.one(
      true,
      userId,
      orgId,
      `/* pipeshub:identity.credential_touch */ INSERT INTO identity.user_credentials AS c (org_id, user_id, ip_address) VALUES ($1, $2, $3)
        ON CONFLICT (org_id, user_id) DO UPDATE SET ip_address = COALESCE(excluded.ip_address, c.ip_address), updated_at = now() RETURNING ${COLS}`,
      [ipAddress === undefined ? null : stripNul(ipAddress, 'identity', 'ip')],
    );
  }

  async unblock(userId: string, orgId: string): Promise<boolean> {
    return (
      (await this.changed(
        userId,
        orgId,
        `/* pipeshub:identity.credential_unblock */ UPDATE identity.user_credentials SET is_blocked = false, wrong_count = 0,
           block_expires_at = NULL, updated_at = now() WHERE org_id = $1 AND user_id = $2::text AND is_blocked`,
      )) > 0
    );
  }

  async blockedUserIds(orgId: string, among?: string[]): Promise<string[]> {
    const ids = among?.flatMap((i) => oidOf(i) ?? []);
    if (ids?.length === 0) return [];
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<{ user_id: string }>(
        `/* pipeshub:identity.credential_blocked */ SELECT c.user_id FROM identity.user_credentials c
          WHERE c.org_id = $1 AND c.is_blocked AND ($2::text[] IS NULL OR c.user_id = ANY($2::text[]))`,
        [k.orgId, ids ?? null],
      );
      return rows.map((r) => r.user_id);
    });
  }

  // These three name only the account: the organisation is the one the unit runs as.
  private async ownUnit(userId: string, text: string): Promise<void> {
    const uid = oidOf(userId);
    if (uid === undefined) return;
    await inUnit(undefined, { write: true }, (k) =>
      k.query(text, [k.orgId, uid]),
    );
  }

  clearPassword(userId: string, _email: string): Promise<void> {
    return this.ownUnit(
      userId,
      `/* pipeshub:identity.credential_clear_password */ UPDATE identity.user_credentials SET password_hash = NULL, updated_at = now()
        WHERE org_id = $1 AND user_id = $2::text`,
    );
  }

  deleteForUser(userId: string, _email: string): Promise<void> {
    return this.ownUnit(
      userId,
      `/* pipeshub:identity.credential_delete */ DELETE FROM identity.user_credentials WHERE org_id = $1 AND user_id = $2::text`,
    );
  }

  discardForUser(userId: string, email: string): Promise<void> {
    return this.deleteForUser(userId, email);
  }
}
