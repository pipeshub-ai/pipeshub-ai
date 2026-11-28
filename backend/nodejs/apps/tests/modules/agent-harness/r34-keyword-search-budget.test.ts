/**
 * R33JGAPS-FU-1 (KSB-1..5): a chat turn's `keywordSearchBudget` is served by the durable route instead of sending the
 * turn to today's loop. The run input carries it while a live worker reads it (`keyword_search_budget`, guarded); a
 * worker that does not never receives the field, since an older worker's input model forbids unknown fields.
 */
import { expect } from 'chai';
import { KeywordSearchBudgetStep } from '../../../src/modules/agent-harness/application/admission/policy-steps';
import { policyContext } from '../../../src/modules/agent-harness/application/admission/context';
import { childInput } from '../../../src/modules/agent-harness/application/child-run-service';
import type { HarnessRun, RunInputSnapshot } from '../../../src/modules/agent-harness/domain/execution';
import {
  KEYWORD_SEARCH_BUDGET_FEATURE,
  withoutGuardedFields,
  workerInput,
} from '../../../src/modules/agent-harness/domain/worker-input';
import { admissionRequirements, GUARDED_WORKER_FEATURES } from '../../../src/modules/agent-harness/domain/worker-compat';
import { chatTurnInput } from '../../../src/modules/agent-harness/transport/chat-turn-route';
import { runInputSchema } from '../../../src/modules/agent-harness/transport/schemas';
import { goldenSpecSubject } from './support/golden-run-spec';
import { without } from './support/without';

const INPUT: RunInputSnapshot = { ...goldenSpecSubject().run.input!, keywordSearchBudget: 'short' };

describe('R33JGAPS-FU-1: the chat turn\'s keyword search budget on the durable path', () => {
  it('KSB-1: a turn sending it is a durable turn carrying it; a value the chat routes refuse is refused too', () => {
    const input = chatTurnInput({ query: 'q', chatMode: 'quick', keywordSearchBudget: 'short' }, 'agent');
    expect(input?.keywordSearchBudget).to.equal('short');
    expect(chatTurnInput({ query: 'q', chatMode: 'quick' }, 'agent')).to.not.have.property('keywordSearchBudget');
    expect(chatTurnInput({ query: 'q', keywordSearchBudget: 'forever' }, 'agent')).to.equal(null);
    expect(runInputSchema.safeParse({ query: 'q', keywordSearchBudget: 'full' }).success).to.equal(true);
  });

  it('KSB-2: a run carrying it requires the guarded keyword_search_budget; one without it does not', () => {
    expect(GUARDED_WORKER_FEATURES).to.include(KEYWORD_SEARCH_BUDGET_FEATURE);
    expect(admissionRequirements({ agentKey: 'a1', input: INPUT }).features).to.include(KEYWORD_SEARCH_BUDGET_FEATURE);
    const plain = without(INPUT, 'keywordSearchBudget');
    expect(admissionRequirements({ agentKey: 'a1', input: plain }).features).to.not.include(
      KEYWORD_SEARCH_BUDGET_FEATURE,
    );
  });

  it('KSB-3: admission keeps it while a live worker reads it, else withholds it (ADR-CAP-01 D2)', async () => {
    for (const live of [true, false]) {
      const withheld: string[] = [];
      const context = policyContext({ orgId: 'o', actorId: 'a', idempotencyKey: 'k', deadlineMs: 1, input: INPUT }, {});
      await new KeywordSearchBudgetStep({
        live: async (feature) => (feature === KEYWORD_SEARCH_BUDGET_FEATURE ? live : feature === 'chat'),
        metrics: { withheld: (feature: string) => withheld.push(feature) } as never,
      }).run(context);
      expect(context.keywordSearchBudget).to.equal(live ? true : undefined);
      expect(withheld).to.deep.equal(live ? [] : [KEYWORD_SEARCH_BUDGET_FEATURE]);
      const admitted = withoutGuardedFields(
        INPUT,
        (feature) => feature !== KEYWORD_SEARCH_BUDGET_FEATURE || context.keywordSearchBudget === true,
      );
      expect(admitted.keywordSearchBudget).to.equal(live ? 'short' : undefined);
    }
  });

  it('KSB-4: a claim sends it only to a run that requires the feature, so a relaxed run reaches an older worker', () => {
    const run = (features: string[]): Pick<HarnessRun, 'input' | 'requires'> =>
      ({ input: INPUT, requires: { foldVersion: 1, features } }) as never;
    expect(workerInput(run([KEYWORD_SEARCH_BUDGET_FEATURE]))?.keywordSearchBudget).to.equal('short');
    expect(workerInput(run([]))).to.not.have.property('keywordSearchBudget');
    expect(workerInput(run([]))?.query).to.equal(INPUT.query);
  });

  it('KSB-5: a child run searches with its parent\'s budget', () => {
    const parent = { ...goldenSpecSubject().run, input: INPUT } as HarnessRun;
    expect(childInput(parent, 'Find the owner', null).keywordSearchBudget).to.equal('short');
    expect(childInput(goldenSpecSubject().run as HarnessRun, 'Find the owner', null)).to.not.have.property(
      'keywordSearchBudget',
    );
  });
});
