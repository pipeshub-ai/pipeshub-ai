import type {
  ApprovalRequest,
  Binding,
  EffectiveSet,
  Grant,
  Installation,
} from '../contracts/generated';

/** The control-plane objects of 02-lld §2, generated from contracts/control-plane. */
export type { ApprovalRequest, Binding, EffectiveSet, Grant, Installation };
export {
  approvalRequestSchema,
  bindingSchema,
  effectiveSetSchema,
  grantSchema,
  installationSchema,
} from '../contracts/generated';

export type InstallationStatus = Installation['status'];
export type GrantEntry = Grant['entries'][number];
export type BindingStatus = Binding['status'];
export type BindingMode = Binding['mode'];
export type ApprovalState = ApprovalRequest['state'];
export type ApprovalDecision = ApprovalRequest['decisions'][number];
export type ApprovalTarget = ApprovalRequest['target'];
export type ChangeKind = ApprovalRequest['change']['type'];
export type EffectiveItem = EffectiveSet['items'][number];
export type PinnedBinding = NonNullable<EffectiveItem['binding']>;
export type UnavailableReason = NonNullable<EffectiveItem['reason']>;

/** `org`, `team:<id>`, `agent:<key>`, `user:<id>` (common.schema.json `SubjectRef`). */
export type SubjectRef = Grant['subject'];
/** `org`, `team:<id>`, `user:<id>`. */
export type InstallationOwnerRef = Installation['owner'];
/** `org`, `agent:<key>`, `user:<id>`. */
export type BindingOwnerRef = Binding['owner'];

/** The maximum entries of one grant (NFR-11); the schema holds the same bound. */
export const MAX_GRANT_ENTRIES = 500;
