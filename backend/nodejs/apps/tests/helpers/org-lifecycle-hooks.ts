/**
 * The org lifecycle is process-wide: `Application.initialize()` installs this
 * build's hooks once at boot. A test that boots the app leaves them installed,
 * so a later test in the same mocha worker that checks nothing is installed
 * failed or passed by how `--parallel` spread the files. Each test starts
 * with the defaults. Loaded by `.mocharc.yaml`.
 */
import { resetOrgLifecycle } from '../../src/libs/org-lifecycle';

export const mochaHooks = {
  beforeEach(): void {
    resetOrgLifecycle();
  },
};
