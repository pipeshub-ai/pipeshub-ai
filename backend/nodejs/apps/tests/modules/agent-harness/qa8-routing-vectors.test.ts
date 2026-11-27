/**
 * QA-8 RTE-21: `contracts/harness/vectors/routing.json` is the one statement
 * of routing. The file is total and self-consistent (QA8-1), and LAT1's
 * adapter answers it end to end through admission (QA8-2, QA8-3), and so
 * does CPD-10's, the resolved run configuration.
 */
import { expect } from 'chai';
import fc from 'fast-check';
import { propertyRuns } from '../../support/harness/property-profile';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { RunAdmissionScheduledRuns } from '../../../src/modules/agent-harness/application/scheduled-run-admission';
import { legacyFor } from '../../../src/modules/agent-harness/domain/rollout';
import { RoutingSubject } from '../../../src/modules/agent-harness/ports/routing-decider';
import { fixedRollout } from './support/rollout';
import {
  DURABLE_ASSISTANT_CHAT_MODES,
  DURABLE_CHAT_MODES,
} from '../../../src/modules/agent-harness/domain/rollout';
import {
  arbitraryInput,
  inputOf,
  isValidInput,
  levelsOf,
  resolvedRoutingFromWorld,
  rolloutRoutingFromWorld,
  ROUTING_VECTORS,
  routingVectorSuite,
  tableAnswer,
} from './support/routing-vectors';

describe('QA-8: the routing vector file', () => {
  it('QA8-1: every named vector is a valid input whose expected answer is the table’s', () => {
    for (const vector of ROUTING_VECTORS.vectors) {
      const input = inputOf(vector.input);
      expect(isValidInput(input), vector.id).to.equal(true);
      expect(tableAnswer(input)?.answer, vector.id).to.deep.equal(vector.expected);
    }
    expect(new Set(ROUTING_VECTORS.vectors.map((v) => v.id)).size).to.equal(ROUTING_VECTORS.vectors.length);
  });

  it('QA8-1: every rule is the answer of some named vector', () => {
    const used = new Set(ROUTING_VECTORS.vectors.map((v) => tableAnswer(inputOf(v.input))?.rule));
    expect(ROUTING_VECTORS.rules.map((r) => r.id).filter((id) => !used.has(id))).to.deep.equal([]);
  });

  it('QA8-3: the table answers every valid input', () => {
    fc.assert(
      fc.property(arbitraryInput, (input) => {
        expect(tableAnswer(input), JSON.stringify(input)).to.not.equal(null);
      }),
      propertyRuns(2_000),
    );
  });

  it('QA8-5: the table’s durable modes are the product’s', () => {
    const durableFor = (kind: 'agent' | 'assistant') =>
      levelsOf('chatMode')
        .filter((chatMode) => tableAnswer(inputOf({ kind, chatMode }))?.answer.route === 'durable')
        .sort();
    expect(durableFor('agent')).to.deep.equal([...DURABLE_CHAT_MODES].sort());
    expect(durableFor('assistant')).to.deep.equal([...DURABLE_ASSISTANT_CHAT_MODES].sort());
  });
});

routingVectorSuite('today’s rollout and the conversation’s placement (RolloutRoutingDecider)', rolloutRoutingFromWorld);
// RTE-21, the second adapter (CPD-10): the same vectors and the same 200 random inputs, unchanged.
routingVectorSuite('the resolved run configuration', resolvedRoutingFromWorld);

describe('QA-8 QA8-9 (ADM-25): every admission path asks the one RoutingDecider', () => {
  const spied = () => {
    const seen: RoutingSubject[] = [];
    const admissions = new RunAdmissionService({
      store: new InMemoryExecutionStore({ now: () => 1_000 }),
      rollout: fixedRollout(),
      routing: {
        decide: async (subject) => {
          seen.push(subject);
          return { routing: legacyFor('no_live_worker'), scope: {} };
        },
      },
      clock: { now: () => 1_000 },
    });
    return { seen, admissions };
  };
  const input = { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } };

  it('a schedule’s firing: asked as scheduled, and its legacy answer refuses the firing', async () => {
    const { seen, admissions } = spied();
    const outcome = await new RunAdmissionScheduledRuns(admissions).admit({
      orgId: 'org-1',
      actorId: 'user-1',
      agentKey: 'agent-1',
      idempotencyKey: 'firing-1',
      deadlineMs: 60_000,
      input,
      authorization: 'Bearer t',
      trigger: { scheduleId: 's-1', firingId: 'f-1', slotKey: 'slot-1' },
    });
    expect(outcome).to.deep.equal({ kind: 'refused', reason: 'rollout_legacy' });
    expect(seen).to.have.length(1);
    expect(seen[0]).to.deep.include({ agentKey: 'agent-1', scheduled: true });
  });

  it('a regenerate: asked with the conversation it regenerates in', async () => {
    const { seen, admissions } = spied();
    const outcome = await admissions.submit({
      orgId: 'org-1',
      actorId: 'user-1',
      sessionId: 'session-1',
      idempotencyKey: 'regen-1',
      deadlineMs: 60_000,
      input,
      regenerate: { messageId: 'm-1' },
    });
    expect(outcome.route).to.equal('legacy');
    expect(seen).to.have.length(1);
    expect(seen[0]).to.deep.include({ sessionId: 'session-1' });
  });
});
