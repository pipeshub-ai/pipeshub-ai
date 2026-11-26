import type { Authorizer } from '../../control-plane';
import type {
  AgentPolicySections,
  PortableSections,
} from '../ports/agent-policy-sections';
import {
  agentConfigDocumentSchema,
  type AgentConfigDocument,
  type PortableAgent,
} from '../contracts/generated/ext/pipeshub';
import { redactPastedSecrets } from '../domain/pasted-secrets';

export const AGENT_CONFIG_FORMAT = 'pipeshub.agent-config';
/** What an export is written as: 2 carries the agent's own gates (WP-56). */
export const AGENT_CONFIG_SCHEMA_VERSION = 2;
/** Version 1, before an agent's own gates travelled: its agent has none, and is read as setting none. */
const AGENT_CONFIG_V1 = 1;

/** One thing the import could not bind in the importer's org, for them to bind again (C4.08). */
export interface Rebind {
  kind: string;
  name: string;
  reason: string;
  detail: string;
}

/** The query service's half of an export: the portable agent, which definition it is, what it left out. */
export interface ExportedDefinition {
  agent: PortableAgent;
  source: AgentConfigDocument['source'];
  excluded: AgentConfigDocument['excluded'];
}

export interface ImportedDefinition {
  agentKey: string;
  name: string;
  bound: Array<{ kind: string; name: string }>;
  rebind: Rebind[];
  warnings: unknown;
}

/**
 * The agents API of the query service, called with the caller's own
 * credential: it decides who may read an agent and creates the new one as
 * the caller, in the caller's org. `credential` is opaque here.
 */
export interface AgentDefinitions<C> {
  export(
    credential: C,
    agentKey: string,
    version: string | undefined,
  ): Promise<ExportedDefinition>;
  /**
   * `bind: 'sole_instance'` (an agent drafted in chat, ADR-AGB-01): an action named without an instance binds to the
   * caller's one instance of its type.
   */
  import(
    credential: C,
    agent: PortableAgent,
    options?: { bind?: 'named' | 'sole_instance' },
  ): Promise<ImportedDefinition>;
  /** Removes an agent this import created and could not finish. */
  remove(credential: C, agentKey: string): Promise<void>;
}

export interface Caller {
  orgId: string;
  userId: string;
}

export interface AgentConfigTransferDeps<C> {
  definitions: AgentDefinitions<C>;
  /** Null: this deployment keeps no agent layers, so an agent travels without policy sections. */
  policy: AgentPolicySections | null;
  authorizer: Authorizer | null;
  /** Refuses an engine profile naming an engine the org cannot use now (ENGPICK), as an agent save does. */
  assertEnginesUsable(orgId: string, engines: readonly string[]): Promise<void>;
  now: () => number;
}

export type AgentConfigRefusal =
  | 'AGENT_CONFIG_INVALID'
  | 'AGENT_CONFIG_POLICY_UNAVAILABLE'
  | 'AGENT_CONFIG_FORBIDDEN';

/** An export or import refused before anything was written (the document, or who may apply its policy). */
export class AgentConfigRefusedError extends Error {
  constructor(
    readonly code: AgentConfigRefusal,
    message: string,
    readonly field?: string,
  ) {
    super(message);
    this.name = 'AgentConfigRefusedError';
  }
}

export interface AgentImportResult {
  agentKey: string;
  name: string;
  bound: Array<{ kind: string; name: string }>;
  rebind: Rebind[];
  /** The policy sections the new agent's layer holds. */
  policy: string[];
  /** What the source agent had that the document leaves out by design: set these again. */
  excluded: AgentConfigDocument['excluded'];
  /** Policy sections the document holds that this deployment does not offer: the agent landed without them. */
  notImported: NotImportedSection[];
  warnings: unknown;
}

export interface NotImportedSection {
  section: string;
  reason: 'not_offered';
}

const policyScope = (agentKey: string): string => `agent:${agentKey}`;

/** A scope id no agent has: an import's policy is checked against the layers above before its agent exists. */
const UNCREATED_AGENT = policyScope('import-check');

/**
 * Export and import of an agent with its engine profile and its own policy
 * layer, for promotion between environments (C4.08). The agent's part is the
 * query service's (`PortableAgent`: names, never ids or secrets); this adds
 * the policy and the document around it. An import is held to the importer's
 * org: its policy is checked against the layers above before the agent is
 * created, and written as one audited control-plane write after; an agent
 * whose policy then fails to land is removed again, so an import either
 * lands whole or not at all.
 */
export class AgentConfigTransfer<C> {
  constructor(private readonly deps: AgentConfigTransferDeps<C>) {}

  async export(
    caller: Caller,
    credential: C,
    agentKey: string,
    version?: string,
  ): Promise<AgentConfigDocument> {
    const definition = await this.deps.definitions.export(
      credential,
      agentKey,
      version,
    );
    const { sections, withheld } = await this.portablePolicy(caller, agentKey);
    const excluded = [
      ...new Set([
        ...definition.excluded,
        ...(withheld.includes('approvals') ? (['approvals'] as const) : []),
      ]),
    ].sort();
    return agentConfigDocumentSchema.parse({
      format: AGENT_CONFIG_FORMAT,
      schemaVersion: AGENT_CONFIG_SCHEMA_VERSION,
      exportedAt: this.deps.now(),
      source: definition.source,
      agent: withoutPastedSecrets(definition.agent),
      policy: sections,
      excluded,
    });
  }

  async import(
    caller: Caller,
    credential: C,
    raw: unknown,
  ): Promise<AgentImportResult> {
    const document = this.parse(raw);
    const { sections, notImported } = this.offered(document.policy);
    const engines = document.agent.engineProfile?.engines ?? [];
    if (engines.length > 0) {
      await this.deps.assertEnginesUsable(caller.orgId, engines);
    }
    const policy = this.policyFor(sections);
    if (policy !== null) {
      await policy.check(caller.orgId, UNCREATED_AGENT, sections);
    }
    const created = await this.deps.definitions.import(
      credential,
      withoutPastedSecrets(document.agent),
    );
    if (policy !== null) {
      await this.landPolicy(
        policy,
        caller,
        credential,
        created.agentKey,
        sections,
      );
    }
    return {
      agentKey: created.agentKey,
      name: created.name,
      bound: created.bound,
      rebind: created.rebind,
      policy: Object.keys(sections).sort(),
      excluded: document.excluded,
      notImported,
      warnings: created.warnings,
    };
  }

  private parse(raw: unknown): AgentConfigDocument {
    const parsed = agentConfigDocumentSchema.safeParse(fromVersion1(raw));
    if (parsed.success) return parsed.data;
    const issue = parsed.error.issues[0];
    const path = issue?.path.join('.') ?? '';
    const field = path === '' ? 'document' : path;
    const version = (raw as { schemaVersion?: unknown } | null)?.schemaVersion;
    throw new AgentConfigRefusedError(
      'AGENT_CONFIG_INVALID',
      field === 'schemaVersion'
        ? `This export is schema version ${JSON.stringify(version)}; this PipesHub reads versions ${String(AGENT_CONFIG_V1)} and ${String(AGENT_CONFIG_SCHEMA_VERSION)}`
        : `This is not an agent export PipesHub can import (${field}: ${issue?.message ?? 'not valid'})`,
      field,
    );
  }

  /**
   * The document's sections this deployment offers, and the ones it does not:
   * an agent is not refused for a part this deployment has no use for; the
   * result names what was left out.
   */
  private offered(all: PortableSections): {
    sections: PortableSections;
    notImported: NotImportedSection[];
  } {
    const sections: PortableSections = {};
    const notImported: NotImportedSection[] = [];
    for (const [name, value] of Object.entries(all)) {
      const offered = this.deps.policy?.offers(name) ?? false;
      if (offered) sections[name] = value;
      else notImported.push({ section: name, reason: 'not_offered' });
    }
    notImported.sort((a, b) => a.section.localeCompare(b.section));
    return { sections, notImported };
  }

  /** The policy store the document's sections go to; null when there are none. */
  private policyFor(sections: PortableSections): AgentPolicySections | null {
    if (Object.keys(sections).length === 0) return null;
    if (this.deps.policy === null) {
      // Dropping them would leave the agent looser than the document says: refuse instead.
      throw new AgentConfigRefusedError(
        'AGENT_CONFIG_POLICY_UNAVAILABLE',
        'This deployment keeps no agent policy, so the policy in this export cannot be applied',
        'policy',
      );
    }
    return this.deps.policy;
  }

  private async landPolicy(
    policy: AgentPolicySections,
    caller: Caller,
    credential: C,
    agentKey: string,
    sections: PortableSections,
  ): Promise<void> {
    try {
      for (const section of Object.keys(sections)) {
        await this.authorize(caller, `policy.write:${section}`, agentKey);
      }
      await policy.create(
        { orgId: caller.orgId, actor: `user:${caller.userId}` },
        policyScope(agentKey),
        sections,
      );
    } catch (error) {
      await this.deps.definitions.remove(credential, agentKey);
      throw error;
    }
  }

  private async portablePolicy(
    caller: Caller,
    agentKey: string,
  ): Promise<{ sections: PortableSections; withheld: readonly string[] }> {
    if (this.deps.policy === null) return { sections: {}, withheld: [] };
    await this.authorize(caller, 'policy.read', agentKey);
    return this.deps.policy.portable(caller.orgId, policyScope(agentKey));
  }

  private async authorize(
    caller: Caller,
    action: 'policy.read' | `policy.write:${string}`,
    agentKey: string,
  ): Promise<void> {
    if (this.deps.authorizer === null) {
      throw new AgentConfigRefusedError(
        'AGENT_CONFIG_POLICY_UNAVAILABLE',
        "This deployment cannot check who may change an agent's policy",
      );
    }
    const decision = await this.deps.authorizer.check(caller, action, {
      orgId: caller.orgId,
      type: 'policy',
      owner: `agent:${agentKey}`,
    });
    if (!decision.allow) {
      throw new AgentConfigRefusedError(
        'AGENT_CONFIG_FORBIDDEN',
        "You may not set this agent's policy",
      );
    }
  }
}

/**
 * A version 1 document as version 2 reads it: its agent sets no gates. One
 * that names `gates` anyway was not written by an export of version 1 and is
 * refused, never read as none or as gates.
 */
function fromVersion1(raw: unknown): unknown {
  if (raw === null || typeof raw !== 'object') return raw;
  const document = raw as Record<string, unknown>;
  const agent = document['agent'];
  if (
    document['schemaVersion'] !== AGENT_CONFIG_V1 ||
    agent === null ||
    typeof agent !== 'object' ||
    Array.isArray(agent)
  ) {
    return raw;
  }
  if ('gates' in agent) {
    throw new AgentConfigRefusedError(
      'AGENT_CONFIG_INVALID',
      'This is not an agent export PipesHub can import (agent.gates: a version 1 export carries no gates)',
      'agent.gates',
    );
  }
  return { ...document, agent: { ...agent, gates: null } };
}

/**
 * SEC-CR-07 beside C4.08: a portable agent carries names, never secrets, so a
 * key someone pasted into its instructions or a starter prompt neither leaves
 * in an export nor lands in the importer's org; every text is checked.
 */
export function withoutPastedSecrets<T>(value: T): T {
  if (typeof value === 'string') {
    return redactPastedSecrets(value).text as T;
  }
  if (Array.isArray(value)) {
    return value.map((item: unknown) => withoutPastedSecrets(item)) as T;
  }
  if (value !== null && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).map(([key, item]) => [
        key,
        withoutPastedSecrets(item),
      ]),
    ) as T;
  }
  return value;
}
