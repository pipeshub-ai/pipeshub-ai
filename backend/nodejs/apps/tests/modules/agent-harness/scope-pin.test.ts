import { expect } from 'chai';
import { readFileSync } from 'fs';
import { join } from 'path';
import { runSpecSchema } from '../../../src/modules/agent-harness/contracts/generated/core/run-spec';
import {
  capabilitySnapshot,
  LEAST_CAPABILITIES,
} from '../../../src/modules/agent-harness/domain/capabilities';
import {
  childFeatures,
  childGrants,
  childRunSpec,
} from '../../../src/modules/agent-harness/domain/child-runs';
import { configuredFamilies } from '../../../src/modules/agent-harness/domain/configured-llms';
import type {
  HarnessRun,
  RunInputSnapshot,
} from '../../../src/modules/agent-harness/domain/execution';
import {
  PinnedRunSpec,
  pinRunSpec,
  resolveRunSpec,
  SpecSubject,
} from '../../../src/modules/agent-harness/domain/run-spec';
import { admissionRequirements } from '../../../src/modules/agent-harness/domain/worker-compat';
import type { AgentRunDefinition } from '../../../src/modules/agent-harness/domain/agent-definition';
import type { Timeline } from '../../../src/modules/agent-harness/projection/timeline';
import {
  failedBeforeStarting,
  runFailure,
} from '../../../src/modules/agent-harness/projection/transcript';
import { agentDefinition } from './support/agent-definition';
import { goldenSpecSubject } from './support/golden-run-spec';

/**
 * SYNC4 S1/S2/S12/S13, Node's half of `contracts/harness/vectors/scope-pin.json`
 * (Python's: `tests/unit/harness/scope/test_scope_pin_vectors.py`). Node pins the
 * selection a request named below app level and a saved agent's source limits;
 * never the expanded nodes or any access result.
 *
 * Written test-first, before `origin/main` was merged: the pin had only
 * apps/kb/strictScope, so every selection case was red.
 */

type Knowledge = Record<string, unknown>;

interface PinCase {
  id: string;
  definition: { knowledge: AgentRunDefinition['knowledge'] } | null;
  filters: Record<string, unknown>;
  internalSearch?: boolean;
  pinned: Knowledge;
  feature: boolean;
}

interface ChildCase {
  id: string;
  parent: Knowledge;
  request: { apps?: string[]; kb?: string[] };
  child?: Knowledge;
  refused?: string[];
}

const table = JSON.parse(
  readFileSync(
    join(__dirname, '../../../../../../contracts/harness/vectors/scope-pin.json'),
    'utf8',
  ),
) as { pins: PinCase[]; children: ChildCase[] };

const DURABLE = capabilitySnapshot(
  { ...LEAST_CAPABILITIES, 'journal.durable': true },
  1,
);

const input = (testCase: PinCase): RunInputSnapshot => ({
  query: 'q',
  recordIds: [],
  filters: testCase.filters,
  attachments: [],
  model: {
    chatMode: testCase.definition === null ? 'internal_search' : 'quick',
  },
  ...(testCase.internalSearch === false
    ? { agentCapabilities: { internalSearch: false, webSearch: false } }
    : {}),
});

const run = (testCase: PinCase): HarnessRun =>
  ({
    runId: 'run-1',
    orgId: 'org-1',
    sessionId: 'session-1',
    actorId: 'user-1',
    ...(testCase.definition === null ? {} : { agentKey: 'agent-1' }),
    userMessageKey: 'message-1',
    idempotencyKey: 'idem-1',
    payloadHash: 'hash',
    harnessMajor: 2,
    profile: 'dispatched',
    status: 'queued',
    outcome: null,
    revision: 0,
    fence: 0,
    attemptId: null,
    workerId: null,
    leaseExpiresAt: null,
    absoluteDeadlineAt: Date.UTC(2026, 9, 1, 12),
    checkpoint: null,
    input: input(testCase),
    createdAt: Date.UTC(2026, 9, 1, 11),
    updatedAt: Date.UTC(2026, 9, 1, 11),
  }) as HarnessRun;

const definitionOf = (testCase: PinCase): AgentRunDefinition | null =>
  testCase.definition === null
    ? null
    : agentDefinition({ knowledge: testCase.definition.knowledge });

const subject = (testCase: PinCase): SpecSubject => ({
  run: run(testCase),
  agentDefinition: definitionOf(testCase),
  via: 'pat',
});

const knowledgeOf = (spec: { extensions?: Record<string, unknown> }): Knowledge =>
  (spec.extensions?.['x.pipeshub'] as { knowledge: Knowledge }).knowledge;

describe('SYNC4 scope pin (shared table scope-pin.json)', () => {
  for (const testCase of table.pins) {
    it(`S1 ${testCase.id}: pins the requested selection, never its expansion`, () => {
      const spec = resolveRunSpec(subject(testCase), DURABLE);
      expect(runSpecSchema.safeParse(spec).success).to.equal(true);
      expect(knowledgeOf(spec)).to.deep.equal(testCase.pinned);
    });

    it(`S1 ${testCase.id}: needs the knowledge_selection worker feature only when it pins one`, () => {
      const needs = admissionRequirements(
        run(testCase),
        [],
        'loop',
        null,
        null,
        false,
        false,
        false,
        false,
        false,
        definitionOf(testCase),
      );
      expect(needs.features.includes('knowledge_selection')).to.equal(
        testCase.feature,
      );
    });
  }

  const parentPin = (knowledge: Knowledge): PinnedRunSpec => {
    const pinned = pinRunSpec(goldenSpecSubject(), DURABLE);
    const spec = structuredClone(pinned.spec);
    (spec.extensions!['x.pipeshub'] as { knowledge: Knowledge }).knowledge =
      structuredClone(knowledge);
    spec.budgets = { ...spec.budgets, maxChildren: 4 };
    return { ...pinned, spec };
  };

  for (const testCase of table.children) {
    it(`S2 ${testCase.id}: a child never widens its parent's scope`, () => {
      const parent = parentPin(testCase.parent);
      const decision = childGrants(
        parent.spec,
        { knowledge: testCase.request },
        1,
      );
      if (testCase.refused !== undefined) {
        expect(decision.refusal?.dimension).to.equal('knowledge');
        expect(decision.refusal?.items).to.deep.equal(testCase.refused);
        return;
      }
      if (decision.grants === null) expect.fail('expected a grant');
      const child = childRunSpec(parent, decision.grants);
      expect(runSpecSchema.safeParse(child.spec).success).to.equal(true);
      expect(knowledgeOf(child.spec)).to.deep.equal(testCase.child);
      const selects = ['recordGroups', 'records', 'recordsExact', 'sourceLimits'].some(
        (key) => key in (testCase.child ?? {}),
      );
      expect(childFeatures(child).includes('knowledge_selection')).to.equal(
        selects,
      );
    });
  }
});

describe('SYNC4 S13: configured families read only the llm bucket (#4261)', () => {
  it('a decision model key routes as an unknown model, never by its provider', () => {
    const families = configuredFamilies({
      llm: [{ modelKey: 'chat-1', provider: 'openAI', isDefault: true }],
      decision: [{ modelKey: 'decide-1', provider: 'anthropic' }],
      embedding: [{ modelKey: 'embed-1', provider: 'gemini' }],
    });
    expect(families.byKey.get('chat-1')).to.equal('openai');
    expect(families.byKey.has('decide-1')).to.equal(false);
    expect(families.byKey.has('embed-1')).to.equal(false);
  });
});

describe("SYNC4 S5: a scope that cannot be searched ends the run with main's words", () => {
  const failed = (errorType: string, message: string): Timeline =>
    ({
      end: { status: 'failed', errorType, message },
      approvals: [],
      turns: [],
      questions: [],
      answer: null,
    }) as unknown as Timeline;

  for (const code of [
    'selection_too_large',
    'selection_not_ready',
    'selection_empty',
    'accessible_records_not_found',
  ]) {
    it(`${code}: RUN_ERROR alone, under the code, with the worker's (main's) message`, () => {
      const timeline = failed(code, `main's words for ${code}`);
      expect(runFailure(timeline)).to.deep.equal({
        code,
        message: `main's words for ${code}`,
      });
      expect(failedBeforeStarting(timeline)).to.equal(true);
    });
  }
});
