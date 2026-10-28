/**
 * PostgreSQL implementation of the organisation repository (`DOC_STORE=postgres`). The record is read in its own
 * organisation's unit of work; the install-wide questions (how many organisations, which is oldest) go through the identity
 * functions. Creating an organisation runs as the new organisation: its context is bound from the ids `allocateIds` chose,
 * and the administrator, the two default groups and the sign-in configuration are written beside it in the same unit.
 */
import { ValidationError } from '../../errors/validation.error';
import {
  runWithOrgContext,
  makeOrgContext,
  currentOrgContextOrSystem,
} from '../../context/orgContext';
import { getIdentityPort } from '../identity';
import { stripNul } from '../nul';
import { assertOrg, inUnit, newObjectId } from '../pg-util';
import { nextSlug } from '../slug/pg';
import type {
  NewOrgIds,
  NewOrgWithAdmin,
  OrgDto,
  OrgLogoDto,
  OrgPatch,
  OrgRepository,
} from './types';
import type { Tx } from '../uow';

interface OrgRow {
  org_id: string;
  slug: string;
  registered_name: string | null;
  short_name: string | null;
  domain: string;
  contact_email: string;
  account_type: 'individual' | 'business';
  phone_number: string | null;
  permanent_address: Record<string, unknown> | null;
  on_boarding_status: string | null;
  is_deleted: boolean;
  deleted_by_user: string | null;
  admin_role_guard_at: Date | null;
  signup_channel: string | null;
  signup_referrer_domain: string | null;
  version: number;
  created_at: Date;
  updated_at: Date;
}

const COLS =
  'o.org_id, o.slug, o.registered_name, o.short_name, o.domain, o.contact_email, o.account_type, o.phone_number, o.permanent_address, o.on_boarding_status, o.is_deleted, o.deleted_by_user, o.admin_role_guard_at, o.signup_channel, o.signup_referrer_domain, o.version, o.created_at, o.updated_at';

function dto(r: OrgRow): OrgDto {
  const o: Record<string, unknown> = {
    _id: r.org_id,
    slug: r.slug,
    domain: r.domain,
    contactEmail: r.contact_email,
    accountType: r.account_type,
    isDeleted: r.is_deleted,
    createdAt: r.created_at,
    updatedAt: r.updated_at,
    __v: r.version,
  };
  if (r.registered_name !== null) o.registeredName = r.registered_name;
  if (r.short_name !== null) o.shortName = r.short_name;
  if (r.phone_number !== null) o.phoneNumber = r.phone_number;
  if (r.permanent_address !== null) o.permanentAddress = r.permanent_address;
  if (r.on_boarding_status !== null) o.onBoardingStatus = r.on_boarding_status;
  if (r.deleted_by_user !== null) o.deletedByUser = r.deleted_by_user;
  if (r.admin_role_guard_at !== null)
    o.adminRoleGuardAt = r.admin_role_guard_at;
  if (r.signup_channel !== null) o.signupChannel = r.signup_channel;
  if (r.signup_referrer_domain !== null)
    o.signupReferrerDomain = r.signup_referrer_domain;
  return o as unknown as OrgDto;
}

const str = (v: unknown): string | null => (typeof v === 'string' ? v : null);

export class PgOrgRepository implements OrgRepository {
  async count(): Promise<number> {
    return getIdentityPort().orgCount(false);
  }

  async isActive(orgId: string): Promise<boolean> {
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query(
        '/* pipeshub:identity.org_is_active */ SELECT 1 FROM identity.orgs o WHERE o.org_id = $1 AND NOT o.is_deleted',
        [k.orgId],
      );
      return rows.length > 0;
    });
  }

  async liveSummary(): Promise<{ count: number; oldestId: string | null }> {
    const port = getIdentityPort();
    return {
      count: await port.orgCount(true),
      oldestId: await port.oldestOrgId(),
    };
  }

  allocateIds(): NewOrgIds {
    return { orgId: newObjectId(), adminUserId: newObjectId() };
  }

  async getLive(orgId: string): Promise<OrgDto | null> {
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<OrgRow>(
        `/* pipeshub:identity.org_get_live */ SELECT ${COLS} FROM identity.orgs o WHERE o.org_id = $1 AND NOT o.is_deleted`,
        [k.orgId],
      );
      return rows[0] ? dto(rows[0]) : null;
    });
  }

  /** The install's oldest live organisation, read as that organisation (single-organisation sign-in pages). */
  async getFirstLive(): Promise<OrgDto | null> {
    const id = await getIdentityPort().oldestOrgId();
    if (id === null) return null;
    const outer = (() => {
      try {
        return currentOrgContextOrSystem();
      } catch {
        return undefined;
      }
    })();
    // a request for another organisation asking for "the" organisation is a mistake, never a read across
    if (outer?.orgId && outer.orgId !== id) return null;
    return runWithOrgContext(
      makeOrgContext({
        orgId: id,
        userId: null,
        kind: 'job',
        source: 'job:first-org',
      }),
      () => this.getLive(id),
    );
  }

  private async column<T>(
    orgId: string,
    tag: string,
    cols: string,
    live = true,
  ): Promise<T | null> {
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<T>(
        `/* pipeshub:identity.${tag} */ SELECT ${cols} FROM identity.orgs o WHERE o.org_id = $1${live ? ' AND NOT o.is_deleted' : ''}`,
        [k.orgId],
      );
      return rows[0] ?? null;
    });
  }

  async getAccountType(
    orgId: string,
  ): Promise<Pick<OrgDto, 'accountType'> | null> {
    const r = await this.column<{ account_type: OrgDto['accountType'] }>(
      orgId,
      'org_account_type',
      'o.account_type',
    );
    return r ? { accountType: r.account_type } : null;
  }

  async getNames(
    orgId: string,
  ): Promise<Pick<OrgDto, 'registeredName' | 'shortName'> | null> {
    const r = await this.column<{
      registered_name: string | null;
      short_name: string | null;
    }>(orgId, 'org_names', 'o.registered_name, o.short_name');
    if (!r) return null;
    return {
      ...(r.registered_name !== null && { registeredName: r.registered_name }),
      ...(r.short_name !== null && { shortName: r.short_name }),
    };
  }

  async getOnboardingStatus(
    orgId: string,
  ): Promise<Pick<OrgDto, 'onBoardingStatus'> | null> {
    const r = await this.column<{ on_boarding_status: string | null }>(
      orgId,
      'org_onboarding',
      'o.on_boarding_status',
    );
    return r
      ? {
          ...(r.on_boarding_status !== null && {
            onBoardingStatus: r.on_boarding_status,
          }),
        }
      : null;
  }

  async update(orgId: string, patch: OrgPatch): Promise<OrgDto | null> {
    const p = stripNul(patch, 'org', 'patch');
    return inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      const sets: string[] = [];
      const values: unknown[] = [k.orgId];
      const set = (col: string, v: unknown, cast = ''): void => {
        values.push(v);
        sets.push(`${col} = $${String(values.length)}${cast}`);
      };
      if (p.contactEmail !== undefined)
        set('contact_email', p.contactEmail.toLowerCase());
      if (p.registeredName !== undefined)
        set('registered_name', p.registeredName);
      if (p.shortName !== undefined) set('short_name', p.shortName);
      if (p.permanentAddress !== undefined)
        set('permanent_address', JSON.stringify(p.permanentAddress), '::jsonb');
      set('updated_at', new Date());
      const { rows } = await k.query<OrgRow>(
        `/* pipeshub:identity.org_update */ UPDATE identity.orgs o SET ${sets.join(', ')} WHERE o.org_id = $1 RETURNING ${COLS}`,
        values,
      );
      return rows[0] ? dto(rows[0]) : null;
    });
  }

  async setOnboardingStatus(
    orgId: string,
    status: string,
  ): Promise<OrgDto | null> {
    return inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<OrgRow>(
        `/* pipeshub:identity.org_set_onboarding */ UPDATE identity.orgs o SET on_boarding_status = $2, updated_at = $3 WHERE o.org_id = $1 AND NOT o.is_deleted RETURNING ${COLS}`,
        [k.orgId, status, new Date()],
      );
      return rows[0] ? dto(rows[0]) : null;
    });
  }

  async softDelete(orgId: string): Promise<OrgDto | null> {
    return inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<OrgRow>(
        `/* pipeshub:identity.org_soft_delete */ UPDATE identity.orgs o SET is_deleted = true, updated_at = $2 WHERE o.org_id = $1 AND NOT o.is_deleted RETURNING ${COLS}`,
        [k.orgId, new Date()],
      );
      return rows[0] ? dto(rows[0]) : null;
    });
  }

  async createWithAdmin(
    input: NewOrgWithAdmin,
    tx?: Tx,
  ): Promise<{
    org: OrgDto;
    adminUserId: string;
    adminEmail: string;
  }> {
    const o = stripNul(input.org, 'org', 'org');
    const accountType = str(o.accountType);
    if (accountType !== 'individual' && accountType !== 'business')
      throw new ValidationError(
        'accountType must be individual or business',
        [],
      );
    if (accountType === 'business' && !str(o.registeredName))
      throw new ValidationError(
        'Registered Name is required for business accounts',
        [],
      );
    const domain = str(o.domain);
    const contactEmail = str(o.contactEmail);
    if (domain === null || contactEmail === null)
      throw new ValidationError('domain and contactEmail are required', []);
    const adminEmail = stripNul(
      input.admin.email,
      'org',
      'email',
    ).toLowerCase();
    const adminName = stripNul(input.admin.fullName, 'org', 'name').trim();
    const { orgId, adminUserId } = input.ids;
    const ip = input.ipAddress;
    if (ip === undefined || ip === '')
      throw new ValidationError('ipAddress is required', []);

    const org = await inUnit(tx, { write: true }, async (k) => {
      assertOrg(k, orgId);
      const now = new Date();
      await k.query(
        `/* pipeshub:identity.org_create */ INSERT INTO identity.orgs
          (org_id, registered_name, short_name, domain, contact_email, account_type, phone_number, permanent_address, on_boarding_status, signup_channel, signup_referrer_domain, created_at, updated_at)
          VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $11, $12, $10, $10)`,
        [
          orgId,
          str(o.registeredName),
          str(o.shortName),
          domain,
          contactEmail.toLowerCase(),
          accountType,
          str(o.phoneNumber),
          o.permanentAddress === undefined || o.permanentAddress === null
            ? null
            : JSON.stringify(o.permanentAddress),
          str(o.onBoardingStatus),
          now,
          str(o.signupChannel),
          str(o.signupReferrerDomain),
        ],
      );
      await k.query(
        `/* pipeshub:identity.auth_config_create */ INSERT INTO identity.org_auth_config (org_id, id, auth_steps, created_at, updated_at)
          VALUES ($1, $2, '[{"order": 1, "allowedMethods": [{"type": "password"}]}]'::jsonb, $3, $3)`,
        [orgId, newObjectId(), now],
      );
      const groups = [
        {
          id: newObjectId(),
          name: 'everyone',
          type: 'everyone',
          slug: await nextSlug(k, 'UserGroup'),
        },
        {
          id: newObjectId(),
          name: 'standard',
          type: 'standard',
          slug: await nextSlug(k, 'UserGroup'),
        },
      ];
      await k.query(
        `/* pipeshub:identity.groups_create */ INSERT INTO identity.user_groups (org_id, id, slug, name, type, created_at, updated_at)
          SELECT $1, g.id, g.slug, g.name, g.type, $5::timestamptz, $5::timestamptz FROM unnest($2::text[], $3::text[], $4::text[], $6::text[]) AS g(id, slug, name, type)`,
        [
          orgId,
          groups.map((g) => g.id),
          groups.map((g) => g.slug),
          groups.map((g) => g.name),
          now,
          groups.map((g) => g.type),
        ],
      );
      await k.query(
        `/* pipeshub:identity.admin_create */ INSERT INTO identity.users (org_id, id, slug, full_name, email, role, created_at, updated_at)
          VALUES ($1, $2, $3, $4, $5, 'admin', $6, $6)`,
        [
          orgId,
          adminUserId,
          await nextSlug(k, 'User'),
          adminName === '' ? null : adminName,
          adminEmail,
          now,
        ],
      );
      // the administrator's password, in this unit: created with the organisation or not at all
      await k.query(
        '/* pipeshub:identity.credential_first_password */ INSERT INTO identity.user_credentials (org_id, user_id, password_hash, ip_address) VALUES ($1, $2, $3, $4)',
        [
          orgId,
          adminUserId,
          stripNul(input.passwordHash, 'org', 'password'),
          ip,
        ],
      );
      await k.query(
        '/* pipeshub:identity.group_member_add */ INSERT INTO identity.user_group_members (org_id, group_id, user_id, pos) VALUES ($1, $2, $3, 0)',
        [orgId, groups[0]?.id, adminUserId],
      );
      const { rows } = await k.query<OrgRow>(
        `/* pipeshub:identity.org_get */ SELECT ${COLS} FROM identity.orgs o WHERE o.org_id = $1`,
        [orgId],
      );
      return dto(rows[0] as OrgRow);
    });
    return { org, adminUserId, adminEmail };
  }

  async getLogo(orgId: string): Promise<OrgLogoDto | null> {
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<LogoRow>(
        '/* pipeshub:identity.logo_get */ SELECT l.id, l.logo, l.logo_url, l.mime_type, l.version FROM identity.org_logos l WHERE l.org_id = $1',
        [k.orgId],
      );
      return rows[0] ? logo(rows[0], k.orgId) : null;
    });
  }

  async setLogo(
    orgId: string,
    logoData: string,
    mimeType: string,
  ): Promise<void> {
    await inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      await k.query(
        `/* pipeshub:identity.logo_set */ INSERT INTO identity.org_logos (org_id, id, logo, mime_type) VALUES ($1, $2, $3, $4)
          ON CONFLICT (org_id) DO UPDATE SET logo = excluded.logo, mime_type = excluded.mime_type`,
        [k.orgId, newObjectId(), stripNul(logoData, 'org', 'logo'), mimeType],
      );
    });
  }

  async removeLogo(orgId: string): Promise<OrgLogoDto | null> {
    return inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<LogoRow>(
        '/* pipeshub:identity.logo_remove */ UPDATE identity.org_logos l SET logo = NULL, mime_type = NULL WHERE l.org_id = $1 RETURNING l.id, l.logo, l.logo_url, l.mime_type, l.version',
        [k.orgId],
      );
      return rows[0] ? logo(rows[0], k.orgId) : null;
    });
  }
}

interface LogoRow {
  id: string;
  logo: string | null;
  logo_url: string | null;
  mime_type: string | null;
  version: number;
}

function logo(r: LogoRow, orgId: string): OrgLogoDto & { __v: number } {
  return {
    _id: r.id,
    orgId,
    logo: r.logo,
    ...(r.logo_url !== null && { logoUrl: r.logo_url }),
    mimeType: r.mime_type,
    __v: r.version,
  };
}
