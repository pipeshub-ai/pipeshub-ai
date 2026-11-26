/** ENGPICK: the coding-engine preferences contract on the in-memory store. */
import { InMemoryCodingEnginePreferences } from '../../../src/modules/agent-harness/adapters/in-memory-coding-engine-preferences';
import { describeCodingEnginePreferencesContract } from './support/coding-engine-preferences-contract';

let sessions = 0;

describeCodingEnginePreferencesContract('in memory', async () => ({
  store: new InMemoryCodingEnginePreferences(),
  orgId: 'org-1',
  users: ['u1', 'u2'],
  stranger: { orgId: 'org-2', userId: 'u9' },
  session: () => Promise.resolve(`s-${(sessions += 1)}`),
}));
