/**
 * CP-SKEL: the service contracts (AZ, CB, BV, CN, MP, AD, RC) on the in-memory
 * adapters.
 */
import { ChainConfigAuditLog } from '../../../src/modules/agent-harness/adapters/chain-config-audit-log';
import { InMemoryAuditChainStore } from '../../../src/modules/agent-harness/adapters/in-memory-audit';
import type { PolicyLayer } from '../../../src/modules/control-plane/domain/policy-alias';
import { describeAuthorizerContract } from './support/authorizer-contract';
import { describeCredentialBinderContract } from './support/credential-binder-contract';
import { describeDirectoryContract } from './support/directory-contract';
import { FailingConfigAuditLog, InMemoryChangeOutbox, InMemoryControlPlaneTransaction } from './support/in-memory';
import {
  InMemoryAgentDirectory,
  InMemoryAuthorizer,
  InMemoryBindingStateView,
  InMemoryChangeNotifier,
  InMemoryCredentialBinder,
  InMemoryMembership,
  InMemoryReferenceChecker,
  InMemorySecretVault,
} from './support/in-memory-services';
import { InMemoryBindingStore } from './support/in-memory-stores';
import { describeChangeNotifierContract } from './support/notifier-contract';
import { describeReferenceCheckerContract } from './support/reference-checker-contract';
import { ulid } from './support/cp-objects';

describeAuthorizerContract('InMemoryAuthorizer', async () => {
  const authorizer = new InMemoryAuthorizer();
  return { authorizer, setRole: async (orgId, userId, role) => authorizer.setRole(orgId, userId, role) };
});

describeCredentialBinderContract('InMemoryCredentialBinder', {
  async make(options = {}) {
    const chain = new InMemoryAuditChainStore();
    const outbox = new InMemoryChangeOutbox();
    const audit = options.failingAudit ? new FailingConfigAuditLog() : new ChainConfigAuditLog(chain);
    const transaction = new InMemoryControlPlaneTransaction({ audit, outbox });
    const bindings = new InMemoryBindingStore();
    const vault = new InMemorySecretVault();
    let ids = 0;
    const binder = new InMemoryCredentialBinder({
      transaction,
      bindings,
      vault,
      newId: () => ulid((ids += 1)),
      now: () => 1_000,
    });
    return {
      binder,
      view: new InMemoryBindingStateView(bindings),
      bindings,
      vaultRead: async (path) => vault.read(path),
      configEntries: async (orgId) =>
        (chain.records.get(orgId) ?? []).filter((record) => record.streamId === `cp:${orgId}`),
      changeEvents: async () => outbox.events,
    };
  },
});

describeChangeNotifierContract('InMemoryChangeNotifier', async () => ({
  notifier: new InMemoryChangeNotifier(),
  settled: async () => undefined,
}));

describeDirectoryContract('InMemoryMembership and InMemoryAgentDirectory', async () => {
  const membership = new InMemoryMembership();
  const agents = new InMemoryAgentDirectory();
  return {
    membership,
    agents,
    setTeams: async (orgId, userId, teams) => {
      membership.teams.set(`${orgId}|${userId}`, teams);
    },
    setAgent: async (orgId, agentKey, agent) => {
      agents.agents.set(`${orgId}|${agentKey}`, agent);
    },
    setAvailable: async (available) => {
      membership.available = available;
      agents.available = available;
    },
  };
});

const referencesOf = (layer: PolicyLayer): string[] => {
  const sections = layer as unknown as { approvals?: { approvers?: string[] }; tools?: { grant?: Record<string, unknown> } };
  const approvers = sections.approvals?.approvers ?? [];
  const grant = sections.tools?.grant ?? {};
  return [...approvers, ...Object.keys(grant)];
};

describeReferenceCheckerContract('InMemoryReferenceChecker', async () => {
  const checker = new InMemoryReferenceChecker(referencesOf);
  const add = async (orgId: string, ref: string) => {
    checker.known.set(orgId, new Set([...(checker.known.get(orgId) ?? []), ref]));
  };
  return { checker, addGroup: add, addInstallation: add };
});
