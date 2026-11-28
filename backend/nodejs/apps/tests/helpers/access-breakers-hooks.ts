/**
 * The access checks' circuit breakers are the process's own, one per kind of call to the query service. A test whose
 * calls fail can open one, and it would stay open for the next tests the worker runs, in this file or another: a later
 * test's sync or check is then skipped. Each test starts with every breaker closed. Loaded by `.mocharc.yaml`.
 */
import { recordAccessBreaker } from '../../src/modules/enterprise_search/adapters/harness-record-access';
import { viewSyncBreaker } from '../../src/modules/enterprise_search/services/conversation/sharing/chat-content-access';
import { accessCheckBreaker } from '../../src/modules/enterprise_search/utils/access-check-breaker';

export const mochaHooks = {
  beforeEach(): void {
    accessCheckBreaker.reset();
    recordAccessBreaker.reset();
    viewSyncBreaker.reset();
  },
};
