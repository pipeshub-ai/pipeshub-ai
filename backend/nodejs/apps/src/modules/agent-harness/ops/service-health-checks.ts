/**
 * Checks the combined services health route and the harness dependency
 * route report beside what they probe themselves: an extension point. A
 * binder adds one at boot; this build adds none.
 */
export interface ServiceHealthView {
  status: 'ok' | 'degraded';
  /** When it stopped being ok (ISO time); null while ok. */
  since?: string | null;
  /** Plain words while degraded. */
  detail?: string;
}

export interface ServiceHealthCheck {
  /** The key under `services` (and `details`) in the health answer. */
  readonly name: string;
  /** Whether the whole answer is unhealthy while this check is not ok. */
  readonly critical: boolean;
  view(): ServiceHealthView;
}

const checks = new Map<string, ServiceHealthCheck>();

/** Each added check's view, by name (the harness dependency route). */
export const serviceHealthViews = (): Record<string, ServiceHealthView> =>
  Object.fromEntries([...checks.values()].map((c) => [c.name, c.view()]));

/** Adds `check`, replacing one of the same name. */
export function addServiceHealthCheck(check: ServiceHealthCheck): void {
  checks.set(check.name, check);
}

export const serviceHealthChecks = (): readonly ServiceHealthCheck[] => [
  ...checks.values(),
];

/** Forgets every check (tests). */
export function clearServiceHealthChecks(): void {
  checks.clear();
}
