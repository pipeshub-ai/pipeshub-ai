import 'reflect-metadata';
import { expect } from 'chai';
import { readFileSync, writeFileSync } from 'fs';
import { join } from 'path';
import { Container } from 'inversify';
import * as composition from '../../../src/modules/agent-harness/composition';
import {
  AgentHarnessDependencies,
  bindAgentHarness,
} from '../../../src/modules/agent-harness/composition';

/**
 * WP-8: the composition root's object graph, class by class and field by
 * field, against the committed snapshot (first taken on 72672ec3a, before
 * the binder split). A change that rewires a service fails here; a deliberate
 * one regenerates the snapshot with UPDATE_COMPOSITION_GRAPH=1 and says why in
 * its commit.
 */
const SNAPSHOT = join(__dirname, 'composition-graph.snapshot.json');
const FAKE = Symbol('fake');
const MAX_DEPTH = 4;

type Graph = string | Graph[] | { [key: string]: Graph };

const fake = (name: string): object => ({ [FAKE]: name });

function describeValue(value: unknown, depth: number, path: Set<object>): Graph {
  if (value === null) return 'null';
  if (typeof value === 'function') return 'function';
  if (typeof value !== 'object') return typeof value;
  if (value === process.env) return 'process.env';
  const name = (value as Record<symbol, unknown>)[FAKE];
  if (typeof name === 'string') return `fake:${name}`;
  if (path.has(value)) return `cycle:${value.constructor?.name ?? 'Object'}`;
  if (Array.isArray(value)) {
    path.add(value);
    const items = value.map((item) => describeValue(item, depth, path));
    path.delete(value);
    return items;
  }
  const className = value.constructor?.name ?? 'Object';
  if (depth >= MAX_DEPTH || className === 'Container') return className;
  path.add(value);
  const fields: { [key: string]: Graph } = { class: className };
  for (const key of Object.keys(value).sort()) {
    fields[key] = describeValue(
      (value as Record<string, unknown>)[key],
      depth + 1,
      path,
    );
  }
  path.delete(value);
  return fields;
}

const tokens = (): string[] =>
  Object.entries(composition)
    .filter(
      ([name, value]) =>
        typeof value === 'string' && /^(AGENT_HARNESS|CONTROL_PLANE)_/.test(name),
    )
    .map(([, value]) => value as string)
    .sort();

function graphOf(container: Container): Record<string, Graph> {
  const graph: Record<string, Graph> = {};
  for (const token of tokens()) {
    if (!container.isBound(token)) continue;
    try {
      graph[token] = describeValue(container.get(token), 0, new Set());
    } catch (error) {
      graph[token] = `throws:${error instanceof Error ? error.message : String(error)}`;
    }
  }
  return graph;
}

const required = (): AgentHarnessDependencies =>
  ({
    sessions: fake('sessions'),
    conversations: fake('conversations'),
    history: fake('history'),
    agentAccess: fake('agentAccess'),
    rollout: fake('rollout'),
    authority: {
      workerCredential: fake('authority.workerCredential'),
      attemptSigningKeys: fake('authority.attemptSigningKeys'),
      attemptPublicKeys: fake('authority.attemptPublicKeys'),
    },
    keyEncryptionKey: fake('keyEncryptionKey'),
  }) as unknown as AgentHarnessDependencies;

const everything = (): AgentHarnessDependencies =>
  ({
    ...required(),
    payloads: fake('payloads'),
    gatewayTokens: {
      gatewaySigningKeys: fake('gatewayTokens.signing'),
      gatewayPublicKeys: fake('gatewayTokens.public'),
    },
    inlineClaimer: () => 'http://query',
    conversationReader: fake('conversationReader'),
    recordAccess: fake('recordAccess'),
    toolRules: fake('toolRules'),
    artifactAccess: fake('artifactAccess'),
    roles: fake('roles'),
    collaboratorTurns: fake('collaboratorTurns'),
    liveBus: fake('liveBus'),
    activity: { setting: fake('activity.setting'), bus: fake('activity.bus') },
    wakeup: fake('wakeup'),
    runReady: fake('runReady'),
    approvers: fake('approvers'),
    noticeChannels: fake('noticeChannels'),
    auditNames: { members: fake('auditNames.members'), chats: fake('auditNames.chats') },
    policyStore: fake('policyStore'),
    approvalSettings: {
      document: fake('approvalSettings.document'),
      identities: fake('approvalSettings.identities'),
    },
    modelAccounts: fake('modelAccounts'),
    codingEnginePreferences: fake('codingEnginePreferences'),
    repositoryToolsets: fake('repositoryToolsets'),
    modelAccess: {
      models: fake('modelAccess.models'),
      offering: 'managed',
    },
    capabilityProbes: [fake('capabilityProbe')],
    controlPlane: fake('controlPlane'),
    sandboxBrokerEndpoint: async () => 'http://broker',
    forks: {
      store: fake('forks.store'),
      artifacts: fake('forks.artifacts'),
      objects: fake('forks.objects'),
    },
    schedules: {
      actors: fake('schedules.actors'),
      setting: fake('schedules.setting'),
      notifier: fake('schedules.notifier'),
      store: fake('schedules.store'),
      connectorEvents: {
        consumer: () => fake('schedules.consumer'),
        streams: () => ['connector-events'],
      },
    },
    publications: {
      setting: fake('publications.setting'),
      authority: fake('publications.authority'),
    },
    memory: {
      policies: fake('memory.policies'),
      store: fake('memory.store'),
    },
    erasure: {
      conversations: fake('erasure.conversations'),
      artifacts: fake('erasure.artifacts'),
      liveBus: fake('erasure.liveBus'),
      retention: fake('erasure.retention'),
      agentErasures: fake('erasure.agentErasures'),
      agentDeletions: fake('erasure.agentDeletions'),
    },
  }) as unknown as AgentHarnessDependencies;

const SCENARIOS: Record<string, { dependencies: () => AgentHarnessDependencies; logger: boolean }> = {
  required: { dependencies: required, logger: false },
  everything: { dependencies: everything, logger: true },
};

/** Tokens a change adds before its snapshot is regenerated; everything else must match. */
const ADDED: readonly string[] = [];

function scenarioGraph(scenario: keyof typeof SCENARIOS): Record<string, Graph> {
  const { dependencies, logger } = SCENARIOS[scenario]!;
  const saved = process.env.SANDBOX_MODE;
  process.env.SANDBOX_MODE = 'broker';
  try {
    const container = new Container();
    if (logger) container.bind('Logger').toConstantValue(fake('logger'));
    bindAgentHarness(container, dependencies());
    return graphOf(container);
  } finally {
    if (saved === undefined) delete process.env.SANDBOX_MODE;
    else process.env.SANDBOX_MODE = saved;
  }
}

describe('WP-8 composition graph: every binding as on the base commit', () => {
  const current = Object.fromEntries(
    Object.keys(SCENARIOS).map((scenario) => [scenario, scenarioGraph(scenario)]),
  );

  if (process.env.UPDATE_COMPOSITION_GRAPH === '1') {
    writeFileSync(SNAPSHOT, `${JSON.stringify(current, null, 1)}\n`);
  }
  const snapshot = JSON.parse(readFileSync(SNAPSHOT, 'utf8')) as Record<
    string,
    Record<string, Graph>
  >;

  for (const [id, scenario] of [
    ['WP8-1', 'required'],
    ['WP8-2', 'everything'],
  ] as const) {
    describe(`${id}: ${scenario} dependencies`, () => {
      const before = snapshot[scenario]!;
      const after = current[scenario]!;

      it('binds the same tokens, plus only the documented additions', () => {
        const added = Object.keys(after).filter((token) => !(token in before));
        expect(added.sort()).to.deep.equal([...ADDED].sort());
        expect(Object.keys(before).filter((token) => !(token in after))).to.deep.equal([]);
      });

      for (const token of Object.keys(before)) {
        it(`${token} resolves to the same graph`, () => {
          expect(after[token]).to.deep.equal(before[token]);
        });
      }
    });
  }
});
