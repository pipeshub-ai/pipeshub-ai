import { inDurableTransaction } from '../../../libs/repositories/agent-harness/durable-transaction';
import { prometheusChatPathMetrics } from '../adapters/telemetry/chat-path-metrics';
import { prometheusResolvedTurnMetrics } from '../adapters/telemetry/resolved-turn-metrics';
import {
  GatewayTokenStores,
  HarnessAuthorityStores,
} from '../adapters/configuration/encrypted-config-document-store';
import { AttemptTokenAuthority } from '../application/attempt-token-authority';
import { BudgetLedgerService } from '../application/budget-ledger-service';
import { ExecutionService } from '../application/execution-service';
import { GatewayRefusalJournal } from '../application/gateway-refusal-journal';
import {
  gatewayKeyring,
  GatewayTokenAuthority,
} from '../application/gateway-token-authority';
import { GatewayTokenService } from '../application/gateway-token-service';
import { HarnessAuthority, RunReader } from '../application/harness-authority';
import { HarnessLogger } from '../application/harness-log';
import { ResolvedTurnAuthority } from '../application/resolved-turn-authority';
import { SigningKeyring } from '../application/signing-keyring';
import { WorkerCredentialAuthority } from '../application/worker-credential-authority';
import { ComponentCredentialPublisher } from '../application/component-credential-publisher';
import { RedisComponentLogins } from '../adapters/redis/redis-component-logins';
import { AllComponentLogins } from '../adapters/etcd/etcd-component-roles';
import { sandboxPlaneEndpoints } from '../adapters/configuration/sandbox-plane-endpoints';
import { componentKey } from '../domain/component-keys';
import { brokerBindingKeys } from '../domain/sandbox-binding';
import { loadConfigurationManagerConfig } from '../../configuration_manager/config/config';
import {
  secretKeyConfigured,
  secretKeyRing,
} from '../../../libs/encryptor/secret-key-ring';
import { getRedisProvider } from '../../../libs/services/redis/connectionProviderFactory';
import { AuthorityClock, systemAuthorityClock } from '../domain/authority';
import { HarnessBinder } from './context';
import { sharedBudgetGuardOf } from './cost-ports';
import {
  AGENT_HARNESS_AUTHORITY,
  AGENT_HARNESS_BUDGET_LEDGER,
  AGENT_HARNESS_COMPONENT_PUBLISHER,
  AGENT_HARNESS_EXECUTION_SERVICE,
  AGENT_HARNESS_GATEWAY_AUTHORITY,
  AGENT_HARNESS_GATEWAY_KEYRING,
  AGENT_HARNESS_GATEWAY_TOKENS,
  AGENT_HARNESS_RESOLVED_TURNS,
} from './tokens';
import type { interfaces } from 'inversify';

export function createHarnessAuthority(
  stores: HarnessAuthorityStores,
  runs: RunReader,
  clock: AuthorityClock = systemAuthorityClock,
  logger?: HarnessLogger,
): HarnessAuthority {
  const role = (
    store: HarnessAuthorityStores['workerCredential'],
  ): WorkerCredentialAuthority =>
    new WorkerCredentialAuthority(store, clock, undefined, logger);
  const others = stores.roleCredentials;
  return new HarnessAuthority(
    role(stores.workerCredential),
    new AttemptTokenAuthority({
      signingKeys: stores.attemptSigningKeys,
      publishedKeys: stores.attemptPublicKeys,
      clock,
      logger,
    }),
    runs,
    others === undefined
      ? null
      : {
          scrape: role(others.scrape),
          'egress-proxy': role(others['egress-proxy']),
          'sandbox-broker': role(others['sandbox-broker']),
        },
  );
}

/**
 * The gateway keyring and its signers, once per process: gateway tokens and
 * resolved turns (CPD-10) sign with the same keys under their own audiences.
 */
function bindGatewayKeyring(
  container: interfaces.Container,
  stores: GatewayTokenStores,
  logger: HarnessLogger | undefined,
): void {
  if (container.isBound(AGENT_HARNESS_GATEWAY_KEYRING)) return;
  const options = {
    signingKeys: stores.gatewaySigningKeys,
    publishedKeys: stores.gatewayPublicKeys,
    clock: systemAuthorityClock,
    logger,
  };
  container
    .bind<SigningKeyring>(AGENT_HARNESS_GATEWAY_KEYRING)
    .toDynamicValue(() => gatewayKeyring(options))
    .inSingletonScope();
  container
    .bind<GatewayTokenAuthority>(AGENT_HARNESS_GATEWAY_AUTHORITY)
    .toDynamicValue(
      (context) =>
        new GatewayTokenAuthority(
          options,
          context.container.get<SigningKeyring>(AGENT_HARNESS_GATEWAY_KEYRING),
        ),
    )
    .inSingletonScope();
  container
    .bind<ResolvedTurnAuthority>(AGENT_HARNESS_RESOLVED_TURNS)
    .toDynamicValue(
      (context) =>
        new ResolvedTurnAuthority({
          keys: context.container.get<SigningKeyring>(
            AGENT_HARNESS_GATEWAY_KEYRING,
          ),
          clock: systemAuthorityClock,
          metrics: prometheusResolvedTurnMetrics,
          logger,
        }),
    )
    .inSingletonScope();
}

export const bindAuthority: HarnessBinder = ({
  container,
  dependencies,
  logger,
}) => {
  if (dependencies.gatewayTokens !== undefined) {
    bindGatewayKeyring(container, dependencies.gatewayTokens, logger);
  }
  if (container.isBound(AGENT_HARNESS_AUTHORITY)) return;
  // Wiring that binds only part of the harness (the binder tests) passes no authority stores.
  const stores = dependencies.authority as HarnessAuthorityStores | undefined;
  const components = stores?.components ?? {};
  const endpointsSource = stores?.endpoints;
  const kvLogins = stores?.kvLogins;
  if (Object.keys(components).length > 0) {
    container
      .bind<ComponentCredentialPublisher>(AGENT_HARNESS_COMPONENT_PUBLISHER)
      .toDynamicValue(
        (context) =>
          new ComponentCredentialPublisher({
            authority: context.container.get<HarnessAuthority>(
              AGENT_HARNESS_AUTHORITY,
            ),
            documents: components,
            ring: () => secretKeyRing(),
            logins: new AllComponentLogins([
              new RedisComponentLogins(
                getRedisProvider(),
                (component) => componentKey(component),
                loadConfigurationManagerConfig().redisConfig.keyPrefix ??
                  'pipeshub:kv:',
              ),
              ...(kvLogins !== undefined ? [kvLogins] : []),
            ]),
            ...(endpointsSource !== undefined
              ? { endpoints: () => sandboxPlaneEndpoints(endpointsSource) }
              : {}),
            logger,
          }),
      )
      .inSingletonScope();
  }
  container
    .bind<HarnessAuthority>(AGENT_HARNESS_AUTHORITY)
    .toDynamicValue((context) =>
      createHarnessAuthority(
        dependencies.authority,
        context.container.get<ExecutionService>(
          AGENT_HARNESS_EXECUTION_SERVICE,
        ),
        systemAuthorityClock,
        logger,
      ),
    )
    .inSingletonScope();
};

export const bindGatewayTokens: HarnessBinder = ({
  container,
  dependencies,
  journal,
  logger,
}) => {
  const gatewayStores = dependencies.gatewayTokens;
  if (
    gatewayStores === undefined ||
    container.isBound(AGENT_HARNESS_GATEWAY_TOKENS)
  ) {
    return;
  }
  bindGatewayKeyring(container, gatewayStores, logger);
  container
    .bind<GatewayTokenService>(AGENT_HARNESS_GATEWAY_TOKENS)
    .toDynamicValue(
      (context) =>
        new GatewayTokenService({
          tokens: context.container.get<GatewayTokenAuthority>(
            AGENT_HARNESS_GATEWAY_AUTHORITY,
          ),
          runs: context.container.get<ExecutionService>(
            AGENT_HARNESS_EXECUTION_SERVICE,
          ),
          clock: systemAuthorityClock,
          logger,
          bindingKeys: () =>
            brokerBindingKeys(() =>
              secretKeyConfigured() ? secretKeyRing() : null,
            ),
          refusals: new GatewayRefusalJournal(
            journal.store,
            journal.service,
            inDurableTransaction,
            logger,
          ),
        }),
    )
    .inSingletonScope();
};

export const bindBudgetLedger: HarnessBinder = ({
  container,
  dependencies,
  budgets,
  logger,
}) => {
  if (container.isBound(AGENT_HARNESS_BUDGET_LEDGER)) return;
  const gatewayStores = dependencies.gatewayTokens;
  if (gatewayStores !== undefined) {
    bindGatewayKeyring(container, gatewayStores, logger);
  }
  container
    .bind<BudgetLedgerService>(AGENT_HARNESS_BUDGET_LEDGER)
    .toDynamicValue(
      (context) =>
        new BudgetLedgerService({
          ledger: budgets,
          runs: context.container.get<ExecutionService>(
            AGENT_HARNESS_EXECUTION_SERVICE,
          ),
          attempts: context.container.get<HarnessAuthority>(
            AGENT_HARNESS_AUTHORITY,
          ),
          gateways:
            gatewayStores === undefined
              ? null
              : context.container.get<GatewayTokenAuthority>(
                  AGENT_HARNESS_GATEWAY_AUTHORITY,
                ),
          clock: systemAuthorityClock,
          logger,
          metrics: prometheusChatPathMetrics,
          shared: sharedBudgetGuardOf(context.container),
        }),
    )
    .inSingletonScope();
};
