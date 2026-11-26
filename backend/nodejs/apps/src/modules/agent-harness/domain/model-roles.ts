/**
 * GA-15 (G-27): the roles a run calls models in and which route each engine
 * role calls, folded from its pin's `models.roles`. Python folds the same
 * table (`harness_core.access.model_roles`; `contracts/harness/vectors/model-roles.json`).
 * Only the reserved keys name an engine role; any other key is a sub-agent
 * role, a child run's (MA1). A role without a route calls the primary route,
 * and so does every role of a run whose primary route is pinned to a model
 * account (A1), so nobody's account pays for another's model.
 */
import { pipeshubRunSpecExtensionSchema } from '../contracts/generated/ext/pipeshub/run-spec';
import type { RunSpec } from '../contracts/generated/core/run-spec';

export const MODEL_ROLES = [
  'primary',
  'fast',
  'subagent',
  'compaction',
] as const;
export type ModelRole = (typeof MODEL_ROLES)[number];
export const ENGINE_ROLES = ['fast', 'subagent', 'compaction'] as const;
export type EngineRole = (typeof ENGINE_ROLES)[number];

/** The id an engine names for each role; any other id is refused by the model gateway. */
export const ROLE_MODEL_IDS: Readonly<Record<ModelRole, string>> = {
  primary: 'engine-model',
  fast: 'engine-model-fast',
  subagent: 'engine-model-subagent',
  compaction: 'engine-model-compaction',
};

export const MODEL_ROLES_DIMENSION = 'models.roles';

export interface RoleRoute {
  provider: string;
  model: string;
}

export const isModelRole = (value: unknown): value is ModelRole =>
  typeof value === 'string' &&
  (MODEL_ROLES as readonly string[]).includes(value);

const splitRoute = (value: unknown): RoleRoute | null => {
  if (typeof value !== 'string') return null;
  const colon = value.indexOf(':');
  if (colon <= 0 || colon === value.length - 1) return null;
  return { provider: value.slice(0, colon), model: value.slice(colon + 1) };
};

/** Each mapped engine role's route; a role left out calls the primary route. */
export function engineRoleRoutes(
  roles: unknown,
  primaryAccount: boolean,
): Partial<Record<EngineRole, RoleRoute>> {
  if (primaryAccount || roles === null || typeof roles !== 'object') return {};
  const map = roles as Record<string, unknown>;
  const folded: Partial<Record<EngineRole, RoleRoute>> = {};
  for (const role of ENGINE_ROLES) {
    if (!Object.prototype.hasOwnProperty.call(map, role)) continue;
    const route = splitRoute(map[role]);
    if (route !== null) folded[role] = route;
  }
  return folded;
}

/**
 * The engine roles' routes of a pinned spec: its control-plane pin's
 * `models.roles`, which the pin already held within `models.routes`. A run
 * pinned without the control plane has none: every role is its primary.
 */
export function pinnedRoleRoutes(
  spec: RunSpec,
): Partial<Record<EngineRole, RoleRoute>> {
  const extension = pipeshubRunSpecExtensionSchema.safeParse(
    spec.extensions?.['x.pipeshub'],
  );
  const resolved = extension.success ? extension.data.resolved : undefined;
  const dimensions = (
    resolved as
      | {
          effectiveConfig?: {
            dimensions?: Record<string, { value?: unknown }>;
          };
        }
      | undefined
  )?.effectiveConfig?.dimensions;
  const primary = spec.model.policy[0];
  return engineRoleRoutes(
    dimensions?.[MODEL_ROLES_DIMENSION]?.value ?? null,
    primary?.access?.accountId !== undefined,
  );
}
