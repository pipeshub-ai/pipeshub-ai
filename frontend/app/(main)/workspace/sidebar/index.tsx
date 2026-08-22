'use client';

import { useState, type ReactNode } from 'react';
import { usePathname } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import { Flex } from '@radix-ui/themes';
import { SidebarBase } from '@/app/components/sidebar';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ICON_SIZE_DEFAULT } from '@/app/components/sidebar';
import { WorkspaceSidebarItem } from './sidebar-item';
import { SectionHeader } from './section-header';
import { CollapsibleSection } from './collapsible-section';
import { useUserStore, selectIsAdmin } from '@/lib/store/user-store';
import { useFeatureFlagsStore, selectMcpEnabled, selectActionsEnabled } from '@/lib/store/feature-flags-store';
import { YourConnectorsIcon } from '@/app/components/ui/your-connectors-icon';

// ========================================
// Route constants (labels resolved at render via i18n)
// ========================================

interface NavItem {
  icon: string;
  labelKey: string;
  route: string;
  adminOnly?: boolean;
  customIcon?: ReactNode;
  /** Only rendered when the named feature flag is enabled. */
  requiresFlag?: 'mcp' | 'actions';
}

const OVERVIEW_ITEMS: NavItem[] = [
  { icon: 'business', labelKey: 'workspace.sidebar.nav.general', route: '/workspace/general' },
  { icon: 'science', labelKey: 'workspace.sidebar.nav.labs', route: '/workspace/labs', adminOnly: true },
];
const DEVELOPER_SETTINGS_ITEMS: NavItem[] = [
  { icon: 'code', labelKey: 'workspace.sidebar.nav.oauth2', route: '/workspace/developer-settings/oauth2' },
  {
    icon: 'vpn_key',
    labelKey: 'workspace.sidebar.nav.personalAccessTokens',
    route: '/workspace/developer-settings/personal-access-tokens',
  },
];

const PEOPLE_SUB_ITEMS = [
  { labelKey: 'workspace.sidebar.nav.users', route: '/workspace/users' },
  { labelKey: 'workspace.sidebar.nav.groups', route: '/workspace/groups', adminOnly: true },
  { labelKey: 'workspace.sidebar.nav.teams', route: '/workspace/teams' },
];

const WORKSPACE_ITEMS: NavItem[] = [
  { icon: 'security', labelKey: 'workspace.sidebar.nav.authentication', route: '/workspace/authentication', adminOnly: true },
  { icon: 'hub', labelKey: 'workspace.sidebar.nav.connectors', route: '/workspace/connectors/team', adminOnly: true },
  { icon: 'device_hub', labelKey: 'workspace.sidebar.nav.mcpServers', route: '/workspace/mcp-servers/team', adminOnly: true, requiresFlag: 'mcp' },
  { icon: 'bolt', labelKey: 'workspace.sidebar.nav.actions', route: '/workspace/actions/team', adminOnly: true, requiresFlag: 'actions' },
  { icon: 'support_agent', labelKey: 'workspace.sidebar.nav.bots', route: '/workspace/bots', adminOnly: true },
  { icon: 'manage_accounts', labelKey: 'workspace.sidebar.nav.services', route: '/workspace/services', adminOnly: true },
  { icon: 'smart_toy', labelKey: 'workspace.sidebar.nav.aiModels', route: '/workspace/ai-models', adminOnly: true },
  { icon: 'mail', labelKey: 'workspace.sidebar.nav.mail', route: '/workspace/mail', adminOnly: true },
  { icon: 'edit_note', labelKey: 'workspace.sidebar.nav.prompts', route: '/workspace/prompts', adminOnly: true },
  { icon: 'travel_explore', labelKey: 'workspace.sidebar.nav.webSearch', route: '/workspace/web-search', adminOnly: true },
];

const PERSONAL_ITEMS: NavItem[] = [
  { icon: 'person', labelKey: 'workspace.sidebar.nav.profile', route: '/workspace/profile' },
  { icon: '', labelKey: 'workspace.sidebar.nav.yourConnectors', route: '/workspace/connectors/personal', customIcon: <YourConnectorsIcon size={ICON_SIZE_DEFAULT} color="var(--slate-11)" /> },
  { icon: 'bolt', labelKey: 'workspace.sidebar.nav.yourActions', route: '/workspace/actions/personal', requiresFlag: 'actions' },
  { icon: 'psychology', labelKey: 'workspace.sidebar.nav.yourSkills', route: '/workspace/skills/personal' },
  { icon: 'device_hub', labelKey: 'workspace.sidebar.nav.yourMcpServers', route: '/workspace/mcp-servers/personal', requiresFlag: 'mcp' },
  { icon: 'archive', labelKey: 'workspace.sidebar.nav.archivedChats', route: '/workspace/archived-chats' },
];

const PEOPLE_ROUTES = PEOPLE_SUB_ITEMS.map((item) => item.route);

// ========================================
// Component
// ========================================

/**
 * Workspace sidebar — settings navigation with collapsible "People" section.
 *
 * Uses `SidebarBase` shell (no header, no footer).
 * Active item determined from current pathname.
 */
export default function WorkspaceSidebar() {
  const rawPathname = usePathname();
  const { t } = useTranslation();
  const isAdmin = useUserStore(selectIsAdmin);
  const mcpEnabled = useFeatureFlagsStore(selectMcpEnabled);
  const actionsEnabled = useFeatureFlagsStore(selectActionsEnabled);

  // Normalize trailing slash (trailingSlash: true in next.config)
  const pathname = rawPathname.endsWith('/') && rawPathname !== '/'
    ? rawPathname.slice(0, -1)
    : rawPathname;

  const [isPeopleExpanded, setIsPeopleExpanded] = useState(
    PEOPLE_ROUTES.some((route) => pathname.startsWith(route))
  );

  const isNavItemVisible = (item: NavItem) =>
    (isAdmin || !item.adminOnly) &&
    (item.requiresFlag !== 'mcp' || mcpEnabled) &&
    (item.requiresFlag !== 'actions' || actionsEnabled);

  const allRoutes = [
    ...OVERVIEW_ITEMS.map((item) => item.route),
    ...(isAdmin ? WORKSPACE_ITEMS.filter(isNavItemVisible).map((item) => item.route) : []),
    ...DEVELOPER_SETTINGS_ITEMS.map((item) => item.route),
    ...PERSONAL_ITEMS.filter(isNavItemVisible).map((item) => item.route),
    ...PEOPLE_SUB_ITEMS.map((item) => item.route),
    '/workspace/actions',
  ];

  const isActive = (route: string) => {
    if (pathname === route) return true;
    if (pathname.startsWith(route + '/')) {
      // Don't match if a more specific route exists that also matches
      const hasMoreSpecificRoute = allRoutes.some(
        (r) => r !== route && r.startsWith(route + '/') && (pathname === r || pathname.startsWith(r + '/'))
      );
      return !hasMoreSpecificRoute;
    }
    return false;
  };
  const isPeopleChildActive = PEOPLE_ROUTES.some((route) => pathname.startsWith(route));

  const visibleOverviewItems = OVERVIEW_ITEMS.filter(isNavItemVisible);
  const visibleWorkspaceItems = WORKSPACE_ITEMS.filter(isNavItemVisible);
  const visiblePeopleItems = PEOPLE_SUB_ITEMS.filter((item) => isAdmin || !item.adminOnly);
  const visiblePersonalItems = PERSONAL_ITEMS.filter(isNavItemVisible);

  return (
    <SidebarBase>
      <Flex direction="column" gap="4">
        {/* ── Back to app ── */}
        <WorkspaceSidebarItem
          icon={<MaterialIcon name="arrow_back" size={ICON_SIZE_DEFAULT} color="var(--slate-11)" />}
          label={t('workspace.sidebar.backToApp')}
          href="/chat/"
        />

        {/* ── Overview section ── */}
        <Flex direction="column" gap="1">
          <SectionHeader title={t('workspace.sidebar.sections.overview')} />
          {visibleOverviewItems.map((item) => (
            <WorkspaceSidebarItem
              key={item.route}
              icon={<MaterialIcon name={item.icon} size={ICON_SIZE_DEFAULT} color="var(--slate-11)" />}
              label={t(item.labelKey)}
              href={`${item.route}/`}
              isActive={isActive(item.route)}
            />
          ))}

          {/* People collapsible — visible to all, sub-items filtered by role */}
          {visiblePeopleItems.length > 0 && (
            <CollapsibleSection
              icon="groups"
              label={t('workspace.sidebar.sections.people')}
              isExpanded={isPeopleExpanded}
              onToggle={() => setIsPeopleExpanded((prev) => !prev)}
              hasActiveChild={isPeopleChildActive}
            >
              {visiblePeopleItems.map((item) => (
                <WorkspaceSidebarItem
                  key={item.route}
                  label={t(item.labelKey)}
                  href={`${item.route}/`}
                  isActive={isActive(item.route)}
                  paddingLeft={36}
                />
              ))}
            </CollapsibleSection>
          )}
        </Flex>

        {/* ── Workspace section ── */}
        {isAdmin && (
          <Flex direction="column" gap="1">
            <SectionHeader title={t('workspace.sidebar.sections.workspace')} />
            {visibleWorkspaceItems.map((item) => (
              <WorkspaceSidebarItem
                key={item.route}
                icon={<MaterialIcon name={item.icon} size={ICON_SIZE_DEFAULT} color="var(--slate-11)" />}
                label={t(item.labelKey)}
                href={`${item.route}/`}
                isActive={isActive(item.route)}
              />
            ))}
          </Flex>
        )}
        <Flex direction="column" gap="1">
          <SectionHeader title={t('workspace.sidebar.sections.developerSettings')} />
          {DEVELOPER_SETTINGS_ITEMS.map((item) => (
            <WorkspaceSidebarItem
              key={item.route}
              icon={<MaterialIcon name={item.icon} size={ICON_SIZE_DEFAULT} color="var(--slate-11)" />}
              label={t(item.labelKey)}
              href={`${item.route}/`}
              isActive={isActive(item.route)}
            />
          ))}
        </Flex>

        {/* ── Personal section ── */}
        <Flex direction="column" gap="1">
          <SectionHeader title={t('workspace.sidebar.sections.personal')} />
          {visiblePersonalItems.map((item) => (
            <WorkspaceSidebarItem
              key={item.route}
              icon={item.customIcon ?? <MaterialIcon name={item.icon} size={ICON_SIZE_DEFAULT} color="var(--slate-11)" />}
              label={t(item.labelKey)}
              href={`${item.route}/`}
              isActive={isActive(item.route)}
            />
          ))}
        </Flex>
      </Flex>
    </SidebarBase>
  );
}
