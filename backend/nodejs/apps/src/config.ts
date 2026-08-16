// --- Global singletons (Mongoose models) ---
export { Users, User } from './modules/user_management/schema/users.schema';
export { Org } from './modules/user_management/schema/org.schema';

// --- Shared cross-cutting middleware ---
export { AuthMiddleware } from './libs/middlewares/auth.middleware';

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
export { createSamlRouter } from './modules/auth/routes/saml.routes';
export { createOrgAuthConfigRouter } from './modules/auth/routes/orgAuthConfig.routes';
export { SamlController } from './modules/auth/controller/saml.controller';

// --- Mail ---
export { MailServiceContainer } from './modules/mail/container/mailService.container';
export { createMailServiceRouter } from './modules/mail/routes/mail.routes';

// --- Enterprise search ---
export { EnterpriseSearchAgentContainer } from './modules/enterprise_search/container/es.container';
export {
  createConversationalRouter,
  createSemanticSearchRouter,
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

// --- OAuth Apps ---
export { OAuthAppsContainer } from './modules/oauth_apps/container/oauth_apps.container';
export { createOAuthAppsRouter } from './modules/oauth_apps/routes/oauth_apps.routes';
