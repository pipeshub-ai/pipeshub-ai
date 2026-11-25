import { bindingProof } from '../../../../src/modules/agent-harness/domain/sandbox-binding';

/** The key a test broker signs sandbox bindings under (R38IDENT-FU-7); a test value, not a secret. */
export const TEST_BINDING_KEY = Buffer.alloc(32, 7);

export const testBindingKeys = (): readonly Buffer[] => [TEST_BINDING_KEY];

/** What the broker attached to the sandbox serving `attemptId` of `runId` in `orgId`. */
export const proofFor = (orgId: string, runId: string, attemptId: string): string =>
  bindingProof(TEST_BINDING_KEY, { orgId, runId, attemptId });
