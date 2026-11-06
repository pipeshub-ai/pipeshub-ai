'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import { Flex, Text, TextField } from '@radix-ui/themes';
import type { ProjectSummary } from '@/chat/project-types';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { SidebarBase, ICON_SIZE_DEFAULT } from '@/app/components/sidebar';
import { useChatStore } from '@/chat/store';
import { ProjectApi } from '@/chat/project-api';
import { ChatSidebarHeader } from '@/chat/sidebar/header';
import { ChatSidebarFooter } from '@/config';
import { ChatSectionHeader } from '@/chat/sidebar/chat-section-header';
import { SidebarItem } from '@/chat/sidebar/sidebar-item';
import { ListSkeleton } from '@/app/components/ui/list-skeleton';
import { useDeferredLoading } from '@/app/components/ui/use-deferred-loading';
import { CreateProjectDialog } from '@/chat/sidebar/dialogs';
import { useMobileSidebarStore } from '@/lib/store/mobile-sidebar-store';
import { useIsBelowDesktop } from '@/lib/hooks/use-is-mobile';
import { useFeatureFlagsStore, selectProjectsEnabled } from '@/lib/store/feature-flags-store';
import { SIDEBAR_LISTS_STALE_MS } from '@/chat/constants';
import { getCurrentUserId } from '@/chat/utils/current-user-id';
import { watchSidebarSession } from '@/chat/sidebar/use-sidebar-conversations';

const PROJECTS_SKELETON_COUNT = 3;
const SIDEBAR_PROJECTS_FETCH_LIMIT = 50;

/** The `projectsVersion` this rail last loaded the projects at: a bump since means the list is behind. */
let loadedProjectsVersion = -1;

/**
 * Sidebar for the dedicated `/projects` route — full list of the user's
 * projects (pinned first, then by recent activity), search, and create.
 * Renders in the `@sidebar/projects` slot.
 */
export function ProjectsSidebar() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const currentProjectId = searchParams.get('projectId');
  const { t } = useTranslation();
  // Below desktop the sidebar is the navigation drawer.
  const isMobile = useIsBelowDesktop();
  const projectsEnabled = useFeatureFlagsStore(selectProjectsEnabled);
  const isMobileOpen = useMobileSidebarStore((s) => s.isOpen);
  const closeMobileSidebar = useMobileSidebarStore((s) => s.close);

  const projects = useChatStore((s) => s.projects);
  const isProjectsLoading = useChatStore((s) => s.isProjectsLoading);
  const projectsError = useChatStore((s) => s.projectsError);
  const projectsVersion = useChatStore((s) => s.projectsVersion);
  const setProjects = useChatStore((s) => s.setProjects);
  const setIsProjectsLoading = useChatStore((s) => s.setIsProjectsLoading);
  const setProjectsError = useChatStore((s) => s.setProjectsError);
  const upsertProjectInList = useChatStore((s) => s.upsertProjectInList);

  const [search, setSearch] = useState('');
  const [createOpen, setCreateOpen] = useState(false);

  // Server-side search results, kept separate from the shared `projects`
  // cache (`useChatStore.projects`) so searching here can never truncate the
  // list `MoveToProjectDialog` reads from that same store field.
  const [searchResults, setSearchResults] = useState<ProjectSummary[] | null>(null);
  const [isSearching, setIsSearching] = useState(false);
  const [searchError, setSearchError] = useState(false);
  const searchRequestIdRef = useRef(0);

  const load = useCallback(async () => {
    // Only a rail that has not loaded yet waits on a skeleton; a reload swaps the rows in place.
    const first = useChatStore.getState().projectsLoadedAt === null;
    const version = useChatStore.getState().projectsVersion;
    const userId = getCurrentUserId();
    if (first) setIsProjectsLoading(true);
    setProjectsError(null);
    try {
      const { projects: rows } = await ProjectApi.list(
        { scope: 'all', limit: SIDEBAR_PROJECTS_FETCH_LIMIT, includeArchived: false },
        // A reload behind a list on screen fails quietly: the list stays, so there is nothing to say.
        { suppressErrorToast: !first },
      );
      // Signed out, or someone else signed in, while this was out: these are not their projects.
      if (getCurrentUserId() !== userId) return;
      setProjects(rows);
      useChatStore.setState({ projectsLoadedAt: Date.now() });
      loadedProjectsVersion = version;
    } catch {
      // A reload that fails keeps the list on screen.
      if (first && getCurrentUserId() === userId) {
        setProjectsError(t('chat.projects.failedToLoad'));
        setProjects([]);
      }
    } finally {
      // Only this person's load ends their skeleton: the last person's, landing late, leaves the next person's on. A
      // sign-out already turned it off.
      if (first && getCurrentUserId() === userId) setIsProjectsLoading(false);
    }
  }, [setProjects, setIsProjectsLoading, setProjectsError, t]);

  // Sign-out clears the projects kept in the store, even in a session that never opened a chat list.
  useEffect(() => watchSidebarSession(), []);

  useEffect(() => {
    if (!projectsEnabled) return;
    // Loaded under 30 s ago and not behind a bump: shown as it is, an empty list too. Sign-out clears projectsLoadedAt.
    const loadedAt = useChatStore.getState().projectsLoadedAt;
    const fresh =
      loadedAt !== null && loadedProjectsVersion === projectsVersion && Date.now() - loadedAt < SIDEBAR_LISTS_STALE_MS;
    if (!fresh) load();
  }, [load, projectsVersion, projectsEnabled]);

  // The cached `projects` list is capped at SIDEBAR_PROJECTS_FETCH_LIMIT, so
  // filtering it in-memory would silently miss matches beyond the cap.
  // Search the server instead once a query is entered; a request-id guard
  // drops responses for a query the user has since changed or cleared.
  useEffect(() => {
    const query = search.trim();
    if (!query) {
      searchRequestIdRef.current += 1;
      setSearchResults(null);
      setIsSearching(false);
      setSearchError(false);
      return;
    }
    const requestId = ++searchRequestIdRef.current;
    setIsSearching(true);
    setSearchError(false);
    const handle = setTimeout(() => {
      ProjectApi.list({
        scope: 'all',
        limit: SIDEBAR_PROJECTS_FETCH_LIMIT,
        includeArchived: false,
        search: query,
      })
        .then(({ projects: rows }) => {
          if (requestId !== searchRequestIdRef.current) return;
          setSearchResults(rows);
        })
        .catch(() => {
          if (requestId !== searchRequestIdRef.current) return;
          setSearchResults([]);
          setSearchError(true);
        })
        .finally(() => {
          if (requestId === searchRequestIdRef.current) setIsSearching(false);
        });
    }, 250);
    return () => clearTimeout(handle);
  }, [search]);

  const isSearchActive = search.trim().length > 0;
  const isListLoading = isSearchActive ? isSearching : isProjectsLoading;
  const listSkeleton = useDeferredLoading(isListLoading);
  const showSkeleton = isListLoading || listSkeleton.visible;

  const filtered = useMemo(() => {
    const rows = isSearchActive ? (searchResults ?? []) : projects;
    return [...rows].sort((a, b) => {
      if (a.isPinned !== b.isPinned) return a.isPinned ? -1 : 1;
      return b.lastActivityAt - a.lastActivityAt;
    });
  }, [projects, searchResults, isSearchActive]);

  const pinned = filtered.filter((p) => p.isPinned);
  const recent = filtered.filter((p) => !p.isPinned);

  const goCreateProject = useCallback(() => setCreateOpen(true), []);

  const openProject = () => {
    if (isMobile) closeMobileSidebar();
  };

  const renderRow = (project: (typeof filtered)[number]) => (
    <SidebarItem
      key={project._id}
      icon={<MaterialIcon name="folder" size={ICON_SIZE_DEFAULT} color={project.color || 'var(--slate-11)'} />}
      label={project.name}
      isActive={currentProjectId === project._id}
      href={`/chat/?projectId=${encodeURIComponent(project._id)}`}
      onClick={openProject}
    />
  );

  // The page itself redirects to /chat when the flag is off; avoid a flash
  // of this sidebar (and its project list fetch) during that transition.
  if (!projectsEnabled) return null;

  return (
    <SidebarBase
      header={<ChatSidebarHeader />}
      footer={<ChatSidebarFooter />}
      isMobile={isMobile}
      mobileOpen={isMobileOpen}
      onMobileClose={closeMobileSidebar}
    >
      <Flex direction="column" gap="3" style={{ flex: 1, minHeight: 0 }}>
        <SidebarItem
          icon={<MaterialIcon name="chevron_left" size={ICON_SIZE_DEFAULT} />}
          label={t('chat.newChat')}
          href="/chat/"
          onClick={openProject}
        />

        <Flex direction="column" gap="2" style={{ padding: '0 var(--space-3)' }}>
          <TextField.Root
            size="1"
            placeholder={t('projects.searchPlaceholder')}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          >
            <TextField.Slot>
              <MaterialIcon name="search" size={14} color="var(--slate-9)" />
            </TextField.Slot>
          </TextField.Root>
        </Flex>

        <Flex
          direction="column"
          className="no-scrollbar"
          style={{ flex: 1, minHeight: 0, overflowY: 'auto' }}
        >
          <ChatSectionHeader
            title={t('projects.pageTitle')}
            onAdd={goCreateProject}
            addAriaLabel={t('chat.projects.newProject')}
          />

          {isSearchActive && searchError ? (
            <Text size="1" style={{ padding: 'var(--space-2) var(--space-3)', color: 'var(--red-11)' }}>
              {t('chat.projects.failedToLoad')}
            </Text>
          ) : !isSearchActive && projectsError ? (
            <Text size="1" style={{ padding: 'var(--space-2) var(--space-3)', color: 'var(--red-11)' }}>
              {projectsError}
            </Text>
          ) : showSkeleton ? (
            <ListSkeleton
              variant="sidebar-row"
              count={PROJECTS_SKELETON_COUNT}
              glyphSize={16}
              statusLabel={t('projects.loadingList')}
              exiting={listSkeleton.exiting}
            />
          ) : filtered.length === 0 ? (
            <Text size="1" style={{ padding: 'var(--space-2) var(--space-3)', color: 'var(--slate-10)' }}>
              {t('projects.emptyState')}
            </Text>
          ) : (
            <>
              {pinned.length > 0 && (
                <Flex direction="column">
                  <Flex align="center" style={{ height: 28, padding: '0 var(--space-3)' }}>
                    <Text size="1" style={{ color: 'var(--slate-10)' }}>
                      {t('projects.pinnedSection')}
                    </Text>
                  </Flex>
                  <Flex direction="column" gap="1">
                    {pinned.map(renderRow)}
                  </Flex>
                </Flex>
              )}
              {recent.length > 0 && (
                <Flex direction="column">
                  {pinned.length > 0 && (
                    <Flex align="center" style={{ height: 28, padding: '0 var(--space-3)' }}>
                      <Text size="1" style={{ color: 'var(--slate-10)' }}>
                        {t('projects.allSection')}
                      </Text>
                    </Flex>
                  )}
                  <Flex direction="column" gap="1">
                    {recent.map(renderRow)}
                  </Flex>
                </Flex>
              )}
            </>
          )}
        </Flex>
      </Flex>

      <CreateProjectDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreated={(project) => {
          upsertProjectInList({ ...project, conversationCount: 0 });
          if (isMobile) closeMobileSidebar();
          router.push(`/chat/?projectId=${encodeURIComponent(project._id)}`);
        }}
      />
    </SidebarBase>
  );
}
