import { Container } from 'inversify';
import { IAuditWriter } from '../../../libs/audit/audit.writer';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { IScopedChatLoader } from '../../authz/ports';
import { IProjectAccessPort } from '../../authz/ports/project-access.port';
import { isSmtpConfigured } from '../../configuration_manager/utils/smtp-status';
import { IFeatureFlags } from '../../configuration_manager/services/platform-feature-flags.service';
import { INotificationPreferencesRepository } from '../../notification/repository/notification-preferences.repository';
import {
  IOrgDirectory,
  MongoOrgDirectory,
} from '../../user_management/services/org-directory.service';
import { ITeamDirectory } from '../../user_management/services/team-directory.service';
import {
  ConnectorTeamLookup,
  ITeamLookup,
} from '../../user_management/services/team-lookup.service';
import { IUserDirectory } from '../../user_management/services/user-directory.service';
import { AppConfig } from '../../tokens_manager/config/config';
import { CollaboratorsController } from '../controller/collaborators.controller';
import { IAgentDirectory, IAgentProfiles } from '../services/collaboration/mentions/agent.directory';
import { isReplicaSet } from '../../../libs/utils/replica-set';
import { MentionsController } from '../controller/mentions.controller';
import { MentionablesService } from '../services/collaboration/mentions/mentionables.service';
import { IMentionValidator } from '../services/collaboration/mentions/mention.validator';
import { NoteService } from '../services/collaboration/mentions/note.service';
import { COLLAB_TYPES } from '../services/collaboration/collab.types';
import {
  ConversationCollaborationService,
  IConversationCollaborationService,
} from '../services/collaboration/conversation-collaboration.service';
import {
  ConversationFeedService,
  IConversationFeedService,
} from '../services/collaboration/feed/conversation-feed.service';
import { OwnerActivity } from '../services/collaboration/http/owner-activity';
import { LegacySharing } from '../services/collaboration/legacy/legacy-sharing';
import { MongoMutationRunner } from '../services/collaboration/mutation/mutation-runner';
import { MutationEffects } from '../services/collaboration/mutation/mutation-effects';
import { RecipientResolver } from '../services/collaboration/notify/recipient-resolver';
import { CollaborationEmailIntents } from '../services/collaboration/notify/collaboration-email-intents';
import { ICollaborationNotifier } from '../services/collaboration/notify/collaboration-notifier';
import {
  ConversationEventProducers,
  IConversationEventProducers,
} from '../services/collaboration/notify/conversation-event-producers';
import {
  IChatAudienceRepository,
  MongoChatAudienceRepository,
} from '../services/collaboration/persistence/chat-audience.repository';
import { ICollaboratorRepository } from '../services/collaboration/persistence/collaborator.repository';
import { IConversationMessageFeed } from '../services/collaboration/persistence/message-feed';
import { IReadStateRepository } from '../services/collaboration/persistence/read-state.repository';
import { PrincipalResolver } from '../services/collaboration/principals/principal-resolver';
import { IAgentReadinessPort } from '../services/collaboration/readiness/agent-readiness.port';
import {
  ConversationReadinessService,
  IConversationReadinessService,
} from '../services/collaboration/readiness/conversation-readiness.service';
import { CollaboratorListProjector } from '../services/collaboration/views/collaborator-list.projector';
import { Logger } from '../../../libs/services/logger.service';

export interface CollaborationBindingDeps {
  appConfig: AppConfig;
  keyValueStore: KeyValueStoreService;
  flags: IFeatureFlags;
  chats: IScopedChatLoader;
  projects: IProjectAccessPort;
  teams: ITeamDirectory;
  users: IUserDirectory;
  readiness: IAgentReadinessPort;
  repo: ICollaboratorRepository;
  audit: IAuditWriter;
  readState: IReadStateRepository;
  preferences: INotificationPreferencesRepository;
  messages: IConversationMessageFeed;
  notifier: ICollaborationNotifier;
  logger: Logger;
  /** Replaceable collaborators; the connector, org and SMTP lookups by default. */
  teamLookup?: ITeamLookup;
  orgs?: IOrgDirectory;
  audience?: IChatAudienceRepository;
  smtpConfigured?: () => Promise<boolean>;
  /** Shared with the guards' mention gate, so both see one agent-directory cache. */
  mentionValidator: IMentionValidator;
  /** Offers the agents the validator would accept in the @ picker (agent builder flag). */
  agentDirectory?: IAgentDirectory & IAgentProfiles;
}

/** Binds the collaboration service, its collaborators and the controller. */
export function bindCollaboration(
  container: Container,
  deps: CollaborationBindingDeps,
): void {
  const rsAvailable = isReplicaSet();
  container
    .bind<IFeatureFlags>(COLLAB_TYPES.FeatureFlags)
    .toConstantValue(deps.flags);
  container
    .bind<ICollaborationNotifier>(COLLAB_TYPES.CollaborationNotifier)
    .toConstantValue(deps.notifier);
  const effects = new MutationEffects(deps.audit, deps.notifier);
  const emailIntents = new CollaborationEmailIntents({
    isSmtpConfigured:
      deps.smtpConfigured ?? (() => isSmtpConfigured(deps.keyValueStore)),
    flags: deps.flags,
    preferences: deps.preferences,
    users: deps.users,
    orgs: deps.orgs ?? new MongoOrgDirectory(),
  });
  const producers = new ConversationEventProducers({
    audience: deps.audience ?? new MongoChatAudienceRepository(),
    readState: deps.readState,
    preferences: deps.preferences,
    notifier: deps.notifier,
    effects,
    recipients: new RecipientResolver(deps.users, deps.teams),
    emailIntents,
    logger: deps.logger,
  });
  container
    .bind<IConversationEventProducers>(COLLAB_TYPES.ConversationEventProducers)
    .toConstantValue(producers);
  const principals = new PrincipalResolver(deps.users, deps.teams);
  container.bind(COLLAB_TYPES.PrincipalResolver).toConstantValue(principals);
  const service = new ConversationCollaborationService({
    repo: deps.repo,
    effects,
    runner: new MongoMutationRunner(rsAvailable),
    principals,
    projector: new CollaboratorListProjector(
      deps.users,
      deps.teams,
      deps.teamLookup ?? new ConnectorTeamLookup(deps.appConfig),
    ),
    emailIntents,
    chats: deps.chats,
    projects: deps.projects,
    flags: deps.flags,
    legacy: new LegacySharing(deps.appConfig.iamBackend, rsAvailable),
  });
  container
    .bind<IConversationCollaborationService>(COLLAB_TYPES.CollaborationService)
    .toConstantValue(service);
  container
    .bind<IConversationFeedService>(COLLAB_TYPES.FeedService)
    .toConstantValue(
      new ConversationFeedService(deps.messages, deps.users, deps.readState),
    );
  container
    .bind<IConversationReadinessService>(COLLAB_TYPES.ReadinessService)
    .toConstantValue(
      new ConversationReadinessService(
        deps.readiness,
        deps.projects,
        deps.teams,
        new OwnerActivity(deps.users, deps.logger),
      ),
    );
  container
    .bind<MentionsController>(COLLAB_TYPES.MentionsController)
    .toConstantValue(
      new MentionsController(
        new NoteService(deps.mentionValidator, () => producers),
        new MentionablesService(
          deps.users,
          deps.teamLookup ?? new ConnectorTeamLookup(deps.appConfig),
          deps.agentDirectory,
          deps.flags,
        ),
      ),
    );
  container
    .bind<CollaboratorsController>(COLLAB_TYPES.CollaboratorsController)
    .toConstantValue(
      new CollaboratorsController(
        service,
        container.get<IConversationFeedService>(COLLAB_TYPES.FeedService),
        container.get<IConversationReadinessService>(
          COLLAB_TYPES.ReadinessService,
        ),
      ),
    );
}
