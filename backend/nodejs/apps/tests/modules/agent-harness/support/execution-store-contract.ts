/**
 * Behaviour every ExecutionStore + RunAdmissionStore pair must share. Runs the
 * same cases against the in-memory adapter (unit suite) and MongoDB
 * (`execution-store.mongo.itest.ts`, real replica set), so the two cannot drift.
 * The cases live by concern under `execution-store-contract/`; the harness every
 * part shares, and other suites import, is `execution-store-contract/harness.ts`.
 */
import { describeAdmissionCases } from './execution-store-contract/admission';
import { describeAnswerCases } from './execution-store-contract/answers';
import { describeClaimCases } from './execution-store-contract/claims';
import { describeCommitCases } from './execution-store-contract/commits';
import { describeControlCases } from './execution-store-contract/control';
import { describeDecisionCases } from './execution-store-contract/decisions';
import { describePersonWaitCases } from './execution-store-contract/person-wait';
import { describePriorityCases } from './execution-store-contract/priority';
import { describeRunPolicyCases } from './execution-store-contract/run-policy';
import { describeRunListCases } from './execution-store-contract/run-lists';
import {
  ContractBackendFactory,
  ContractHarness,
  ContractHooks,
  contractClock,
  createContractHarness,
  freshContractBackend,
} from './execution-store-contract/harness';

export * from './execution-store-contract/harness';

export function describeExecutionStoreContract(
  name: string,
  createBackend: ContractBackendFactory,
  hooks: ContractHooks = {},
): void {
  describe(`ExecutionStore contract: ${name}`, function () {
    this.timeout(30_000);
    let h: ContractHarness;

    if (hooks.before) before(hooks.before);
    if (hooks.after) after(hooks.after);
    beforeEach(async () => {
      const clock = contractClock();
      h = createContractHarness(await freshContractBackend(createBackend, clock), clock);
    });

    describeAdmissionCases(() => h, createBackend);
    describeClaimCases(() => h);
    describeCommitCases(() => h);
    describeControlCases(() => h);
    describeDecisionCases(() => h);
    describePersonWaitCases(() => h);
    describePriorityCases(() => h);
    describeAnswerCases(() => h);
    describeRunPolicyCases(() => h);
    describeRunListCases(() => h);
  });
}
