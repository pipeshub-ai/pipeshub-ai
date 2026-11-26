import type { AccountKind, EngineProfileNarrowing } from '@/app/(main)/workspace/ai-accounts/types';

/**
 * The account kinds an agent's engine tasks may choose between: the release's
 * default `accountKinds` (E4a/A1). Plan kinds wait for A2a/A2b.
 */
export const AGENT_ACCOUNT_KINDS: readonly AccountKind[] = ['org_api_key', 'org_cloud', 'user_api_key'];

/** `any`: no narrowing (whatever the org ceiling allows); `custom`: a stored list of several kinds, kept as is. */
export type AccountChoice = 'any' | 'custom' | AccountKind;

export function accountKindChoice(profile: EngineProfileNarrowing | null | undefined): AccountChoice {
  const kinds = profile?.accountKinds;
  if (!Array.isArray(kinds) || kinds.length === 0) return 'any';
  return kinds.length === 1 ? kinds[0] : 'custom';
}

/** The agent's narrowing with its `accountKinds` set by `choice`; every other dimension is kept. */
export function withAccountKinds(
  profile: EngineProfileNarrowing,
  choice: Exclude<AccountChoice, 'custom'>,
): EngineProfileNarrowing {
  const next: EngineProfileNarrowing = { ...profile };
  if (choice === 'any') {
    delete next.accountKinds;
  } else {
    next.accountKinds = [choice];
  }
  return next;
}

/** An agent runs on an engine when its document carries an `engineProfile` object (E4a); otherwise the loop. */
export const isEngineProfile = (value: unknown): value is EngineProfileNarrowing =>
  value !== null && typeof value === 'object' && !Array.isArray(value);

/** The coding engine an agent's narrowing names (ENGPICK): `none` for a loop agent, `any` for one naming none. */
export function engineChoice(profile: EngineProfileNarrowing | null): string {
  if (profile === null) return 'none';
  const first = profile.engines?.[0];
  return first === undefined ? 'any' : (first.split('@')[0] ?? first);
}

/** The narrowing after picking `choice`: null removes the engine; an engine keeps every other dimension. */
export function withEngine(profile: EngineProfileNarrowing | null, choice: string): EngineProfileNarrowing | null {
  if (choice === 'none') return null;
  if (choice === 'any') return profile;
  return { ...(profile ?? {}), engines: [choice] };
}
