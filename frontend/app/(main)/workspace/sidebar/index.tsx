'use client';

import { useState, type MouseEvent } from 'react';
import { usePathname } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import { Badge, Box, Flex, Text } from '@radix-ui/themes';
import { SidebarBase } from '@/app/components/sidebar';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ICON_SIZE_DEFAULT } from '@/app/components/sidebar';
import { WorkspaceSidebarItem } from './sidebar-item';
import { SectionHeader } from './section-header';
import { CollapsibleSection } from './collapsible-section';
import { SidebarHeaderActions } from '@/app/(main)/chat/sidebar/header';
import { useUserStore, selectIsAdmin } from '@/lib/store/user-store';
import {
  useFeatureFlagsStore,
  selectMcpEnabled,
  selectActionsEnabled,
  selectSkillsEnabled,
  selectScheduledRunsEnabled,
  selectAgentHarnessOn,
  selectAgentHarnessResumeEnabled,
  selectEngineRunsEnabled,
  type FeatureFlagGate,
} from '@/lib/store/feature-flags-store';
import { YourConnectorsIcon } from '@/app/components/ui/your-connectors-icon';
import { BetaBadge } from '@/app/components/ui/beta-badge';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { useEnterprisePrompt } from '@/lib/hooks/use-enterprise-prompt';
import { settingsScopeFor } from './settings-scope';
import { useMobileSidebarStore } from '@/lib/store/mobile-sidebar-store';
import { getApiBaseUrl } from '@/lib/utils/api-base-url';
import { workspaceNavExtras } from '@/config';
import { mergeWorkspaceNav, placeWorkspaceExtras, type NavItem } from './workspace-nav';
import { ENTERPRISE_FEATURES, type EnterpriseSection } from '@/app/(main)/workspace/enterprise/enterprise-feature-pages';
import { ENTERPRISE_OVERVIEW_ROUTE } from '@/lib/constants/enterprise';
import { EnterpriseSidebarEntry } from './enterprise-sidebar-entry';
import { useFeedbackDialogStore } from '@/app/(main)/feedback/store';
import { useSupportDialogStore } from '@/app/(main)/support/store';

// ========================================
// Route constants (labels resolved at render via i18n)
// ========================================

const BUILT_IN_OVERVIEW_ITEMS: NavItem[] = [
  { icon: 'business', labelKey: 'workspace.sidebar.nav.general', route: '/workspace/general' },
  { icon: 'science', labelKey: 'workspace.sidebar.nav.labs', route: '/workspace/labs', adminOnly: true },
];
const BUILT_IN_DEVELOPER_SETTINGS_ITEMS: NavItem[] = [
  { icon: 'code', labelKey: 'workspace.sidebar.nav.oauth2', route: '/workspace/developer-settings/oauth2' },
  {
    icon: 'vpn_key',
    labelKey: 'workspace.sidebar.nav.personalAccessTokens',
    route: '/workspace/developer-settings/personal-access-tokens',
    personal: true,
  },
];

const PEOPLE_SUB_ITEMS = [
  { labelKey: 'workspace.sidebar.nav.users', route: '/workspace/users' },
  { labelKey: 'workspace.sidebar.nav.teams', route: '/workspace/teams' },
];

const BUILT_IN_AI_ITEMS: NavItem[] = [
  { icon: 'smart_toy', labelKey: 'workspace.sidebar.nav.aiModels', route: '/workspace/ai-models', adminOnly: true },
  { icon: 'admin_panel_settings', labelKey: 'workspace.sidebar.nav.aiSettings', route: '/workspace/ai-access', adminOnly: true, requiresFlag: 'agentHarness', beta: true },
  { icon: 'edit_note', labelKey: 'workspace.sidebar.nav.prompts', route: '/workspace/prompts', adminOnly: true },
  { icon: 'travel_explore', labelKey: 'workspace.sidebar.nav.webSearch', route: '/workspace/web-search', adminOnly: true },
];

const BUILT_IN_INTEGRATION_ITEMS: NavItem[] = [
  { icon: 'hub', labelKey: 'workspace.sidebar.nav.connectors', route: '/workspace/connectors/team', adminOnly: true },
  { icon: 'bolt', labelKey: 'workspace.sidebar.nav.actions', route: '/workspace/actions/team', adminOnly: true, requiresFlag: 'actions' },
  { icon: 'device_hub', labelKey: 'workspace.sidebar.nav.mcpServers', route: '/workspace/mcp-servers/team', adminOnly: true, requiresFlag: 'mcp' },
  { icon: 'support_agent', labelKey: 'workspace.sidebar.nav.bots', route: '/workspace/bots', adminOnly: true },
];

/** Who may sign in, and what may reach the org's data. */
const BUILT_IN_SECURITY_ITEMS: NavItem[] = [
  { icon: 'security', labelKey: 'workspace.sidebar.nav.authentication', route: '/workspace/authentication', adminOnly: true },
  { icon: 'precision_manufacturing', labelKey: 'workspace.sidebar.nav.serviceAccounts', route: '/workspace/service-accounts', adminOnly: true },
  { icon: 'admin_panel_settings', labelKey: 'workspace.sidebar.nav.mcpAccess', route: '/workspace/mcp-access', adminOnly: true },
];

const BUILT_IN_SYSTEM_ITEMS: NavItem[] = [
  { icon: 'mail', labelKey: 'workspace.sidebar.nav.mail', route: '/workspace/mail', adminOnly: true },
  { icon: 'manage_accounts', labelKey: 'workspace.sidebar.nav.services', route: '/workspace/services', adminOnly: true },
];

const BUILT_IN_PERSONAL_ITEMS: NavItem[] = [
  { icon: 'person', labelKey: 'workspace.sidebar.nav.profile', route: '/workspace/profile' },
  { icon: 'key', labelKey: 'workspace.sidebar.nav.aiAccounts', route: '/workspace/ai-accounts', requiresFlag: 'engineRuns', beta: true },
  { icon: 'schedule', labelKey: 'workspace.sidebar.nav.schedules', route: '/workspace/schedules', requiresFlag: 'scheduledRuns', beta: true },
  { icon: 'memory', labelKey: 'workspace.sidebar.nav.memories', route: '/workspace/memories', requiresFlag: 'agentHarness', beta: true },
  { icon: '', labelKey: 'workspace.sidebar.nav.yourConnectors', route: '/workspace/connectors/personal', customIcon: <YourConnectorsIcon size={ICON_SIZE_DEFAULT} color="var(--slate-11)" /> },
  { icon: 'bolt', labelKey: 'workspace.sidebar.nav.yourActions', route: '/workspace/actions/personal', requiresFlag: 'actions' },
  { icon: 'device_hub', labelKey: 'workspace.sidebar.nav.yourMcpServers', route: '/workspace/mcp-servers/personal', requiresFlag: 'mcp' },
  { icon: 'psychology', labelKey: 'workspace.sidebar.nav.yourSkills', route: '/workspace/skills/personal', requiresFlag: 'skills', beta: true },
  { icon: 'archive', labelKey: 'workspace.sidebar.nav.archivedChats', route: '/workspace/archived-chats' },
];

// The built-in entries with an edition's (workspaceNavExtras in config.ts).
const NAV_EXTRAS = placeWorkspaceExtras(
  { ai: BUILT_IN_AI_ITEMS, integrations: BUILT_IN_INTEGRATION_ITEMS, security: BUILT_IN_SECURITY_ITEMS, system: BUILT_IN_SYSTEM_ITEMS },
  workspaceNavExtras,
);
const OVERVIEW_ITEMS = mergeWorkspaceNav('overview', BUILT_IN_OVERVIEW_ITEMS, NAV_EXTRAS);
const DEVELOPER_SETTINGS_ITEMS = mergeWorkspaceNav('developerSettings', BUILT_IN_DEVELOPER_SETTINGS_ITEMS, NAV_EXTRAS);
const PERSONAL_ITEMS = mergeWorkspaceNav('personal', BUILT_IN_PERSONAL_ITEMS, NAV_EXTRAS);

/** The admin sections, in the order they are listed. */
const ADMIN_SECTIONS = [
  { key: 'ai', titleKey: 'workspace.sidebar.sections.ai', items: mergeWorkspaceNav('ai', BUILT_IN_AI_ITEMS, NAV_EXTRAS) },
  { key: 'integrations', titleKey: 'workspace.sidebar.sections.integrations', items: mergeWorkspaceNav('integrations', BUILT_IN_INTEGRATION_ITEMS, NAV_EXTRAS) },
  { key: 'security', titleKey: 'workspace.sidebar.sections.security', items: mergeWorkspaceNav('security', BUILT_IN_SECURITY_ITEMS, NAV_EXTRAS) },
  { key: 'system', titleKey: 'workspace.sidebar.sections.system', items: mergeWorkspaceNav('system', BUILT_IN_SYSTEM_ITEMS, NAV_EXTRAS) },
] as const;

/** Pages of Personal settings beyond the built-in list in settings-scope.ts: an edition's. */
const EXTRA_PERSONAL_ROUTES = NAV_EXTRAS.filter(
  (item) => item.section === 'personal' || (item.section === 'developerSettings' && item.personal),
).map((item) => item.route);

const PEOPLE_ROUTES = PEOPLE_SUB_ITEMS.map((item) => item.route);

/**
 * Marks an Enterprise Edition page: a tag that says there is more to see, where a lock would say
 * "no access". Hidden from the link's name, so it still reads "Groups"; the page itself explains.
 */
function EnterpriseNavTag() {
  const { t } = useTranslation();
  return (
    <Badge aria-hidden="true" size="1" variant="soft" radius="full" style={{ flexShrink: 0 }}>
      {t('common.enterprise.short')}
    </Badge>
  );
}

// ========================================
// Component
// ========================================

/**
 * Workspace sidebar — settings navigation with a collapsible "People" section.
 *
 * Uses `SidebarBase` shell, with the Enterprise Edition entry as its footer for admins; at phone
 * width it is the drawer the app's menu button opens.
 * Active item determined from current pathname.
 */
export default function WorkspaceSidebar() {
  const rawPathname = usePathname();
  const { t } = useTranslation();
  const isAdmin = useUserStore(selectIsAdmin);
  const openSupport = useSupportDialogStore((s) => s.open);
  const openFeedback = useFeedbackDialogStore((s) => s.open);
  const mcpEnabled = useFeatureFlagsStore(selectMcpEnabled);
  const actionsEnabled = useFeatureFlagsStore(selectActionsEnabled);
  const skillsEnabled = useFeatureFlagsStore(selectSkillsEnabled);
  const scheduledRunsEnabled = useFeatureFlagsStore(selectScheduledRunsEnabled);
  const agentHarnessOn = useFeatureFlagsStore(selectAgentHarnessOn);
  const agentHarnessResumeEnabled = useFeatureFlagsStore(selectAgentHarnessResumeEnabled);
  const engineRunsEnabled = useFeatureFlagsStore(selectEngineRunsEnabled);
  const showEnterprise = useEnterprisePrompt();
  const isMobile = useIsMobile();
  const isMobileOpen = useMobileSidebarStore((s) => s.isOpen);
  const closeMobileSidebar = useMobileSidebarStore((s) => s.close);

  // Normalize trailing slash (trailingSlash: true in next.config)
  const pathname = rawPathname.endsWith('/') && rawPathname !== '/'
    ? rawPathname.slice(0, -1)
    : rawPathname;

  const scope = settingsScopeFor(pathname, isAdmin, EXTRA_PERSONAL_ROUTES);
  const isPersonalScope = scope === 'personal';
  // Members have no Workspace settings, so what they can open of it is listed with their own.
  const showOrgItems = !isPersonalScope || isAdmin !== true;
  const otherScope = isPersonalScope
    ? { icon: 'settings', labelKey: 'workspaceMenu.workspaceSettings', href: '/workspace/general/' }
    : { icon: 'person', labelKey: 'workspaceMenu.personalSettings', href: '/workspace/profile/' };

  const [isPeopleExpanded, setIsPeopleExpanded] = useState(
    PEOPLE_ROUTES.some((route) => pathname.startsWith(route))
  );

  const flagEnabled: Record<FeatureFlagGate, boolean> = {
    mcp: mcpEnabled,
    actions: actionsEnabled,
    skills: skillsEnabled,
    scheduledRuns: scheduledRunsEnabled,
    agentHarness: agentHarnessOn,
    agentHarnessResume: agentHarnessResumeEnabled,
    engineRuns: engineRunsEnabled,
  };
  const isNavItemVisible = (item: NavItem) =>
    (isAdmin || !item.adminOnly) &&
    (item.requiresFlag === undefined || flagEnabled[item.requiresFlag]);

  const allRoutes = [
    ...OVERVIEW_ITEMS.map((item) => item.route),
    ...ADMIN_SECTIONS.flatMap((section) => section.items.filter(isNavItemVisible).map((item) => item.route)),
    ...DEVELOPER_SETTINGS_ITEMS.map((item) => item.route),
    ...PERSONAL_ITEMS.filter(isNavItemVisible).map((item) => item.route),
    ...PEOPLE_SUB_ITEMS.map((item) => item.route),
    ...Object.values(ENTERPRISE_FEATURES).map((item) => item.route),
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
  const visiblePeopleItems = PEOPLE_SUB_ITEMS;
  const visibleDeveloperSettingsItems = DEVELOPER_SETTINGS_ITEMS.filter(
    (item) => isNavItemVisible(item) && (item.personal ? isPersonalScope : showOrgItems),
  );
  const visiblePersonalItems = PERSONAL_ITEMS.filter(isNavItemVisible);

  /** The Enterprise Edition pages listed in one part of the sidebar, each tagged. */
  const renderEnterpriseItems = (section: EnterpriseSection) =>
    (showEnterprise ? Object.values(ENTERPRISE_FEATURES) : [])
      .filter((item) => item.section === section)
      .map((item) => (
        <WorkspaceSidebarItem
          key={item.route}
          icon={<MaterialIcon name={item.icon} size={ICON_SIZE_DEFAULT} color="var(--slate-11)" />}
          label={t(item.labelKey)}
          href={`${item.route}/`}
          isActive={isActive(item.route)}
          rightSlot={<EnterpriseNavTag />}
        />
      ));

  // On mobile any link tap closes the drawer, even to the page already shown.
  const closeDrawerOnLinkTap = (e: MouseEvent) => {
    if (isMobile && (e.target as HTMLElement).closest('a')) closeMobileSidebar();
  };

  return (
    <SidebarBase
      header={isMobile ? <WorkspaceSidebarMobileHeader title={t(isPersonalScope ? 'workspaceMenu.personalSettings' : 'workspaceMenu.workspaceSettings')} /> : undefined}
      footer={
        showEnterprise && !isPersonalScope ? (
          <Box onClick={closeDrawerOnLinkTap}>
            <EnterpriseSidebarEntry isActive={isActive(ENTERPRISE_OVERVIEW_ROUTE)} />
          </Box>
        ) : undefined
      }
      footerHeight="auto"
      isMobile={isMobile}
      mobileOpen={isMobileOpen}
      onMobileClose={closeMobileSidebar}
    >
      <Flex direction="column" gap="4" onClick={closeDrawerOnLinkTap}>
        {/* ── Back to app, then the other settings ── */}
        <Flex direction="column" gap="1">
          <WorkspaceSidebarItem
            icon={<MaterialIcon name="arrow_back" size={ICON_SIZE_DEFAULT} color="var(--slate-11)" />}
            label={t('workspace.sidebar.backToApp')}
            href="/chat/"
          />
          {isAdmin && (
            <WorkspaceSidebarItem
              icon={<MaterialIcon name={otherScope.icon} size={ICON_SIZE_DEFAULT} color="var(--slate-11)" />}
              label={t(otherScope.labelKey)}
              href={otherScope.href}
            />
          )}
        </Flex>

        {/* ── Personal section ── */}
        {isPersonalScope && (
          <Flex direction="column" gap="1">
            <SectionHeader title={t('workspace.sidebar.sections.personal')} />
            {visiblePersonalItems.map((item) => (
              <WorkspaceSidebarItem
                key={item.route}
                icon={item.customIcon ?? <MaterialIcon name={item.icon} size={ICON_SIZE_DEFAULT} color="var(--slate-11)" />}
                label={t(item.labelKey)}
                href={`${item.route}/`}
                isActive={isActive(item.route)}
                rightSlot={item.beta ? <BetaBadge /> : undefined}
              />
            ))}
          </Flex>
        )}

        {/* ── Overview section ── */}
        {showOrgItems && (
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

            {renderEnterpriseItems('overview')}
          </Flex>
        )}

        {!isPersonalScope &&
          ADMIN_SECTIONS.map((section) => {
            const items = section.items.filter(isNavItemVisible);
            const enterpriseItems = section.key === 'security' ? renderEnterpriseItems('security') : [];
            if (items.length === 0 && enterpriseItems.length === 0) return null;
            return (
              <Flex key={section.key} direction="column" gap="1">
                <SectionHeader title={t(section.titleKey)} />
                {items.map((item) => (
                  <WorkspaceSidebarItem
                    key={item.route}
                    icon={item.customIcon ?? <MaterialIcon name={item.icon} size={ICON_SIZE_DEFAULT} color="var(--slate-11)" />}
                    label={t(item.labelKey)}
                    href={`${item.route}/`}
                    isActive={isActive(item.route)}
                    rightSlot={item.beta ? <BetaBadge /> : undefined}
                  />
                ))}
                {enterpriseItems}
              </Flex>
            );
          })}

        {/* ── Usage & billing: an Enterprise Edition page, shown tagged ── */}
        {!isPersonalScope && showEnterprise && (
          <Flex direction="column" gap="1">
            <SectionHeader title={t('workspace.sidebar.sections.usageAndCredits')} />
            {renderEnterpriseItems('usageAndCredits')}
          </Flex>
        )}

        <Flex direction="column" gap="1">
          <SectionHeader title={t('workspace.sidebar.sections.developerSettings')} />
          {visibleDeveloperSettingsItems.map((item) => (
            <WorkspaceSidebarItem
              key={item.route}
              icon={<MaterialIcon name={item.icon} size={ICON_SIZE_DEFAULT} color="var(--slate-11)" />}
              label={t(item.labelKey)}
              href={`${item.route}/`}
              isActive={isActive(item.route)}
            />
          ))}
          {/* The API reference is a page the backend serves, so it opens on the API host. */}
          <WorkspaceSidebarItem
            icon={<MaterialIcon name="menu_book" size={ICON_SIZE_DEFAULT} color="var(--slate-11)" />}
            label={t('workspace.sidebar.nav.apiDocumentation')}
            onClick={() => window.open(`${getApiBaseUrl().replace(/\/+$/, '')}/api/v1/docs`, '_blank', 'noopener,noreferrer')}
            rightSlot={<MaterialIcon name="open_in_new" size={ICON_SIZE_DEFAULT} color="var(--slate-11)" />}
          />
        </Flex>


        {/* The workspace menu, with its help items, is not reachable from this sidebar, so they are listed here. */}
        <Flex direction="column" gap="1">
          <SectionHeader title={t('workspace.sidebar.sections.help')} />
          <WorkspaceSidebarItem
            icon={<MaterialIcon name="support_agent" size={ICON_SIZE_DEFAULT} color="var(--slate-11)" />}
            label={t('support.menuLabel')}
            onClick={openSupport}
          />
          <WorkspaceSidebarItem
            icon={<MaterialIcon name="feedback" size={ICON_SIZE_DEFAULT} color="var(--slate-11)" />}
            label={t('feedback.menuLabel')}
            onClick={openFeedback}
          />
        </Flex>
      </Flex>
    </SidebarBase>
  );
}

/** Title row with inbox and collapse; on the mobile drawer SidebarBase adds the close button beside it. */
export function WorkspaceSidebarMobileHeader({ title }: { title: string }) {
  return (
    <Flex align="center" justify="between" gap="2" style={{ height: '100%', padding: '0 var(--space-4)' }}>
      <Text size="3" weight="medium" style={{ color: 'var(--slate-12)' }}>
        {title}
      </Text>
      <SidebarHeaderActions />
    </Flex>
  );
}
