/**
 * Loaded by .mocharc.yaml `require` before any test file: installs the network guard (./no-network.ts) and ties
 * its dials to the running test through root hooks.
 */
import type { Context } from 'mocha';
import { describeDials, NetworkGuard } from './no-network';

const guard = NetworkGuard.fromEnv();
guard?.install();

export const mochaHooks = guard === null ? {} : {
  beforeEach(this: Context) {
    const test = this.currentTest;
    guard.enter(test ? `${test.file ?? ''} ${test.fullTitle()}` : '');
  },
  afterEach() {
    const dials = guard.leave();
    if (dials.length > 0 && guard.mode === 'fail') {
      throw new Error(
        `${dials.length} network dial(s) refused; add @network to the test title if it must dial\n${describeDials(dials)}`,
      );
    }
  },
};
