import { canonicalDigest } from '../../../libs/utils/canonical-json';
import {
  resolvedRunConfigSchema,
  type ResolvedRunConfig as Pin,
} from '../contracts/generated';
import type { PinBasis, PinSubject } from '../domain/resolution';
import type { Heads } from '../ports/head-revisions';

/** Every member of a pin but its digest. */
export type PinFields = Omit<Pin, 'digest'>;

/** A pin's identity (02-lld §2.9): the canonical digest of every member but `digest`; Python's `pin_digest` twin. */
export const pinDigest = (fields: PinFields): `sha256:${string}` =>
  canonicalDigest(fields);

/**
 * A pin from members already typed by the contract, with its digest: the
 * admission hot path (the schema check of 200 items would cost NFR-1 a fifth
 * of its budget; the run-config-pinner suite checks the production pins).
 */
export const sealPin = (fields: PinFields): Pin => ({
  ...fields,
  digest: pinDigest(fields),
});

/** A pin from members of unchecked shape: sealed, then held to the contract. */
export const assemblePin = (fields: PinFields): Pin =>
  resolvedRunConfigSchema.parse(sealPin(fields));

/**
 * The agent's owner a pin is resolved for: a root's as its admission read the
 * definition, a child's as its parent's pin holds it. A child never re-reads
 * the definition, and its spawn has none in hand, so without this what the
 * owner shared with the agent would read `not_visible` in every child.
 */
export const pinnedAgentOwner = (
  subject: Pick<PinSubject, 'agentOwner' | 'parent'>,
): string | null =>
  subject.parent !== undefined
    ? (subject.parent.scope.agentOwner ?? null)
    : (subject.agentOwner ?? null);

/** A pin's `scope`; Python's `pin_scope` twin. `agentOwner` is absent, not null, so a pin without one keeps its digest. */
export const pinScope = (
  subject: Pick<PinSubject, 'agentKey' | 'projectId' | 'agentOwner' | 'parent'>,
): Pin['scope'] => {
  const agentOwner = pinnedAgentOwner(subject);
  return {
    agentKey: subject.agentKey,
    projectId: subject.projectId,
    ...(agentOwner !== null ? { agentOwner } : {}),
  };
};

/** The revisions a basis was loaded at: what an admission holds its pin to. */
export const basisHeads = (basis: PinBasis): Heads => ({
  org: basis.headRevision,
  deployment: basis.deploymentRevision,
});
