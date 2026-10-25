import {
  REMOVED_SIGN_IN_METHODS,
  RENAMED_SIGN_IN_METHODS,
} from '../../../edition';

export interface StoredAuthStep {
  order: number;
  allowedMethods: { type: string }[];
}

/** A step as read back from the database, where older documents may lack fields. */
interface SavedAuthStep {
  order?: number;
  allowedMethods?: { type: string }[];
}

export interface CleanedAuthSteps {
  steps: StoredAuthStep[];
  /** Removed methods the policy named, in the order first seen. */
  removed: string[];
  /** True when the policy differs from what was stored. */
  changed: boolean;
}

/**
 * Rewrites a stored sign-in policy without the removed methods. A policy that
 * named one also gets password sign-in, so nobody is left without a way in.
 */
export function withoutRemovedSignInMethods(
  stored: readonly SavedAuthStep[] | undefined | null,
): CleanedAuthSteps {
  const removed: string[] = [];
  const seen = new Set<string>();
  const steps: StoredAuthStep[] = [];

  const byOrder = [...(stored ?? [])].sort(
    (a, b) => (a.order ?? 0) - (b.order ?? 0),
  );
  for (const step of byOrder) {
    const allowedMethods: { type: string }[] = [];
    for (const { type: storedType } of step.allowedMethods ?? []) {
      const type = RENAMED_SIGN_IN_METHODS[storedType] ?? storedType;
      if (REMOVED_SIGN_IN_METHODS.includes(type)) {
        if (!removed.includes(type)) removed.push(type);
        continue;
      }
      // A method may appear once per policy; a rename can create a repeat.
      if (seen.has(type)) continue;
      seen.add(type);
      allowedMethods.push({ type });
    }
    if (allowedMethods.length > 0) {
      steps.push({ order: steps.length + 1, allowedMethods });
    }
  }

  const [firstStep] = steps;
  if (!firstStep) {
    steps.push({ order: 1, allowedMethods: [{ type: 'password' }] });
  } else if (removed.length > 0 && !seen.has('password')) {
    firstStep.allowedMethods.push({ type: 'password' });
  }

  const comparable = (s: readonly SavedAuthStep[] | undefined | null): string =>
    JSON.stringify(
      (s ?? []).map((step) => ({
        order: step.order,
        allowedMethods: (step.allowedMethods ?? []).map((m) => m.type),
      })),
    );
  return { steps, removed, changed: comparable(stored) !== comparable(steps) };
}
