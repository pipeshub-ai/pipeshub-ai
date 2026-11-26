import { etcdComponentLogins } from '../../agent-harness/adapters/etcd/etcd-component-roles';
import { createGroupMembership } from '../../../libs/repositories/agent-harness/group-membership';
import {
  createChatTitles,
  createMemberProfiles,
} from '../../../libs/repositories/agent-harness/audit-names';
import { Container } from 'inversify';
import { orgPrincipalErasers } from '../../agent-harness/binders/erasure';
import type { OrgSettings } from '../../agent-harness/ports/org-settings';
import { singleStoreOrgSettings } from '../../agent-harness/adapters/configuration/single-store-org-settings';
import { RedisOrgEraser } from '../../agent-harness/adapters/redis/redis-org-eraser';
import { createOrgDeletedAnnouncer } from '../../../libs/repositories/agent-harness/org-deleted-announcer';
import { MailService as AuthMailService } from '../../auth/services/mail.service';
import { Logger } from '../../../libs/services/logger.service';
import { ConfigurationManagerConfig } from '../../configuration_manager/config/config';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { AppConfig } from '../../tokens_manager/config/config';
import {
  AGENT_HARNESS_CAPABILITIES,
  bindAgentHarness,
  createCollaboratorTurns,
  createHarnessRollout,
  orgHarnessReaderOf,
  harnessOrgStops,
  prepareAgentHarnessAuthority,
  prepareAgentHarnessStore,
  startAgentHarnessBackgroundServices,
  startSessionErasureServices,
  stopAgentHarnessBackgroundServices,
} from '../../agent-harness/composition';
import {
  AGENT_HARNESS_EDITION,
  harnessEditionOf,
  harnessMigrations,
  noopHarnessEdition,
  type HarnessEdition,
} from '../../agent-harness/edition';
import { createChatSessionRepository } from '../../../libs/repositories/agent-harness/chat-session-repository';
import { createConversationWriter } from '../../../libs/repositories/agent-harness/conversation-writer';
import { createHistorySource } from '../../../libs/repositories/agent-harness/history-source';
import { HarnessAgentAccessPolicy } from '../adapters/harness-agent-access';
import { createActorAuthority } from '../../../libs/repositories/agent-harness/actor-authority';
import { WebClient } from '@slack/web-api';
import { refreshSlackBotRegistry } from '../../../integrations/slack-bot/src/botRegistry';
import { slackClientOptions } from '../../../integrations/slack-bot/src/utils/web-api';
import { SlackBotThreadReplies } from '../adapters/slack-thread-replies';
import {
  AgentSlackBotMessages,
  MailScheduleNotices,
} from '../adapters/harness-schedule-channels';
import { HarnessScheduleNotifier } from '../../../libs/repositories/agent-harness/schedule-notifier/mongo';
import { createScheduleNoticeRecords } from '../../../libs/repositories/agent-harness/schedule-notifier';
import {
  QuerySkillProposals,
  SocketInboxCountPublisher,
} from '../adapters/harness-inbox-channels';
import { HarnessAgentNames } from '../adapters/harness-agent-names';
import {
  PlatformActivityPanel,
  PlatformKnowledgePublications,
  PlatformScheduledRuns,
} from '../../agent-harness/adapters/configuration/platform-rollout-settings';
import { ConnectorPersonalInstanceEraser } from '../../agent-harness/adapters/connector-personal-instances';
import { ConnectorRepositoryToolsets } from '../../agent-harness/adapters/connector-repository-toolsets';
import { ConnectorToolRuleWriter } from '../../agent-harness/adapters/connector-tool-rule-writer';
import { QueryPublicationAuthority } from '../../agent-harness/adapters/query-publication-authority';
import { recordEventStreams } from '../../agent-harness/adapters/messaging/broker-trigger-source';
import { createDedicatedMessageConsumer } from '../../../libs/services/message-broker.factory';
import { isRedisBroker, laneCount } from '../../../libs/utils/lane.utils';
import {
  createGatewayTokenStores,
  createHarnessAuthorityStores,
  createApprovalsStore,
} from '../../agent-harness/adapters/configuration/encrypted-config-document-store';
import { createModelAccountVault } from '../../agent-harness/adapters/configuration/model-account-vault';
import { AiModelsConfiguredModels } from '../../agent-harness/adapters/configuration/ai-models-configured-models';
import { createCodingEnginePreferences } from '../../../libs/repositories/agent-harness/coding-engine-preferences';
import {
  AiModelsConfiguredLlms,
  PlatformLabsStages,
} from '../../agent-harness/adapters/configuration/platform-pin-sources';
import { RedisRunWakeup } from '../../agent-harness/adapters/redis/redis-run-wakeup';
import { RedisEngineSignInRelay } from '../../agent-harness/adapters/redis/redis-engine-signin-relay';
import { createEngineSignIns } from '../../../libs/repositories/agent-harness/engine-signin';
import { RedisRunReadyNotifier } from '../../agent-harness/adapters/redis/redis-run-ready';
import { createPolicyStore } from '../../control-plane';
import { preparePolicyStore } from '../../agent-harness/adapters/configuration/policy-backing';
import { HarnessApproverDirectory } from '../../../libs/repositories/agent-harness/approver-directory/mongo';
import { createApproverPeople } from '../../../libs/repositories/agent-harness/approver-directory';
import {
  isAgentHarnessInfrastructureReady,
  recordOrgHarnessReader,
} from '../../agent-harness/config';
import { sandboxBrokerEndpoint } from '../../agent-harness/adapters/configuration/sandbox-broker-endpoint';
import { RedisLiveBus } from '../../agent-harness/adapters/redis/redis-live-bus';
import {
  ACTIVITY_LIVE_CHANNEL,
  ActivityFrame,
} from '../../agent-harness/projection/activity';
import type { LiveBusEraser } from '../../agent-harness/ports/session-erasure';
import { LiveBusCapabilityProbe } from '../../agent-harness/adapters/redis/live-bus-probe';
import { createConversationReader } from '../../../libs/repositories/agent-harness/conversation-reader';
import { createSessionRoles } from '../../../libs/repositories/agent-harness/session-roles';
import { createConversationEraser } from '../../../libs/repositories/agent-harness/conversation-eraser';
import { HarnessAgentDeletionLookup } from '../adapters/harness-agent-deletions';
import { HarnessArtifactEraser } from '../adapters/harness-artifact-eraser';
import { createAttachmentCarriers } from '../../../libs/repositories/agent-harness/attachment-carriers';
import { HarnessArtifactCopier } from '../adapters/harness-artifact-copier';
import { createSessionForkStore } from '../../../libs/repositories/agent-harness/session-forks';
import {
  StorageObjectPurger,
  StorageWorkspaceObjects,
} from '../../agent-harness/adapters/storage/storage-workspace-objects';
import { TokenScopes } from '../../../libs/enums/token-scopes.enum';
import { createConversationRetention } from '../../../libs/repositories/agent-harness/conversation-retention';
import {
  HarnessArtifactReadAccess,
  HarnessRecordReadAccess,
} from '../adapters/harness-record-access';
import { controlPlaneEventBroker } from '../../agent-harness/adapters/messaging/control-plane-event-broker';
import { harnessRedisProvider } from '../../agent-harness/adapters/redis/harness-redis';
import { configurationCipher } from '../../agent-harness/adapters/configuration/encrypted-config-document-store';

const loggerConfig = {
  service: 'Enterprise Search Service',
};

export class EnterpriseSearchAgentContainer {
  protected static instance: Container | null = null;
  protected static logger: Logger = Logger.getInstance(loggerConfig);

  static async initialize(
    configurationManagerConfig: ConfigurationManagerConfig,
    appConfig: AppConfig,
    harnessEdition: HarnessEdition = noopHarnessEdition,
  ): Promise<Container> {
    const container = new Container();
    container
      .bind<HarnessEdition>(AGENT_HARNESS_EDITION)
      .toConstantValue(harnessEdition);

    // Bind configuration
    // Bind logger
    container.bind<Logger>('Logger').toConstantValue(this.logger);
    container
      .bind<ConfigurationManagerConfig>('ConfigurationManagerConfig')
      .toConstantValue(configurationManagerConfig);
    container
      .bind<AppConfig>('AppConfig')
      .toDynamicValue(() => appConfig) // Always fetch latest reference
      .inTransientScope();
    // Initialize and bind services
    await this.initializeServices(container, appConfig);

    this.instance = container;
    return container;
  }

  /**
   * Where the harness reads the settings an org owns (its AI models, its platform and Labs settings), for the org
   * each read names: the one store a single-org deployment keeps them in, or an edition's per-org documents.
   */
  protected static orgSettings(
    keyValueStoreService: KeyValueStoreService,
  ): OrgSettings {
    return singleStoreOrgSettings(keyValueStoreService);
  }

  protected static async initializeServices(
    container: Container,
    appConfig: AppConfig,
  ): Promise<void> {
    try {
      // Initialize services

      const keyValueStoreService = KeyValueStoreService.getInstance(
        container.get<ConfigurationManagerConfig>('ConfigurationManagerConfig'),
      );

      await keyValueStoreService.connect();
      container
        .bind<KeyValueStoreService>('KeyValueStoreService')
        .toConstantValue(keyValueStoreService);
      const authTokenService = new AuthTokenService(
        appConfig.jwtSecret,
        appConfig.scopedJwtSecret,
      );
      const authMiddleware = new AuthMiddleware(
        container.get('Logger'),
        authTokenService,
      );
      container
        .bind<AuthMiddleware>('AuthMiddleware')
        .toConstantValue(authMiddleware);
      // The Redis the workers stream token deltas to (REPORT 6.2.4); only
      // touched when the deployment asked for the harness.
      const configurationManagerConfig =
        container.get<ConfigurationManagerConfig>('ConfigurationManagerConfig');
      const redis = isAgentHarnessInfrastructureReady()
        ? harnessRedisProvider(configurationManagerConfig.redisConfig)
        : null;
      const liveBus = redis !== null ? new RedisLiveBus(redis) : null;
      const activityBus =
        redis !== null
          ? new RedisLiveBus<ActivityFrame>(
              redis,
              undefined,
              ACTIVITY_LIVE_CHANNEL,
            )
          : null;
      const liveStreams: LiveBusEraser | null =
        liveBus !== null && activityBus !== null
          ? {
              erase: async (runIds) =>
                (await liveBus.erase(runIds)) +
                (await activityBus.erase(runIds)),
            }
          : null;
      const approvalsDocument = createApprovalsStore(keyValueStoreService);
      const personalChoices = createCodingEnginePreferences();
      const approverDirectory = new HarnessApproverDirectory(
        createApproverPeople(),
      );
      // WP-51: notices name agents, never show their keys.
      const agentNames = new HarnessAgentNames(() => ({
        aiBackend: appConfig.aiBackend,
        jwtSecret: appConfig.jwtSecret,
        scopedJwtSecret: appConfig.scopedJwtSecret,
      }));
      const modelAccountVault = createModelAccountVault(keyValueStoreService);
      const queryServiceEraser = new HarnessArtifactEraser(() => ({
        aiBackend: appConfig.aiBackend,
        jwtSecret: appConfig.jwtSecret,
        scopedJwtSecret: appConfig.scopedJwtSecret,
      }));
      // TR-07: a schedule's Slack notice comes from its agent's own bot; the form offers Slack only then.
      const agentSlackBots = new AgentSlackBotMessages(
        () => refreshSlackBotRegistry(),
        (botToken) => new WebClient(botToken, slackClientOptions()),
      );
      // TR-07: the inbox, email and the agent's Slack bot, for a schedule's actor and anyone an edition notifies.
      const harnessNotices = new HarnessScheduleNotifier({
        mail: new MailScheduleNotices(
          new AuthMailService(appConfig, container.get<Logger>('Logger')),
          () => appConfig.scopedJwtSecret,
        ),
        slack: agentSlackBots,
        frontendUrl: () => appConfig.frontendUrl,
        records: createScheduleNoticeRecords(),
      });
      const orgSettings = this.orgSettings(keyValueStoreService);
      const rollout = createHarnessRollout(keyValueStoreService, undefined, {
        orgSettings,
        ...harnessOrgStops(harnessEditionOf(container), keyValueStoreService),
      });
      recordOrgHarnessReader(orgHarnessReaderOf(rollout));
      bindAgentHarness(container, {
        edition: harnessEditionOf(container),
        sessions: createChatSessionRepository(),
        conversations: createConversationWriter(),
        history: createHistorySource(),
        agentAccess: new HarnessAgentAccessPolicy(() => appConfig.aiBackend),
        rollout,
        authority: createHarnessAuthorityStores(
          keyValueStoreService,
          undefined,
          etcdComponentLogins(keyValueStoreService),
        ),
        gatewayTokens: createGatewayTokenStores(keyValueStoreService),
        inlineClaimer: () => appConfig.aiBackend,
        conversationReader: createConversationReader(),
        // D-SLACK-ASK: a Slack turn's end, posted into its thread by the bot that answers it.
        slackThreads: new SlackBotThreadReplies(
          async () =>
            (await refreshSlackBotRegistry()).map((bot) => ({
              ...(bot.botId !== undefined ? { botId: bot.botId } : {}),
              botToken: bot.botToken,
            })),
          (botToken) => new WebClient(botToken, slackClientOptions()),
          () => appConfig.frontendUrl,
        ),
        recordAccess: new HarnessRecordReadAccess(() => appConfig.aiBackend),
        toolRules: new ConnectorToolRuleWriter(
          () => appConfig.connectorBackend,
        ),
        artifactAccess: new HarnessArtifactReadAccess(
          () => appConfig.aiBackend,
        ),
        roles: createSessionRoles(),
        collaboratorTurns: createCollaboratorTurns(keyValueStoreService),
        liveBus: liveBus,
        activity: {
          setting: new PlatformActivityPanel(),
          bus: activityBus,
        },
        wakeup: redis !== null ? new RedisRunWakeup(redis) : null,
        runReady:
          redis !== null
            ? new RedisRunReadyNotifier(redis, container.get<Logger>('Logger'))
            : null,
        approvers: approverDirectory,
        noticeChannels: harnessNotices,
        // G2 (ADR-INBOX-01): finished-run notices on the schedule notifier's channels, the counts over the
        // notification socket, and skill proposals.
        inbox: {
          notifier: harnessNotices,
          publisher: new SocketInboxCountPublisher(),
          skills: new QuerySkillProposals(() => appConfig.aiBackend),
        },
        policyStore: createPolicyStore(),
        approvalSettings: {
          document: approvalsDocument,
        },
        // G-41: who may use the harness, engines and models, by the org's user groups, read live.
        groupMembership: createGroupMembership(),
        memory: { choices: personalChoices },
        sandboxBrokerEndpoint: () =>
          sandboxBrokerEndpoint(keyValueStoreService),
        modelAccounts: modelAccountVault,
        codingEnginePreferences: personalChoices,
        repositoryToolsets: new ConnectorRepositoryToolsets(
          () => appConfig.connectorBackend,
        ),
        orgMembers: approverDirectory,
        auditNames: {
          members: createMemberProfiles(),
          chats: createChatTitles(),
          agents: agentNames,
        },
        engineSignIns: {
          store: createEngineSignIns(),
          ...(redis !== null
            ? { relay: new RedisEngineSignInRelay(redis) }
            : {}),
        },
        credentialBindings: {
          kv: keyValueStoreService,
          cipher: configurationCipher,
        },
        modelAccess: {
          models: new AiModelsConfiguredModels(orgSettings),
        },
        pin: {
          models: new AiModelsConfiguredLlms(orgSettings),
          stages: new PlatformLabsStages(orgSettings),
        },
        capabilityProbes:
          redis !== null ? [new LiveBusCapabilityProbe(redis)] : [],
        changeEvents: isAgentHarnessInfrastructureReady()
          ? controlPlaneEventBroker(appConfig, container.get<Logger>('Logger'))
          : null,
        publications: {
          setting: new PlatformKnowledgePublications(orgSettings),
          authority: new QueryPublicationAuthority(() => appConfig.aiBackend),
        },
        schedules: {
          actors: createActorAuthority(() => appConfig.jwtSecret),
          setting: new PlatformScheduledRuns(orgSettings),
          // TR-07: a run's end in the inbox, and by email and Slack where the schedule and the org allow.
          slackBots: agentSlackBots,
          notifier: harnessNotices,
          // ADR-UI-03: the list names its agents and their live versions in one lookup.
          agents: agentNames,
          connectorEvents: {
            consumer: () =>
              createDedicatedMessageConsumer(
                appConfig,
                container.get<Logger>('Logger'),
                'agent-harness-triggers',
                'agent-harness-triggers',
              ),
            streams: () => recordEventStreams(isRedisBroker(), laneCount()),
          },
        },
        forks: {
          store: createSessionForkStore(),
          artifacts: new HarnessArtifactCopier(() => ({
            aiBackend: appConfig.aiBackend,
            jwtSecret: appConfig.jwtSecret,
            scopedJwtSecret: appConfig.scopedJwtSecret,
          })),
          objects: new StorageWorkspaceObjects(
            () => ({
              baseUrl: appConfig.storageBackend,
              storageType: appConfig.storage.storageType,
            }),
            (orgId) =>
              new AuthTokenService(
                appConfig.jwtSecret,
                appConfig.scopedJwtSecret,
              ).generateScopedToken(
                { orgId, scopes: [TokenScopes.STORAGE_TOKEN] },
                '5m',
              ),
          ),
        },
        erasure: {
          conversations: createConversationEraser(queryServiceEraser),
          artifacts: queryServiceEraser,
          traces: queryServiceEraser,
          liveBus: liveStreams,
          agentDeletions: new HarnessAgentDeletionLookup(() => ({
            aiBackend: appConfig.aiBackend,
            jwtSecret: appConfig.jwtSecret,
            scopedJwtSecret: appConfig.scopedJwtSecret,
          })),
          retention: createConversationRetention(),
          unsentAttachments: {
            uploads: queryServiceEraser,
            carriers: createAttachmentCarriers(),
            eraser: queryServiceEraser,
          },
          snapshotObjects: new StorageObjectPurger(
            () => ({
              baseUrl: appConfig.storageBackend,
              storageType: appConfig.storage.storageType,
            }),
            (orgId) =>
              new AuthTokenService(
                appConfig.jwtSecret,
                appConfig.scopedJwtSecret,
              ).generateScopedToken(
                { orgId, scopes: [TokenScopes.STORAGE_TOKEN] },
                '5m',
              ),
          ),
          // CLEANUP-G1: what an org's erasure job is built from.
          orgQueryService: queryServiceEraser,
          orgPrincipals: orgPrincipalErasers({
            modelAccounts: modelAccountVault,
            personalInstances: new ConnectorPersonalInstanceEraser(() => ({
              connectorBackend: appConfig.connectorBackend,
              jwtSecret: appConfig.jwtSecret,
              scopedJwtSecret: appConfig.scopedJwtSecret,
            })),
            ...(redis !== null
              ? { signInRelay: new RedisEngineSignInRelay(redis) }
              : {}),
          }),
          ...(redis !== null ? { orgRedis: new RedisOrgEraser(redis) } : {}),
          orgDeletedAnnouncer: createOrgDeletedAnnouncer(),
        },
      });
      await prepareAgentHarnessStore(
        container.get<Logger>('Logger'),
        process.env,
        container.get(AGENT_HARNESS_CAPABILITIES),
        undefined,
        {
          whenPrepared: () => {
            startAgentHarnessBackgroundServices(container);
          },
          migrations: harnessMigrations(harnessEditionOf(container)),
        },
      );
      await prepareAgentHarnessAuthority(
        container,
        container.get<Logger>('Logger'),
      );
      startAgentHarnessBackgroundServices(container);
      await startSessionErasureServices(
        container,
        container.get<Logger>('Logger'),
      );
      await preparePolicyStore(container.get<Logger>('Logger'));
      this.logger.info(
        'Enterprise Search Agent services initialized successfully',
      );
    } catch (error) {
      this.logger.error(
        'Failed to initialize Enterprise Search Agent services',
        {
          error: error instanceof Error ? error.message : 'Unknown error',
        },
      );
      throw error;
    }
  }

  static getInstance(): Container {
    if (!this.instance) {
      throw new Error('Service container not initialized');
    }
    return this.instance;
  }

  /** Stops the background loops and gives up their leader leases, ahead of the disposal that closes the stores. */
  static async quiesce(): Promise<void> {
    if (this.instance) await stopAgentHarnessBackgroundServices(this.instance);
  }

  static async dispose(): Promise<void> {
    if (!this.instance) return;
    try {
      await stopAgentHarnessBackgroundServices(this.instance);
      // Get only services that need to be disconnected
      const keyValueStoreService = this.instance.isBound('KeyValueStoreService')
        ? this.instance.get<KeyValueStoreService>('KeyValueStoreService')
        : null;

      // Disconnect services if they have a disconnect method
      if (keyValueStoreService && keyValueStoreService.isConnected()) {
        await keyValueStoreService.disconnect();
        this.logger.info('KeyValueStoreService disconnected successfully');
      }

      this.logger.info(
        'All Enterprise Search services disconnected successfully',
      );
    } catch (error) {
      this.logger.error(
        'Error while disconnecting Enterprise Search services',
        {
          error: error instanceof Error ? error.message : 'Unknown error',
        },
      );
    } finally {
      this.instance = null!;
    }
  }
}
