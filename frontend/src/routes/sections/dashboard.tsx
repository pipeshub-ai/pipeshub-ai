import type { ReactNode } from 'react';

import { lazy, Suspense } from 'react';
import { Outlet, Navigate } from 'react-router-dom';

import { CONFIG } from 'src/config-global';
import { useAdmin } from 'src/context/AdminContext';
import { DashboardLayout } from 'src/layouts/dashboard';

import { LoadingScreen } from 'src/components/loading-screen';

import { ConnectorProvider } from 'src/sections/accountdetails/connectors/context';

import { AuthGuard } from 'src/auth/guard';
import { useAuthContext } from 'src/auth/hooks';

// ----------------------------------------------------------------------
// LAZY LOADED PAGES
// ----------------------------------------------------------------------

// QNA Pages
const ChatBotPage = lazy(() => import('src/pages/dashboard/qna/chatbot'));
const AgentPage = lazy(() => import('src/pages/dashboard/qna/agent'));
const AgentBuilderPage = lazy(() => import('src/pages/dashboard/qna/agent-builder'));
const AgentChatPage = lazy(() => import('src/sections/qna/agents/agent-chat'));

// Account Pages
const CompanyProfile = lazy(() => import('src/pages/dashboard/account/company-profile'));
const UsersAndGroups = lazy(() => import('src/pages/dashboard/account/user-and-groups'));
const GroupDetails = lazy(() => import('src/pages/dashboard/account/group-details'));
const UserProfile = lazy(() => import('src/pages/dashboard/account/user-profile'));
const PersonalProfile = lazy(() => import('src/pages/dashboard/account/personal-profile'));

// Settings Pages
const AuthenticationSettings = lazy(
  () => import('src/pages/dashboard/account/authentication-settings')
);
const MailSettings = lazy(() => import('src/pages/dashboard/account/mail-settings'));
const AiModelsSettings = lazy(() => import('src/pages/dashboard/account/ai-models-settings'));
const PlatformSettings = lazy(() => import('src/pages/dashboard/account/platform-settings'));
const PromptsSettings = lazy(() => import('src/pages/dashboard/account/prompts-settings'));
const SamlSsoConfigPage = lazy(() => import('src/pages/dashboard/account/saml-sso-config'));
const OAuthConfig = lazy(() => import('src/pages/dashboard/account/oauth-config'));
const OAuth2Page = lazy(() => import('src/pages/dashboard/account/oauth2'));
const OAuth2AppDetailPage = lazy(
  () => import('src/pages/dashboard/account/oauth2/oauth2-app-detail')
);
const OAuth2NewAppPage = lazy(() => import('src/pages/dashboard/account/oauth2/oauth2-new-app'));

// Connector Pages
const ConnectorSettings = lazy(
  () => import('src/pages/dashboard/account/connectors/connector-settings')
);
const ConnectorRegistry = lazy(() => import('src/pages/dashboard/account/connectors/registry'));
const ConnectorManagementPage = lazy(
  () => import('src/pages/dashboard/account/connectors/[connectorId]')
);
const ConnectorOAuthCallback = lazy(
  () => import('src/pages/dashboard/account/connectors/oauth-callback')
);

// Toolsets Pages
const ToolsetsSettingsPage = lazy(() => import('src/pages/dashboard/account/toolsets'));
const ToolsetOAuthCallback = lazy(
  () => import('src/pages/dashboard/account/toolsets/oauth-callback')
);

// Knowledge Base Pages
const Collections = lazy(() => import('src/pages/dashboard/knowledgebase/collections'));
const RecordDetails = lazy(() => import('src/pages/dashboard/knowledgebase/record-details'));
const KnowledgeSearch = lazy(() => import('src/pages/dashboard/knowledgebase/knowledge-search'));
const AllRecordsPage = lazy(() => import('src/sections/knowledgebase/all-records-page'));

// ----------------------------------------------------------------------
// GUARD COMPONENTS
// ----------------------------------------------------------------------

/**
 * FullNameGuard - Ensures user has completed their profile
 */
export function FullNameGuard({ children }: { children: ReactNode }) {
  const { user } = useAuthContext();
  const hasFullName = !!(user?.fullName && user.fullName.trim() !== '');

  if (!hasFullName) {
    return <Navigate to="/" replace />;
  }

  return <>{children}</>;
}

/**
 * BusinessRouteGuard - Ensures user has a business account
 */
function BusinessRouteGuard({ children }: { children: ReactNode }) {
  const { user, loading } = useAuthContext();

  // Show loading screen while auth is being checked
  if (loading) {
    return <LoadingScreen />;
  }

  const isBusiness = user?.accountType === 'business' || user?.accountType === 'organization';

  if (!isBusiness) {
    return <Navigate to="/account/individual/profile" replace />;
  }

  return <>{children}</>;
}

/**
 * IndividualRouteGuard - Ensures user has an individual account
 */
function IndividualRouteGuard({ children }: { children: ReactNode }) {
  const { user, loading } = useAuthContext();

  // Show loading screen while auth is being checked
  if (loading) {
    return <LoadingScreen />;
  }

  const isBusiness = user?.accountType === 'business' || user?.accountType === 'organization';

  if (isBusiness) {
    return <Navigate to="/account/company-settings/profile" replace />;
  }

  return <>{children}</>;
}

/**
 * AdminRouteGuard - Ensures user is a business admin
 */
function AdminRouteGuard({ children }: { children: ReactNode }) {
  const { isAdmin, loading: adminLoading, isInitialized } = useAdmin();
  const { user, loading: authLoading } = useAuthContext();

  // Show loading screen while auth or admin status is being checked
  if (authLoading || adminLoading || !isInitialized) {
    return <LoadingScreen />;
  }

  const isBusiness = user?.accountType === 'business' || user?.accountType === 'organization';

  if (!isBusiness) {
    return <Navigate to="/account/individual/profile" replace />;
  }

  if (!isAdmin) {
    return <Navigate to="/account/company-settings/profile" replace />;
  }

  return <>{children}</>;
}

// ----------------------------------------------------------------------
// REDIRECT COMPONENTS
// ----------------------------------------------------------------------

/**
 * AccountTypeRedirect - Redirects based on account type
 */
function AccountTypeRedirect() {
  const { user, loading } = useAuthContext();

  // Show loading screen while auth is being checked
  if (loading) {
    return <LoadingScreen />;
  }

  const isBusiness = user?.accountType === 'business' || user?.accountType === 'organization';

  if (isBusiness) {
    return <Navigate to="/account/company-settings/profile" replace />;
  }
  return <Navigate to="/account/individual/profile" replace />;
}

// ----------------------------------------------------------------------
// ROUTE WRAPPER COMPONENTS
// ----------------------------------------------------------------------

/**
 * WithAuth - Wrapper to conditionally apply auth guards based on CONFIG.auth.skip
 */
const WithAuth = ({ children }: { children: ReactNode }) => {
  if (CONFIG.auth.skip) {
    return <>{children}</>;
  }
  return (
    <AuthGuard>
      <FullNameGuard>{children}</FullNameGuard>
    </AuthGuard>
  );
};

/**
 * ProtectedRoute - Basic authenticated route with full name check
 */
const ProtectedRoute = ({ component: Component }: { component: React.ComponentType }) => (
  <WithAuth>
    <Component />
  </WithAuth>
);

/**
 * BusinessOnlyRoute - Route accessible only to business accounts
 */
const BusinessOnlyRoute = ({ component: Component }: { component: React.ComponentType }) => (
  <WithAuth>
    <BusinessRouteGuard>
      <Component />
    </BusinessRouteGuard>
  </WithAuth>
);

/**
 * IndividualOnlyRoute - Route accessible only to individual accounts
 */
const IndividualOnlyRoute = ({ component: Component }: { component: React.ComponentType }) => (
  <WithAuth>
    <IndividualRouteGuard>
      <Component />
    </IndividualRouteGuard>
  </WithAuth>
);

/**
 * AdminProtectedRoute - Route accessible only to business admins
 */
const AdminProtectedRoute = ({ component: Component }: { component: React.ComponentType }) => (
  <WithAuth>
    <AdminRouteGuard>
      <Component />
    </AdminRouteGuard>
  </WithAuth>
);

// ----------------------------------------------------------------------
// LAYOUT CONFIGURATION
// ----------------------------------------------------------------------

const layoutContent = (
  <ConnectorProvider>
    <DashboardLayout>
      <Suspense fallback={<LoadingScreen />}>
        <Outlet />
      </Suspense>
    </DashboardLayout>
  </ConnectorProvider>
);

// ----------------------------------------------------------------------
// ROUTE CONFIGURATION
// ----------------------------------------------------------------------

export const dashboardRoutes = [
  {
    path: '/',
    element: CONFIG.auth.skip ? <>{layoutContent}</> : <AuthGuard>{layoutContent}</AuthGuard>,
    children: [
      // ----------------------------------------------------------------------
      // OAuth Callback Routes (must be before catch-all routes)
      // ----------------------------------------------------------------------
      {
        path: 'connectors/oauth/callback/:connectorId',
        element: <ConnectorOAuthCallback />,
      },
      {
        path: 'toolsets/oauth/callback/:toolsetType',
        element: <ToolsetOAuthCallback />,
      },

      // ----------------------------------------------------------------------
      // QNA Routes
      // ----------------------------------------------------------------------
      { element: <ChatBotPage key="home" />, index: true },
      { path: ':conversationId', element: <ChatBotPage key="conversation" /> },

      // Agent Routes
      { path: 'agents', element: <AgentPage key="agent" /> },
      { path: 'agents/new', element: <AgentBuilderPage key="agent-builder" /> },
      { path: 'agents/:agentKey', element: <AgentChatPage key="agent-chat" /> },
      { path: 'agents/:agentKey/edit', element: <AgentBuilderPage key="agent-edit" /> },
      { path: 'agents/:agentKey/flow', element: <AgentBuilderPage key="flow-agent-edit" /> },
      {
        path: 'agents/:agentKey/conversations/:conversationId',
        element: <AgentChatPage key="agent-conversation" />,
      },

      // ----------------------------------------------------------------------
      // Knowledge Base Routes
      // ----------------------------------------------------------------------
      { path: 'record/:recordId', element: <RecordDetails /> },
      { path: 'all-records', element: <ProtectedRoute component={AllRecordsPage} /> },
      {
        path: 'knowledge-search',
        element: <ProtectedRoute component={KnowledgeSearch} />,
      },
      {
        path: 'collections',
        element: <ProtectedRoute component={Collections} />,
      },

      // ----------------------------------------------------------------------
      // Connector Routes (Legacy redirect)
      // ----------------------------------------------------------------------
      {
        path: 'connectors',
        element: <Navigate to="/account/individual/settings/connector" replace />,
      },
      // ----------------------------------------------------------------------
      // Account Routes
      // ----------------------------------------------------------------------
      {
        path: 'account',
        children: [
          // Redirect /account to appropriate profile based on account type
          {
            index: true,
            element: <ProtectedRoute component={AccountTypeRedirect} />,
          },

          // ----------------------------------------------------------------------
          // Business Account Routes
          // ----------------------------------------------------------------------
          {
            path: 'company-settings/profile',
            element: <BusinessOnlyRoute component={CompanyProfile} />,
          },
          {
            path: 'company-settings/personal-profile',
            element: <BusinessOnlyRoute component={PersonalProfile} />,
          },
          {
            path: 'company-settings/user-profile/:id',
            element: <AdminProtectedRoute component={UserProfile} />,
          },
          {
            path: 'company-settings/groups/:id',
            element: <AdminProtectedRoute component={GroupDetails} />,
          },
          // Business Settings & Management Routes
          {
            path: 'company-settings',
            children: [
              // Redirect /account/company-settings to profile
              {
                index: true,
                element: <Navigate to="/account/company-settings/profile" replace />,
              },

              // User & Group Management (Admin only)
              {
                path: 'users',
                element: <AdminProtectedRoute component={UsersAndGroups} />,
              },
              {
                path: 'groups',
                element: <AdminProtectedRoute component={UsersAndGroups} />,
              },
              {
                path: 'invites',
                element: <AdminProtectedRoute component={UsersAndGroups} />,
              },
              {
                path: 'blocked-users',
                element: <AdminProtectedRoute component={UsersAndGroups} />,
              },
              // Business Admin Settings
              {
                path: 'settings',
                children: [
                  // Redirect /account/company-settings/settings to authentication
                  {
                    index: true,
                    element: (
                      <Navigate to="/account/company-settings/settings/authentication" replace />
                    ),
                  },

                  // Authentication Settings
                  {
                    path: 'authentication',
                    children: [
                      {
                        index: true,
                        element: <AdminProtectedRoute component={AuthenticationSettings} />,
                      },
                      {
                        path: 'saml',
                        element: <AdminProtectedRoute component={SamlSsoConfigPage} />,
                      },
                    ],
                  },
                  {
                    path: 'mail',
                    children: [
                      {
                        index: true,
                        element: <AdminProtectedRoute component={MailSettings} />,
                      },
                    ],
                  },

                  // Connector Settings
                  {
                    path: 'connector',
                    children: [
                      {
                        index: true,
                        element: <BusinessOnlyRoute component={ConnectorSettings} />,
                      },
                      {
                        path: 'registry',
                        element: <BusinessOnlyRoute component={ConnectorRegistry} />,
                      },
                      {
                        path: 'oauth/callback/:connectorId',
                        element: <BusinessOnlyRoute component={ConnectorOAuthCallback} />,
                      },
                      {
                        path: ':connectorId',
                        element: <BusinessOnlyRoute component={ConnectorManagementPage} />,
                      },
                    ],
                  },

                  // Toolsets Settings
                  {
                    path: 'toolsets',
                    element: <BusinessOnlyRoute component={ToolsetsSettingsPage} />,
                  },

                  // OAuth Configuration (connector OAuth configs)
                  {
                    path: 'oauth-config',
                    element: <AdminProtectedRoute component={OAuthConfig} />,
                  },

                  // OAuth 2.0 (Pipeshub OAuth provider apps)
                  {
                    path: 'oauth2',
                    element: <AdminProtectedRoute component={OAuth2Page} />,
                  },
                  {
                    path: 'oauth2/new',
                    element: <AdminProtectedRoute component={OAuth2NewAppPage} />,
                  },
                  {
                    path: 'oauth2/:appId',
                    element: <AdminProtectedRoute component={OAuth2AppDetailPage} />,
                  },

                  // AI Models Settings
                  {
                    path: 'ai-models',
                    element: <AdminProtectedRoute component={AiModelsSettings} />,
                  },

                  // Platform Settings
                  {
                    path: 'platform',
                    element: <AdminProtectedRoute component={PlatformSettings} />,
                  },

                  // Prompts Settings
                  {
                    path: 'prompts',
                    element: <AdminProtectedRoute component={PromptsSettings} />,
                  },
                ],
              },
            ],
          },

          // ----------------------------------------------------------------------
          // Individual Account Routes
          // ----------------------------------------------------------------------
          {
            path: 'individual',
            children: [
              // Redirect /account/individual to profile
              {
                index: true,
                element: <Navigate to="/account/individual/profile" replace />,
              },

              // Personal Profile
              {
                path: 'profile',
                element: <IndividualOnlyRoute component={PersonalProfile} />,
              },
              // Individual Settings
              {
                path: 'settings',
                children: [
                  // Redirect /account/individual/settings to authentication
                  {
                    index: true,
                    element: <Navigate to="/account/individual/settings/authentication" replace />,
                  },

                  // Authentication Settings
                  {
                    path: 'authentication',
                    children: [
                      {
                        index: true,
                        element: <IndividualOnlyRoute component={AuthenticationSettings} />,
                      },
                      {
                        path: 'config-saml',
                        element: <IndividualOnlyRoute component={SamlSsoConfigPage} />,
                      },
                    ],
                  },

                  // Connector Settings
                  {
                    path: 'connector',
                    children: [
                      {
                        index: true,
                        element: <IndividualOnlyRoute component={ConnectorSettings} />,
                      },
                      {
                        path: 'registry',
                        element: <IndividualOnlyRoute component={ConnectorRegistry} />,
                      },
                      {
                        path: 'oauth/callback/:connectorId',
                        element: <IndividualOnlyRoute component={ConnectorOAuthCallback} />,
                      },
                      {
                        path: ':connectorId',
                        element: <IndividualOnlyRoute component={ConnectorManagementPage} />,
                      },
                    ],
                  },

                  // Toolsets Settings
                  {
                    path: 'toolsets',
                    element: <IndividualOnlyRoute component={ToolsetsSettingsPage} />,
                  },

                  // OAuth Configuration (connector OAuth configs)
                  {
                    path: 'oauth-config',
                    element: <IndividualOnlyRoute component={OAuthConfig} />,
                  },

                  // AI Models Settings
                  {
                    path: 'ai-models',
                    element: <IndividualOnlyRoute component={AiModelsSettings} />,
                  },

                  // Platform Settings
                  {
                    path: 'platform',
                    element: <IndividualOnlyRoute component={PlatformSettings} />,
                  },

                  // Prompts Settings
                  {
                    path: 'prompts',
                    element: <IndividualOnlyRoute component={PromptsSettings} />,
                  },
                ],
              },
            ],
          },
        ],
      },
    ],
  },
];
