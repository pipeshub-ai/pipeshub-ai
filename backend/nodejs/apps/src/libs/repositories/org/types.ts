/**
 * Org repository: the organisation record, its logo, and the one use case that
 * creates an organisation together with its first administrator.
 *
 * Rows leave as plain objects with string ids. "Live" means not removed.
 */
import type { Address } from '../../utils/address.utils';
import type { Tx } from '../uow';

export type AccountType = 'individual' | 'business';
export type OnboardingStatus = 'configured' | 'notConfigured' | 'skipped';

export interface OrgDto {
  _id: string;
  slug?: string;
  registeredName?: string;
  shortName?: string;
  domain: string;
  contactEmail: string;
  accountType: AccountType;
  phoneNumber?: string;
  permanentAddress?: Address;
  onBoardingStatus?: string;
  isDeleted: boolean;
  deletedByUser?: string;
  adminRoleGuardAt?: Date;
  createdAt?: Date;
  updatedAt?: Date;
  __v?: number;
}

/** Fields the organisation's admin may change. */
export type OrgPatch = Partial<
  Pick<OrgDto, 'contactEmail' | 'registeredName' | 'shortName'>
> & { permanentAddress?: string };

export interface OrgLogoDto {
  _id: string;
  orgId?: string;
  logo?: string | null;
  logoUrl?: string | null;
  mimeType?: string | null;
}

/** The ids of an organisation and its first administrator, chosen before the creation unit starts (it runs as that organisation). */
export interface NewOrgIds {
  orgId: string;
  adminUserId: string;
}

export interface NewOrgWithAdmin {
  /** From `allocateIds`. */
  ids: NewOrgIds;
  /** Organisation fields as the caller supplied them, plus the derived domain and onboarding status. */
  org: Record<string, unknown>;
  admin: { fullName: string; email: string };
  passwordHash: string;
  ipAddress: string | undefined;
}

export interface OrgRepository {
  count(): Promise<number>;
  getLive(orgId: string): Promise<OrgDto | null>;
  /** Whether the organisation exists and is not removed (a row without the flag counts as live). */
  isActive(orgId: string): Promise<boolean>;
  /** The first live organisation (single-organisation deployments). */
  getFirstLive(opts?: { hydrate?: boolean }): Promise<OrgDto | null>;
  /** How many organisations are live (a store may stop counting at 2) and the oldest: the single-organisation answer is `count === 1`. */
  liveSummary(): Promise<{ count: number; oldestId: string | null }>;
  /** Fresh ids for `createWithAdmin`; the caller binds the new organisation's context with them before it opens its unit of work. */
  allocateIds(): NewOrgIds;
  getAccountType(orgId: string): Promise<Pick<OrgDto, 'accountType'> | null>;
  /** The organisation's display names (consent pages). */
  getNames(
    orgId: string,
  ): Promise<Pick<OrgDto, 'registeredName' | 'shortName'> | null>;
  getOnboardingStatus(
    orgId: string,
  ): Promise<Pick<OrgDto, 'onBoardingStatus'> | null>;
  /** Writes the given fields; null when there is no organisation with this id. */
  update(orgId: string, patch: OrgPatch): Promise<OrgDto | null>;
  /** Null when the organisation is not live. */
  setOnboardingStatus(orgId: string, status: string): Promise<OrgDto | null>;
  /** Marks the live organisation removed; null when it is not live. */
  softDelete(orgId: string): Promise<OrgDto | null>;
  /**
   * Creates the organisation, its administrator (with a password), the
   * "everyone" and "standard" groups and the default sign-in configuration.
   * The slug is assigned when the organisation row is written, last. Pass the
   * `tx` of a unit of work to make the writes atomic where the store can.
   */
  createWithAdmin(
    input: NewOrgWithAdmin,
    tx?: Tx,
  ): Promise<{
    org: OrgDto;
    adminUserId: string;
    adminEmail: string;
  }>;

  getLogo(orgId: string): Promise<OrgLogoDto | null>;
  setLogo(orgId: string, logo: string, mimeType: string): Promise<void>;
  /** Empties the logo but keeps the row; null when the organisation never had one. */
  removeLogo(orgId: string): Promise<OrgLogoDto | null>;
}
