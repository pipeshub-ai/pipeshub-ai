import type { AgentNameReader } from '../ports/agent-names';
import type { GroupNameReader } from '../ports/group-access';
import {
  harnessLogMeta,
  type HarnessLogger,
  silentHarnessLogger,
} from './harness-log';

/** Display names for the ids an admin answer holds, by kind and id; an id nobody could name is absent. */
export interface HeldNames {
  agents: Record<string, string>;
  [kind: string]: Record<string, string>;
}

type NameKind = 'agents' | 'groups';

/**
 * Names for what an admin answer holds by key or id (ADR-UI-03): one batched
 * lookup per kind per answer, whatever its length, so the page never names
 * them one request per id. A failed lookup is logged and answers no names: the
 * answer is still given, and the page shows what it shows for a name it lacks.
 */
export class AdminListNames {
  private readonly logger: HarnessLogger;

  constructor(
    private readonly readers: {
      agents?: AgentNameReader | null;
      groups?: GroupNameReader | null;
    },
    logger?: HarnessLogger,
  ) {
    this.logger = logger ?? silentHarnessLogger;
  }

  agents(
    orgId: string,
    keys: readonly string[],
  ): Promise<Record<string, string>> {
    const reader = this.readers.agents;
    return this.named(
      'agents',
      orgId,
      keys,
      reader ? (ids) => reader.names(orgId, ids) : null,
    );
  }

  groups(
    orgId: string,
    ids: readonly string[],
  ): Promise<Record<string, string>> {
    const reader = this.readers.groups;
    return this.named(
      'groups',
      orgId,
      ids,
      reader ? (held) => reader.groupNames(orgId, held) : null,
    );
  }

  private async named(
    kind: NameKind,
    orgId: string,
    ids: readonly string[],
    read: ((ids: string[]) => Promise<Map<string, string>>) | null,
  ): Promise<Record<string, string>> {
    const held = [...new Set(ids)];
    if (read === null || held.length === 0) return {};
    try {
      return Object.fromEntries(await read(held));
    } catch (error) {
      this.logger.warn(
        'admin.names_failed',
        harnessLogMeta('admin.names_failed', {
          orgId,
          kind,
          errorType: error instanceof Error ? error.name : typeof error,
        }),
      );
      return {};
    }
  }
}
