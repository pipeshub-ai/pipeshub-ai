import type { ReactNode } from 'react';

import { lazy, Suspense } from 'react';
import { Outlet, Navigate } from 'react-router-dom';

import { CONFIG } from 'src/config-global';
import { useAdmin } from 'src/context/AdminContext';
import { DashboardLayout } from 'src/layouts/dashboard';

import { LoadingScreen } from 'src/components/loading-screen';

import { AuthGuard } from 'src/auth/guard';
import { useAuthContext } from 'src/auth/hooks';

// ----------------------------------------------------------------------

// Overview
const ChatBotPage = lazy(() => import('src/pages/dashboard/qna/chatbot'));

// Accountdetails
const CompanyProfile = lazy(() => import('src/pages/dashboard/account/company-profile'));
const UsersAndGroups = lazy(() => import('src/pages/dashboard/account/user-and-groups'));
const GroupDetails = lazy(() => import('src/pages/dashboard/account/group-details'));
const UserProfile = lazy(() => import('src/pages/dashboard/account/user-profile'));
const PersonalProfile = lazy(() => import('src/pages/dashboard/account/personal-profile'));
const ServiceSettings = lazy(() => import('src/pages/dashboard/account/services-settings'));
const AuthenticationSettings = lazy(
  () => import('src/pages/dashboard/account/authentication-settings')
);
const AiModelsSettings = lazy(() => import('src/pages/dashboard/account/ai-models-settings'));
const ConnectorSettings = lazy(
  () => import('src/pages/dashboard/account/connectors/connector-settings')
);

const GoogleWorkspaceBusinessPage = lazy(
  () => import('src/pages/dashboard/account/connectors/googleWorkspace-business-config')
);

const GoogleWorkspaceIndividualPage = lazy(
  () => import('src/pages/dashboard/account/connectors/googleWorkspace-individual-config')
);

const SamlSsoConfigPage = lazy(() => import('src/pages/dashboard/account/saml-sso-config'));

// knowledge-base
const KnowledgeBaseList = lazy(() => import('src/pages/dashboard/knowledgebase/knowledgebase'));
const RecordDetails = lazy(() => import('src/pages/dashboard/knowledgebase/record-details'));
const KnowledgeSearch = lazy(
  () => import('src/pages/dashboard/knowledgebase/knowledgebase-search')
);

// ----------------------------------------------------------------------

// Redirect component based on account type
function AccountTypeRedirect() {
  const { user } = useAuthContext();
  const isBusiness = user?.accountType === 'business' || user?.accountType === 'organization';

  if (isBusiness) {
    return <Navigate to="/account/company-settings/profile" replace />;
  }
  return <Navigate to="/account/individual/profile" replace />;
}

// Guard components
function BusinessRouteGuard({ children }: { children: ReactNode }) {
  const { user } = useAuthContext();
  const isBusiness = user?.accountType === 'business' || user?.accountType === 'organization';

  if (!isBusiness) {
    return <Navigate to="/account/individual/profile" replace />;
  }

  return <>{children}</>;
}

function IndividualRouteGuard({ children }: { children: ReactNode }) {
  const { user } = useAuthContext();
  const isBusiness = user?.accountType === 'business' || user?.accountType === 'organization';

  if (isBusiness) {
    return <Navigate to="/account/company-settings/profile" replace />;
  }

  return <>{children}</>;
}

function AdminRouteGuard({ children }: { children: ReactNode }) {
  const { isAdmin } = useAdmin();
  const { user } = useAuthContext();
  const isBusiness = user?.accountType === 'business' || user?.accountType === 'organization';

  if (!isBusiness) {
    return <Navigate to="/account/individual/profile" replace />;
  }

  if (!isAdmin) {
    return <Navigate to="/account/company-settings/profile" replace />;
  }

  return <>{children}</>;
}

export function FullNameGuard({ children }: { children: ReactNode }) {
  const { user } = useAuthContext();

  // Check if user has a full name
  const hasFullName = !!(user?.fullName && user.fullName.trim() !== '');

  if (!hasFullName) {
    // Redirect to the home page where the dialog will appear
    return <Navigate to="/" replace />;
  }

  // If we're here, the user has a full name and can proceed
  return <>{children}</>;
}

// Route components with guards
const BusinessOnlyRoute = ({ component: Component }: { component: React.ComponentType }) => (
  <AuthGuard>
    <FullNameGuard>
      <BusinessRouteGuard>
        <Component />
      </BusinessRouteGuard>
    </FullNameGuard>
  </AuthGuard>
);

const BusinessAdminOnlyRoute = ({ component: Component }: { component: React.ComponentType }) => (
  <AuthGuard>
    <FullNameGuard>
      <AdminRouteGuard>
        <Component />
      </AdminRouteGuard>
    </FullNameGuard>
  </AuthGuard>
);

const IndividualOnlyRoute = ({ component: Component }: { component: React.ComponentType }) => (
  <AuthGuard>
    <FullNameGuard>
      <IndividualRouteGuard>
        <Component />
      </IndividualRouteGuard>
    </FullNameGuard>
  </AuthGuard>
);

const AdminProtectedRoute = ({ component: Component }: { component: React.ComponentType }) => (
  <AuthGuard>
    <FullNameGuard>
      <AdminRouteGuard>
        <Component />
      </AdminRouteGuard>
    </FullNameGuard>
  </AuthGuard>
);

const ProtectedRoute = ({ component: Component }: { component: React.ComponentType }) => (
  <AuthGuard>
    <FullNameGuard>
      <Component />
    </FullNameGuard>
  </AuthGuard>
);

// Layout with outlet for nested routes
const layoutContent = (
  <DashboardLayout>
    <Suspense fallback={<LoadingScreen />}>
      <Outlet />
    </Suspense>
  </DashboardLayout>
);

export const dashboardRoutes = [
  {
    path: '/',
    element: CONFIG.auth.skip ? <>{layoutContent}</> : <AuthGuard>{layoutContent}</AuthGuard>,
    children: [
      { element: <ChatBotPage key="home" />, index: true },
      { path: ':conversationId', element: <ChatBotPage key="conversation" /> },
      { path: 'record/:recordId', element: <RecordDetails /> },
      {
        path: 'account',
        children: [
          // Catch-all redirect for /account path
          { index: true, element: <ProtectedRoute component={AccountTypeRedirect} /> },

          // Business account routes
          {
            path: 'company-settings/profile',
            element: CONFIG.auth.skip ? (
              <CompanyProfile />
            ) : (
              <BusinessOnlyRoute component={CompanyProfile} />
            ),
          },
          {
            path: 'company-settings/personal-profile',
            element: CONFIG.auth.skip ? (
              <PersonalProfile />
            ) : (
              <BusinessOnlyRoute component={PersonalProfile} />
            ),
          },

          // Admin-only routes (business + admin)
          {
            path: 'company-settings/user-profile/:id',
            element: CONFIG.auth.skip ? (
              <UserProfile />
            ) : (
              <AdminProtectedRoute component={UserProfile} />
            ),
          },
          {
            path: 'company-settings/groups/:id',
            element: CONFIG.auth.skip ? (
              <GroupDetails />
            ) : (
              <AdminProtectedRoute component={GroupDetails} />
            ),
          },
          {
            path: 'company-settings',
            children: [
              // Index route for company-settings
              {
                index: true,
                element: (
                  <ProtectedRoute
                    component={() => <Navigate to="/account/company-settings/profile" replace />}
                  />
                ),
              },

              {
                path: 'users',
                element: CONFIG.auth.skip ? (
                  <UsersAndGroups />
                ) : (
                  <AdminProtectedRoute component={UsersAndGroups} />
                ),
              },
              {
                path: 'groups',
                element: CONFIG.auth.skip ? (
                  <UsersAndGroups />
                ) : (
                  <AdminProtectedRoute component={UsersAndGroups} />
                ),
              },
              {
                path: 'invites',
                element: CONFIG.auth.skip ? (
                  <UsersAndGroups />
                ) : (
                  <AdminProtectedRoute component={UsersAndGroups} />
                ),
              },
              {
                path: 'settings',
                children: [
                  // Index route for company settings
                  {
                    index: true,
                    element: CONFIG.auth.skip ? (
                      <Navigate to="/account/company-settings/settings/authentication" replace />
                    ) : (
                      <FullNameGuard>
                        <AdminRouteGuard>
                          <Navigate
                            to="/account/company-settings/settings/authentication"
                            replace
                          />
                        </AdminRouteGuard>
                      </FullNameGuard>
                    ),
                  },

                  {
                    path: 'authentication',
                    children: [
                      {
                        element: CONFIG.auth.skip ? (
                          <AuthenticationSettings />
                        ) : (
                          <BusinessAdminOnlyRoute component={AuthenticationSettings} />
                        ),
                        index: true,
                      },
                      {
                        path: 'saml',
                        element: CONFIG.auth.skip ? (
                          <SamlSsoConfigPage />
                        ) : (
                          <BusinessAdminOnlyRoute component={SamlSsoConfigPage} />
                        ),
                      },
                    ],
                  },
                  {
                    path: 'connector',
                    children: [
                      {
                        element: CONFIG.auth.skip ? (
                          <ConnectorSettings />
                        ) : (
                          <BusinessAdminOnlyRoute component={ConnectorSettings} />
                        ),
                        index: true,
                      },
                      {
                        path: 'googleWorkspace',
                        element: CONFIG.auth.skip ? (
                          <GoogleWorkspaceBusinessPage />
                        ) : (
                          <BusinessAdminOnlyRoute component={GoogleWorkspaceBusinessPage} />
                        ),
                      },
                    ],
                  },
                  {
                    path: 'services',
                    element: CONFIG.auth.skip ? (
                      <ServiceSettings />
                    ) : (
                      <BusinessAdminOnlyRoute component={ServiceSettings} />
                    ),
                  },
                  {
                    path: 'ai-models',
                    element: CONFIG.auth.skip ? (
                      <AiModelsSettings />
                    ) : (
                      <BusinessAdminOnlyRoute component={AiModelsSettings} />
                    ),
                  },
                ],
              },
            ],
          },

          // Individual account routes
          {
            path: 'individual',
            children: [
              // Index route for individual
              {
                index: true,
                element: (
                  <ProtectedRoute
                    component={() => <Navigate to="/account/individual/profile" replace />}
                  />
                ),
              },

              {
                path: 'profile',
                element: CONFIG.auth.skip ? (
                  <PersonalProfile />
                ) : (
                  <IndividualOnlyRoute component={PersonalProfile} />
                ),
              },
              {
                path: 'settings',
                children: [
                  // Index route for individual settings
                  {
                    index: true,
                    element: CONFIG.auth.skip ? (
                      <Navigate to="/account/individual/settings/authentication" replace />
                    ) : (
                      <FullNameGuard>
                        <IndividualRouteGuard>
                          <Navigate to="/account/individual/settings/authentication" replace />
                        </IndividualRouteGuard>
                      </FullNameGuard>
                    ),
                  },

                  {
                    path: 'authentication',
                    children: [
                      {
                        element: CONFIG.auth.skip ? (
                          <AuthenticationSettings />
                        ) : (
                          <IndividualOnlyRoute component={AuthenticationSettings} />
                        ),
                        index: true,
                      },
                      {
                        path: 'config-saml',
                        element: CONFIG.auth.skip ? (
                          <SamlSsoConfigPage />
                        ) : (
                          <IndividualOnlyRoute component={SamlSsoConfigPage} />
                        ),
                      },
                    ],
                  },
                  {
                    path: 'connector',
                    children: [
                      {
                        element: CONFIG.auth.skip ? (
                          <ConnectorSettings />
                        ) : (
                          <IndividualOnlyRoute component={ConnectorSettings} />
                        ),
                        index: true,
                      },
                      {
                        path: 'googleWorkspace',
                        element: CONFIG.auth.skip ? (
                          <GoogleWorkspaceIndividualPage />
                        ) : (
                          <IndividualOnlyRoute component={GoogleWorkspaceIndividualPage} />
                        ),
                      },
                    ],
                  },
                  {
                    path: 'services',
                    element: CONFIG.auth.skip ? (
                      <ServiceSettings />
                    ) : (
                      <IndividualOnlyRoute component={ServiceSettings} />
                    ),
                  },
                  {
                    path: 'ai-models',
                    element: CONFIG.auth.skip ? (
                      <AiModelsSettings />
                    ) : (
                      <IndividualOnlyRoute component={AiModelsSettings} />
                    ),
                  },
                ],
              },
            ],
          },
        ],
      },
      {
        path: 'knowledge-base',
        children: [
          { path: 'details', element: <ProtectedRoute component={KnowledgeBaseList} /> },
          {
            path: 'search',
            children: [{ element: <ProtectedRoute component={KnowledgeSearch} />, index: true }],
          },
          {
            path: 'company-settings/groups/:id',
            element: CONFIG.auth.skip ? (
              <GroupDetails />
            ) : (
              <AdminProtectedRoute component={GroupDetails} />
            ),
          },
          {
            path: 'company-settings',
            children: [
              {
                path: 'users',
                element: CONFIG.auth.skip ? (
                  <UsersAndGroups />
                ) : (
                  <AdminProtectedRoute component={UsersAndGroups} />
                ),
              },
              {
                path: 'groups',
                element: CONFIG.auth.skip ? (
                  <UsersAndGroups />
                ) : (
                  <AdminProtectedRoute component={UsersAndGroups} />
                ),
              },
              {
                path: 'invites',
                element: CONFIG.auth.skip ? (
                  <UsersAndGroups />
                ) : (
                  <AdminProtectedRoute component={UsersAndGroups} />
                ),
              },
            ],
          },
        ],
      },
    ],
  },
];
