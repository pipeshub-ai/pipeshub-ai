// Pages with implementations

export { default as LoginPage } from '@/app/(public)/login/loginPage';
export { default as SignUpPage } from '@/app/(public)/sign-up/signUpPage';
export { default as WorkspaceLayout } from '@/app/(main)/workspace/workspaceLayout';
export { default as OnboardingPage } from '@/app/(main)/onboarding/onboardingPage';
export { default as AiModelsPage } from '@/app/(main)/workspace/ai-models/aiModelsPage';
export { default as AuthenticationPage } from '@/app/(main)/workspace/authentication/authenticationPage';
export { default as GroupsPage } from '@/app/(main)/workspace/groups/groupsEnterprisePlaceholder';
export { default as ConnectorsTeamPage } from '@/app/(main)/workspace/connectors/team/connectorsTeamPage';
export { default as ConnectorsPersonalPage } from '@/app/(main)/workspace/connectors/personal/connectorsPersonalPage';
export { default as ActionsTeamPage } from '@/app/(main)/workspace/actions/team/actionsTeamPage';
export { default as ProfilePage } from '@/app/(main)/workspace/profile/profilePage';
export { default as SamlSsoSuccessPage } from '@/app/(public)/auth/sign-in/samlSso/success/samlSsoSuccessPage';

// Pages without implementations fall back to Next.js 404
export { notFound as TenantsPage } from 'next/navigation';
export { notFound as TenantsUsersPage } from 'next/navigation';
export { notFound as TenantsGroupsPage } from 'next/navigation';
export { notFound as OrgsPage } from 'next/navigation';
export { notFound as OrgsUsersPage } from 'next/navigation';
export { notFound as OrgsGroupsPage } from 'next/navigation';
export { notFound as OrgsFeatureFlagsPage } from 'next/navigation';
export { notFound as OAuthAppsPage } from 'next/navigation';
export { notFound as GroupsPermissionsPage } from 'next/navigation';
export { notFound as GithubAuthCallbackPage } from 'next/navigation';
