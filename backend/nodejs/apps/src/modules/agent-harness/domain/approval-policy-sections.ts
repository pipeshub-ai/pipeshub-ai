/**
 * Approval-policy sections an edition adds beside the org's rules (ADR-OWD-09,
 * amendment 2026-11-14). Each holds one value per org: in the approvals
 * document's top-level map `documentKey` (orgId -> entry, a key the edition's
 * schema overlay adds) while the KV document is the record, and in the org
 * policy layer's section `key` once the policy store is. The admin route shows
 * it as `policy[key]` and `refused[key]` and saves it with the org's rules,
 * under the same compare-and-set and audit entry; the migration copies it with
 * them. This build adds none: `HarnessEdition.approvalPolicySections` is empty.
 */
export interface ApprovalPolicySection {
  /** The value's key in the admin view's `policy` and `refused`, and the org policy layer's section. */
  readonly key: string;
  /** The approvals document's top-level map that holds each org's entry. */
  readonly documentKey: string;
  /** What an org with nothing stored shows. */
  readonly empty: unknown;
  /** A stored entry (document or layer) or a submitted value as the section holds it; null when its schema refuses it. */
  parse(raw: unknown): unknown;
  /** Whether a value stores nothing: the org's entry is removed. */
  isEmpty(value: unknown): boolean;
}

/** The view's values of the sections, by key; the generated view types name only this build's members. */
export type ApprovalPolicySectionValues = Readonly<Record<string, unknown>>;
