/** Model access (A1) and the coding engine (ENGPICK) as the settings pages see them. */
import type { EngineProfileNarrowing as Narrowing } from '@/lib/contracts/harness/generated/core/engine-profile-narrowing';
import type { AccessAccountKind, VendorRuleStatus } from '@/lib/contracts/harness/generated/core/enums';
import type { ModelAccessView as ModelAccessAnswer } from '@/lib/contracts/harness/generated/ext/pipeshub/api/model-access-view';
import type { RowOutcome as AnswerOutcome } from '@/lib/contracts/harness/generated/ext/pipeshub/enums';

export { ACCOUNT_KINDS } from '@/lib/contracts/harness/generated/core/enums';
export { ROW_OUTCOMES } from '@/lib/contracts/harness/generated/ext/pipeshub/enums';

export type AccountKind = AccessAccountKind;

/** A value this build does not know yet reads as `unknown`, never as a wrong known one. */
export type RowOutcome = AnswerOutcome | 'unknown';
export type RuleStatus = VendorRuleStatus | 'unknown';

export type AccessRule = Omit<ModelAccessAnswer['rules'][number], 'route' | 'status' | 'outcome'> & {
  route: string;
  status: RuleStatus;
  outcome: RowOutcome;
};

export type OrgAccessPolicy = ModelAccessAnswer['policy'];

/** A configured model as the org's policy sees it (MA1): `usdPerMTok` is null when it has no list price. */
export interface ModelChoice {
  modelKey: string;
  modelName: string;
  provider: string;
  usdPerMTok: number | null;
  allowed: boolean;
}

export type ModelAccessView = Omit<ModelAccessAnswer, 'offering' | 'rules' | 'modelChoices' | 'roleCostCeilings'> & {
  offering: string;
  rules: AccessRule[];
  /** Absent from a server older than MA1. */
  modelChoices?: ModelChoice[];
  /** USD per 1M tokens by role key; `default` bounds every role. */
  roleCostCeilings?: Record<string, number>;
};

/** One layer over an engine profile (E4a): an agent's narrowing of the release defaults. */
export type EngineProfileNarrowing = Narrowing;

export type AccountsErrorKind = 'unavailable' | 'forbidden' | 'invalid' | 'not_found' | 'conflict' | 'failed';

/** A refused or failed call, as the pages render it. `message` is the server's sentence for `invalid`. */
export interface AccountsError {
  kind: AccountsErrorKind;
  status: number | null;
  message: string | null;
}

/** One coding engine the org's ceiling allows (ENGPICK); `reason` is why it cannot be used now, lower case. */
export interface CodingEngineOption {
  kind: string;
  available: boolean;
  reason: string | null;
}

/** The coding-engine pickers' read: the allowed engines, the org's default, the person's choice. */
export interface CodingEngineView {
  engines: CodingEngineOption[];
  orgDefault: string | null;
  /** `org_default`, `none` or an engine kind. */
  choice: string;
  /** The install runs engines below the gVisor floor (the dev/test override): admins see a warning. */
  withoutGvisor: boolean;
  /**
   * R32-SECCOMP: whether the server's gVisor filters each sandbox's syscalls with its own profile too
   * (`--oci-seccomp`); false: admins see a warning naming the fix. Null: gVisor not offered, or not said.
   */
  gvisorSyscallFilter: boolean | null;
  /** DEEPROUTE: chat turns hand work bigger than a reply to the engine on their own (the org's switch, on by default). */
  autoRoute: boolean;
}
