/**
 * Loaded via .mocharc.yaml `require` BEFORE any test file.
 *
 * The agent harness's server setting is on by default (ADR-OPS-06 as amended 2026-11-20, AGENT_HARNESS_WORKER_ENABLED);
 * set here too so the suite does not depend on the default. A test of the switch itself, of its default or of the
 * opt-out passes its own environment (every reader takes one) or sets the variable for its own duration.
 */
if (process.env.AGENT_HARNESS_WORKER_ENABLED == null) {
  process.env.AGENT_HARNESS_WORKER_ENABLED = 'true';
}

/*
 * The security profile (ADR-OPS-07, PIPESHUB_HARDENED): unset reads compatibility, the behaviour of a release without
 * the security programme's hardened defaults. Most of this suite was written for those defaults, so the process runs
 * as a hardened install; a test of the compatibility profile passes its own environment or deletes the variable for
 * its own duration.
 */
if (process.env.PIPESHUB_HARDENED == null) {
  process.env.PIPESHUB_HARDENED = 'true';
}

/*
 * The org predicate (ADR-OPS-08): every behaviour a person or an org sees follows `orgHarnessOn`, which reads the
 * org's Labs switch through the reader the composition records. The suite runs as an org with the harness turned
 * on, as it runs with the switch on. A test that builds the composition records the real reader, so each test starts
 * from this one again; a test of an org with it off records its own for its own duration.
 */
// eslint-disable-next-line @typescript-eslint/no-require-imports
const { recordOrgHarnessReader } = require('../../src/modules/agent-harness/config') as typeof import('../../src/modules/agent-harness/config');
const harnessOnForEveryOrg = (): Promise<boolean> => Promise.resolve(true);
recordOrgHarnessReader(harnessOnForEveryOrg);

export const mochaHooks = {
  beforeEach(): void {
    recordOrgHarnessReader(harnessOnForEveryOrg);
  },
};
