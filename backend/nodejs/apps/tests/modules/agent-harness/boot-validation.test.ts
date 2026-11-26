import { expect } from 'chai';
import { spawnSync } from 'node:child_process';
import { join } from 'node:path';
import { validateAgentHarnessEnvironment } from '../../../src/modules/agent-harness/config';
import { OFFERINGS } from '../../../src/modules/agent-harness/contracts/generated/core/enums';

/**
 * CPD0-40/41: a bad AGENT_HARNESS_OFFERING stops the process before the
 * application starts, naming the variable (the one stated behaviour change of
 * CPD-0); unset, blank and every offering in any case pass.
 */
const APP = join(__dirname, '..', '..', '..');

describe('agent-harness boot validation (CPD-0)', function () {
  this.timeout(120_000);

  it('CPD0-41 accepts an unset, blank or listed offering in any case', () => {
    for (const value of [undefined, '', '  ', ...OFFERINGS, ...OFFERINGS.map((o) => ` ${o.toUpperCase()} `)]) {
      expect(() => validateAgentHarnessEnvironment({ AGENT_HARNESS_OFFERING: value }), String(value)).to.not.throw();
    }
  });

  it('CPD0-41 refuses any other offering, naming the variable', () => {
    expect(() => validateAgentHarnessEnvironment({ AGENT_HARNESS_OFFERING: 'enterprise' })).to.throw(
      `AGENT_HARNESS_OFFERING="enterprise" is not one of ${OFFERINGS.join(', ')}`,
    );
  });

  it('CPD0-40 the process exits before the application starts on a bad offering', () => {
    const run = spawnSync(process.execPath, ['-r', 'ts-node/register', 'src/index.ts'], {
      cwd: APP,
      env: {
        ...process.env,
        TS_NODE_TRANSPILE_ONLY: 'true',
        TS_NODE_PROJECT: join(APP, 'tsconfig.json'),
        AGENT_HARNESS_OFFERING: 'bogus',
      },
      encoding: 'utf8',
      timeout: 100_000,
    });
    expect(run.status, run.stderr.slice(-2000)).to.not.equal(0);
    expect(run.status).to.not.equal(null);
    expect(run.stderr).to.match(/AGENT_HARNESS_OFFERING="bogus" is not one of/);
    expect(run.stdout + run.stderr).to.not.match(/Server (is )?(running|listening|started)/i);
  });
});
