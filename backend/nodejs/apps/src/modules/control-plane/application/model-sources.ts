import { configuredRoutes, llmRoute, selectLlm } from '../domain/llm';
import type { PolicyLayer } from '../domain/policy-alias';
import type { PinSubject, Snapshot } from '../domain/resolution';

/**
 * The models a run of the subject may call (POL-20): every configured model,
 * the host's aliases for "the configured default", and the routes the host
 * verified against another authority; narrowed by the org's stored
 * `models.routes` when it set one. Null when the deployment configures no
 * model: nothing bounds a choice that no worker can build anyway, and the
 * run fails with the worker's "no model configured" message, as before.
 */
export function allowedRoutes(
  subject: PinSubject,
  snapshot: Snapshot,
  aliases: readonly string[],
): string[] | null {
  if (snapshot.models.length === 0) return null;
  const stored = snapshot.layers.get(`org:${subject.orgId}`)?.models?.routes;
  const all = new Set([
    ...configuredRoutes(snapshot.models),
    ...aliases,
    ...(subject.verifiedRoutes ?? []),
  ]);
  return [...all]
    .filter((route) => stored === undefined || stored.includes(route))
    .sort();
}

/** The configured-models ceiling as an `org` layer, folded right after the org's stored one. */
export function modelsCeilingLayer(
  subject: PinSubject,
  snapshot: Snapshot,
  aliases: readonly string[],
): PolicyLayer | null {
  const routes = allowedRoutes(subject, snapshot, aliases);
  return routes === null
    ? null
    : { schemaVersion: 1, scope: { kind: 'org' }, models: { routes } };
}

/** The role models still allowed; undefined when none is left. */
const keptRoles = (
  roles: Readonly<Record<string, string>>,
  allowed: readonly string[],
): Record<string, string> | undefined => {
  const kept = Object.entries(roles).filter(([, route]) =>
    allowed.includes(route),
  );
  return kept.length === 0 ? undefined : Object.fromEntries(kept);
};

/**
 * The agent's definition with the models it grants narrowed to those still
 * allowed, so an agent that lists a model the deployment dropped keeps
 * running on the others. A sub-agent role whose model is not allowed is
 * dropped the same way (MA1): the agent runs, and that role's spawn is
 * refused. A definition none of whose models is allowed is left as it is:
 * the fold refuses it (a widening at `agent`).
 */
export function definitionLayer(
  subject: PinSubject,
  snapshot: Snapshot,
  aliases: readonly string[],
): PolicyLayer | null {
  const definition = subject.agent ?? null;
  const granted = definition?.models?.routes;
  const roles = definition?.models?.roles;
  if (definition === null || (granted === undefined && roles === undefined))
    return definition;
  const allowed = allowedRoutes(subject, snapshot, aliases);
  if (allowed === null) return definition;
  const kept = granted?.filter((route) => allowed.includes(route));
  if (kept?.length === 0) return definition;
  const narrowedRoles =
    roles === undefined ? undefined : keptRoles(roles, kept ?? allowed);
  if (
    kept?.length === granted?.length &&
    Object.keys(narrowedRoles ?? {}).length === Object.keys(roles ?? {}).length
  )
    return definition;
  const {
    routes: _routes,
    primary,
    roles: _roles,
    ...rest
  } = definition.models ?? {};
  return {
    ...definition,
    models: {
      ...rest,
      ...(kept !== undefined ? { routes: kept } : {}),
      ...(primary !== undefined
        ? {
            primary:
              kept === undefined || kept.includes(primary) ? primary : kept[0],
          }
        : {}),
      ...(narrowedRoles !== undefined ? { roles: narrowedRoles } : {}),
    },
  };
}

/** Whether the run names a model of its own (a key or a name), which is then looked up among the configured ones. */
export const namesModel = (subject: PinSubject): boolean =>
  subject.model !== undefined &&
  (subject.model.modelKey !== undefined ||
    subject.model.modelName !== undefined);

const verified = (subject: PinSubject): boolean => {
  const primary = subject.request.models?.primary;
  return (
    primary !== undefined && (subject.verifiedRoutes ?? []).includes(primary)
  );
};

/**
 * Whether the run names a model the deployment does not configure: a key no
 * entry has, or a name its entry does not list. Its route may still read as a
 * configured one (another entry's), so the routes ceiling cannot see it; the
 * pinner refuses it (`outside_ceiling` at `models.primary`), never another
 * model. Not asked of a route the host verified elsewhere, nor while no model
 * is configured.
 */
export const namesUnconfiguredModel = (
  subject: PinSubject,
  snapshot: Snapshot,
): boolean =>
  namesModel(subject) &&
  !verified(subject) &&
  snapshot.models.length > 0 &&
  selectLlm(snapshot.models, subject.model) === null;

/**
 * The request with the model it chose named as the deployment configures it:
 * a client names a model by key and may leave its provider or name out, so
 * its route is rewritten to the configured entry's.
 */
export function requestLayer(
  subject: PinSubject,
  snapshot: Snapshot,
): PolicyLayer {
  const { request } = subject;
  if (!namesModel(subject) || verified(subject)) return request;
  const named = selectLlm(snapshot.models, subject.model);
  return named === null || request.models?.primary === undefined
    ? request
    : {
        ...request,
        models: {
          ...request.models,
          primary: llmRoute(named.provider, named.model),
        },
      };
}
