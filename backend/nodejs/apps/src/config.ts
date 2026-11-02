// --- Global singletons (Mongoose models) ---
export { Users, User } from './modules/user_management/schema/users.schema';
export { Org } from './modules/user_management/schema/org.schema';

// --- Shared cross-cutting middleware ---
export { AuthMiddleware } from './libs/middlewares/auth.middleware';
// Session rules for connections that outlive a single request (the desktop socket).
export {
  findSessionRefusal,
  findSessionRefusals,
} from './libs/utils/user-session.utils';

// --- Storage & connectors ---
export { StorageContainer } from './modules/storage/container/storage.container';
export { createStorageRouter } from './modules/storage/routes/storage.routes';
export { createConnectorRouter } from './modules/tokens_manager/routes/connectors.routes';

// --- User management ---
export { createUserRouter } from './modules/user_management/routes/users.routes';
export { createUserGroupRouter } from './modules/user_management/routes/userGroups.routes';
export { createOrgRouter } from './modules/user_management/routes/org.routes';
export { UserManagerContainer } from './modules/user_management/container/userManager.container';

// --- Auth ---
export { AuthServiceContainer } from './modules/auth/container/authService.container';
export { createUserAccountRouter } from './modules/auth/routes/userAccount.routes';
export { createOrgAuthConfigRouter } from './modules/auth/routes/orgAuthConfig.routes';

// --- Mail ---
export { MailServiceContainer } from './modules/mail/container/mailService.container';
export { createMailServiceRouter } from './modules/mail/routes/mail.routes';

// --- Enterprise search ---
export { EnterpriseSearchAgentContainer } from './modules/enterprise_search/container/es.container';
export {
  createConversationalRouter,
  createSemanticSearchRouter,
  createRetrieveRouter,
  createAgentConversationalRouter,
  createChatSpeechRouter,
} from './modules/enterprise_search/routes/es.routes';

// --- Knowledge base ---
export { KnowledgeBaseContainer } from './modules/knowledge_base/container/kb_container';
export { createKnowledgeBaseRouter } from './modules/knowledge_base/routes/kb.routes';

// --- Configuration manager ---
export { ConfigurationManagerContainer } from './modules/configuration_manager/container/cm_container';
export { createConfigurationManagerRouter } from './modules/configuration_manager/routes/cm_routes';
export { MigrationService } from './modules/configuration_manager/services/migration.service';

// --- Tokens manager ---
export { TokenManagerContainer } from './modules/tokens_manager/container/token-manager.container';

// --- Auth (workspace auth routes) ---
export { createWorkspaceAuthRouter } from './modules/auth/routes/workspaceAuth.routes';

// --- User management (feature flags stub, org config stub) ---
export { createFeatureFlagRouter } from './modules/user_management/routes/featureFlag.routes';
export { createOrgConfigRouter } from './modules/user_management/routes/orgConfig.routes';
export { createRequestRouter } from './modules/user_management/routes/request.routes';

// --- Service account delegation (Community stub, real router in ee/) ---
export { createDelegationRouter } from './modules/oauth_provider/routes/delegation.routes';

/**
 * Whether this edition offers delegation: a service account acting as a named
 * person, rather than with its own access.
 *
 * `false` here, `true` in the Enterprise build. Read by the two places that
 * are shared between editions — the token endpoint and OpenID discovery — so
 * the Community build refuses the capability outright rather than merely
 * lacking a screen to reach it. Without this, an allow-list row written
 * straight into the database would be enough to turn delegation on.
 *
 * A build-time constant on purpose. Nothing is looked up, so nothing can fail
 * at request time, which is the rule for anything that sits in the path every
 * request takes.
 *
 * It is not a lock. Anyone running their own build can change it, and the
 * Enterprise value of this feature was never the mechanism — it is the audit
 * trail, the organisation-wide view and the alerting, none of which are in
 * this repository at all.
 */
export const DELEGATION_AVAILABLE = false;

// --- OAuth Apps ---
export { OAuthAppsContainer } from './modules/oauth_apps/container/oauth_apps.container';
export { createOAuthAppsRouter } from './modules/oauth_apps/routes/oauth_apps.routes';

// --- Edition boot hooks ---
export { noopEditionBoot as editionBoot } from './libs/edition/edition-boot';
