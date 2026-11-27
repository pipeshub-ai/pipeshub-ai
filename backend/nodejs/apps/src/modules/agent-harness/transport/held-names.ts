import { Container } from 'inversify';
import { AdminListNames } from '../application/admin-list-names';
import { AGENT_HARNESS_ADMIN_LIST_NAMES } from '../composition';

/**
 * ADR-UI-03 for a list route: the agents its answer holds, named in one batched lookup. Undefined where this
 * deployment composes no names (the page then shows the keys), so the answer keeps its shape.
 */
export async function namedAgents(
  container: Container,
  orgId: string,
  keys: ReadonlyArray<string | null | undefined>,
): Promise<
  { names: { agents: Record<string, string> } } | Record<string, never>
> {
  if (!container.isBound(AGENT_HARNESS_ADMIN_LIST_NAMES)) return {};
  const held = keys.filter(
    (key): key is string => typeof key === 'string' && key !== '',
  );
  return {
    names: {
      agents: await container
        .get<AdminListNames>(AGENT_HARNESS_ADMIN_LIST_NAMES)
        .agents(orgId, held),
    },
  };
}
