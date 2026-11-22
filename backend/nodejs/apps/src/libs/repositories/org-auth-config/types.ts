/** Org auth config repository: which sign-in methods an organisation allows, in order of steps. */

export interface AuthStepDto {
  order: number;
  allowedMethods: Array<{ type: string }>;
}

export interface OrgAuthConfigDto {
  _id: string;
  orgId: string;
  domain?: string;
  authSteps: AuthStepDto[];
  isDeleted?: boolean;
  createdAt?: Date;
  updatedAt?: Date;
  __v?: number;
}

export interface OrgAuthConfigRepository {
  /** The organisation's config whether or not it was deleted. */
  get(orgId: string): Promise<OrgAuthConfigDto | null>;
  getLive(orgId: string): Promise<OrgAuthConfigDto | null>;
  /** The first live config of any organisation (single-organisation sign-in pages). */
  getFirstLive(): Promise<OrgAuthConfigDto | null>;
  anyLive(): Promise<boolean>;
  /** Sign-in methods the 1.0 upgrade took out of the organisation's live policy (empty when none). */
  removedSignInMethods(orgId: string): Promise<string[]>;
  /** Whether the organisation allows password sign-in in some step. */
  allowsPassword(orgId: string): Promise<boolean>;
  /** The default config: one step, password only. Runs in the transaction when one is given. */
  createDefault(
    orgId: string,
    domain: string | undefined,
    tx?: import('../uow').Tx,
  ): Promise<void>;
  /** Replaces the steps; null when the organisation has no config. Validation errors propagate. */
  replaceSteps(
    orgId: string,
    steps: AuthStepDto[],
  ): Promise<OrgAuthConfigDto | null>;
}
