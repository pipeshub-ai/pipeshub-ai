import type { AgentRunDefinition } from './agent-definition';
import type { SubAgentDefinition } from './child-runs';
import { MIN_MAP_DEFAULT, minMapBound } from './policy/orders';
import { machineName } from './run-spec';

/** `<provider>:<model>` as the deployment configures the provider (its machine name). */
export const definitionRoute = (provider: string, model: string): string =>
  `${machineName(provider, 'unknown')}:${model}`;

/** The model a role set for a child, and the role key that named it. */
export interface ChildModel {
  provider: string;
  model: string;
  role: string;
}

const OUTSIDE_KEY = /[^A-Za-z0-9_.-]+/g;
const LEADING = /^[^a-z0-9]+/;
const ROLE_KEY_LENGTH = 64;

const roleKey = (name: string | undefined): string =>
  (name ?? '')
    .replace(OUTSIDE_KEY, '-')
    .replace(/[A-Z]/g, (letter) => letter.toLowerCase())
    .replace(LEADING, '')
    .slice(0, ROLE_KEY_LENGTH);

/**
 * The keys a spawn's sub-agent is looked up under, nearest first: the domain
 * agent it runs as, its role, then `default` (MA1 §2; `child-model.json`).
 */
export function roleKeysOf(
  subAgent: SubAgentDefinition | null | undefined,
): string[] {
  const keys = [
    roleKey(subAgent?.domainAgent),
    roleKey(subAgent?.role),
    MIN_MAP_DEFAULT,
  ];
  return [...new Set(keys.filter((key) => key.length > 0))];
}

/** The definition's role models as routes, by role key; empty when it sets none. */
export function roleRoutesOf(
  definition: Pick<AgentRunDefinition, 'roleModels'> | null,
): Record<string, string> {
  return Object.fromEntries(
    Object.entries(definition?.roleModels ?? {}).map(([role, model]) => [
      role,
      definitionRoute(model.provider, model.modelName),
    ]),
  );
}

/** The model the first of the spawn's role keys names; null: the child calls its parent's. */
export function childModelOf(
  roleRoutes: Readonly<Record<string, string>>,
  subAgent: SubAgentDefinition | null | undefined,
): ChildModel | null {
  for (const role of roleKeysOf(subAgent)) {
    const route = Object.prototype.hasOwnProperty.call(roleRoutes, role)
      ? roleRoutes[role]
      : undefined;
    if (route === undefined) continue;
    const colon = route.indexOf(':');
    return {
      provider: route.slice(0, colon),
      model: route.slice(colon + 1),
      role,
    };
  }
  return null;
}

/**
 * The cost ceiling of a spawn: the least bound `models.roleCostCeilings`
 * holds for any of its role keys (`default` bounds every role); null: none.
 */
export function roleCostBound(
  ceilings: unknown,
  roleKeys: readonly string[],
): number | null {
  const bounds = [...roleKeys, MIN_MAP_DEFAULT]
    .map((key) => minMapBound(ceilings, key))
    .filter((bound): bound is number => bound !== null);
  return bounds.length === 0 ? null : Math.min(...bounds);
}
