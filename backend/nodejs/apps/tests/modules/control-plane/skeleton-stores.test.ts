/**
 * CP-SKEL: the store contracts (IS, GS, PS, BS) on the in-memory adapters.
 * Each production adapter runs the same suite in its own WP (CP-SKEL §3).
 */
import { describeBindingStoreContract } from './support/binding-store-contract';
import { describeGrantStoreContract } from './support/grant-store-contract';
import { describeInstallationStoreContract } from './support/installation-store-contract';
import { describePolicyStoreContract } from './support/policy-store-contract';
import {
  InMemoryBindingStore,
  InMemoryGrantStore,
  InMemoryInstallationStore,
  InMemoryPolicyStore,
} from './support/in-memory-stores';
import { inMemoryWorld } from './support/store-world';

describeInstallationStoreContract('InMemoryInstallationStore', async () => inMemoryWorld(new InMemoryInstallationStore()));
describeGrantStoreContract('InMemoryGrantStore', async () => inMemoryWorld(new InMemoryGrantStore()));
describePolicyStoreContract('InMemoryPolicyStore', async () => {
  const store = new InMemoryPolicyStore();
  return { ...inMemoryWorld(store), corrupt: async (orgId, scope) => store.corrupt(orgId, scope) };
});
describeBindingStoreContract('InMemoryBindingStore', async () => inMemoryWorld(new InMemoryBindingStore()));
