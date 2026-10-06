import { Container } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { ConfigurationManagerConfig } from '../../configuration_manager/config/config';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { AppConfig } from '../../tokens_manager/config/config';
import { AuthorizationService } from '../../authz/authz.service';
import { bindAuthzUserRoutes } from '../../authz/authz.bindings';
import { DecisionCache } from '../../authz/cache/decision-cache';
import { ChatContentCheckService } from '../../authz/chat-content-check.service';
import { ChatAccessLoader } from '../../authz/loaders/chat.loader';
import { ChatContentLoader } from '../../authz/loaders/chat-content.loader';
import { SubjectTeamResolver } from '../../authz/subject-team.resolver';
import { PlatformFeatureFlags } from '../../configuration_manager/services/platform-feature-flags.service';
import { ProjectServiceAccessAdapter } from '../../projects/services/project-access.adapter';
import { getTeamIdsCache } from '../../user_management/services/cached-team-directory';
import { teamDirectoryFor } from '../../user_management/services/team-directory.service';
import { MongoUserDirectory } from '../../user_management/services/user-directory.service';
import { COLLAB_TYPES } from '../services/collaboration/collab.types';
import { IAgentReadinessPort } from '../services/collaboration/readiness/agent-readiness.port';
import { HttpAgentReadinessAdapter } from '../services/collaboration/readiness/http-agent-readiness.adapter';
import { JwtServiceTokenIssuer } from '../../../libs/services/service-token.issuer';
import { ConversationGuards } from '../services/collaboration/http/conversation-guards';
import { IRunLeaseManager } from '../services/collaboration/leases/lease.types';
import { MongoRunLeaseManager } from '../services/collaboration/leases/run-lease.manager';
import {
  IConversationMessageFeed,
  MongoConversationMessageFeed,
} from '../services/collaboration/persistence/message-feed';
import {
  MongoAuditWriter,
  IAuditWriter,
} from '../../../libs/audit/audit.writer';
import {
  ICollaboratorRepository,
  MongoCollaboratorRepository,
} from '../services/collaboration/persistence/collaborator.repository';
import {
  IReadStateRepository,
  MongoReadStateRepository,
} from '../services/collaboration/persistence/read-state.repository';
import {
  INotificationPreferencesRepository,
  MongoNotificationPreferencesRepository,
} from '../../notification/repository/notification-preferences.repository';
import { ConversationTurnDeps } from '../services/collaboration/turn/turn-deps';
import { OutboxCollaborationNotifier } from '../services/collaboration/notify/collaboration-notifier';
import {
  IConversationEventProducers,
  useConversationEventProducers,
} from '../services/collaboration/notify/conversation-event-producers';
import { MongoNotificationArchiver } from '../services/collaboration/notify/notification-archiver';
import { NotificationOutboxWriter } from '../services/collaboration/notify/notification-outbox.writer';
import { RecipientResolver } from '../services/collaboration/notify/recipient-resolver';
import { bindCollaboration } from './collaboration.bindings';
import {
  ChatNotificationContext,
  IChatNotificationContext,
} from '../services/collaboration/notify/chat-notification-context';
import {
  HttpAgentDirectory,
} from '../services/collaboration/mentions/agent.directory';
import { MentionTurnGate } from '../services/collaboration/mentions/mention-turn-gate';
import { MentionValidator } from '../services/collaboration/mentions/mention.validator';

const loggerConfig = {
  service: 'Enterprise Search Service',
};

export class EnterpriseSearchAgentContainer {
  protected static instance: Container;
  protected static logger: Logger = Logger.getInstance(loggerConfig);

  static async initialize(
    configurationManagerConfig: ConfigurationManagerConfig,
    appConfig: AppConfig,
  ): Promise<Container> {
    const container = new Container();

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
      const chats = new ChatAccessLoader();
      const projects = new ProjectServiceAccessAdapter();
      const flags = new PlatformFeatureFlags(keyValueStoreService);
      const teams = teamDirectoryFor(appConfig);
      const leases = new MongoRunLeaseManager({
        users: new MongoUserDirectory(),
      });
      const tokens = new JwtServiceTokenIssuer(authTokenService);
      const agentDirectory = new HttpAgentDirectory(
        () => container.get<AppConfig>('AppConfig').aiBackend,
        this.logger,
      );
      const mentionValidator = new MentionValidator({
        users: new MongoUserDirectory(),
        teams,
        agents: agentDirectory,
      });
      const readiness = new HttpAgentReadinessAdapter(
        () => container.get<AppConfig>('AppConfig').aiBackend,
        tokens,
        this.logger,
      );
      const authz = new AuthorizationService({
        chats,
        projects,
        flags,
        cache: new DecisionCache({
          cache: getTeamIdsCache,
          logger: this.logger,
        }),
        teams,
      });
      container
        .bind<ChatContentCheckService>(COLLAB_TYPES.ChatContentCheckService)
        .toConstantValue(
          new ChatContentCheckService({
            authz,
            chats,
            content: new ChatContentLoader(),
            users: new MongoUserDirectory(),
            teams: new SubjectTeamResolver(teams, tokens),
            flags,
            logger: this.logger,
          }),
        );
      container
        .bind<IChatNotificationContext>(COLLAB_TYPES.ChatNotificationContext)
        .toConstantValue(
          new ChatNotificationContext({
            authz,
            teams,
            users: new MongoUserDirectory(),
            flags,
            logger: this.logger,
          }),
        );
      container
        .bind<ConversationGuards>(COLLAB_TYPES.ConversationGuards)
        .toConstantValue(
          new ConversationGuards({
            authz,
            chats,
            projects,
            flags,
            users: new MongoUserDirectory(),
            teams,
            leases,
            readiness,
            mentions: new MentionTurnGate({
              flags,
              validator: mentionValidator,
            }),
          }),
        );
      container
        .bind<IRunLeaseManager>(COLLAB_TYPES.RunLeaseManager)
        .toConstantValue(leases);
      const feed = new MongoConversationMessageFeed();
      container
        .bind<IConversationMessageFeed>(COLLAB_TYPES.MessageFeed)
        .toConstantValue(feed);
      container
        .bind<ConversationTurnDeps>(COLLAB_TYPES.ConversationTurnDeps)
        .toConstantValue({ feed, users: new MongoUserDirectory(), leases });
      container
        .bind<IAgentReadinessPort>(COLLAB_TYPES.AgentReadinessPort)
        .toConstantValue(readiness);
      container
        .bind<ICollaboratorRepository>(COLLAB_TYPES.CollaboratorRepository)
        .toConstantValue(new MongoCollaboratorRepository());
      container
        .bind<IAuditWriter>(COLLAB_TYPES.AuditWriter)
        .toConstantValue(new MongoAuditWriter());
      container
        .bind<IReadStateRepository>(COLLAB_TYPES.ReadStateRepository)
        .toConstantValue(new MongoReadStateRepository());
      container
        .bind<INotificationPreferencesRepository>(
          COLLAB_TYPES.NotificationPreferencesRepository,
        )
        .toConstantValue(new MongoNotificationPreferencesRepository());
      bindAuthzUserRoutes(container, {
        authz,
        chats,
        projects,
        teams,
        tokens,
      });
      bindCollaboration(container, {
        appConfig,
        keyValueStore: keyValueStoreService,
        flags,
        chats,
        projects,
        teams,
        users: new MongoUserDirectory(),
        readiness,
        repo: container.get<ICollaboratorRepository>(
          COLLAB_TYPES.CollaboratorRepository,
        ),
        audit: container.get<IAuditWriter>(COLLAB_TYPES.AuditWriter),
        readState: container.get<IReadStateRepository>(
          COLLAB_TYPES.ReadStateRepository,
        ),
        preferences: container.get<INotificationPreferencesRepository>(
          COLLAB_TYPES.NotificationPreferencesRepository,
        ),
        messages: feed,
        mentionValidator,
        agentDirectory,
        notifier: new OutboxCollaborationNotifier({
          recipients: new RecipientResolver(
            new MongoUserDirectory(),
            teams,
            this.logger,
          ),
          outbox: new NotificationOutboxWriter(),
          archiver: new MongoNotificationArchiver(),
        }),
        logger: this.logger,
      });
      useConversationEventProducers(
        container.get<IConversationEventProducers>(
          COLLAB_TYPES.ConversationEventProducers,
        ),
      );
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

  static async dispose(): Promise<void> {
    try {
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
