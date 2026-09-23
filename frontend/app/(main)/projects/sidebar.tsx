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
import { ChatSidebarFooter } from '@/chat/sidebar/footer';
import { ChatSectionHeader } from '@/chat/sidebar/chat-section-header';
import { SidebarItem } from '@/chat/sidebar/sidebar-item';
import { ChatItemSkeleton } from '@/chat/sidebar/chat-section-element';
import { CreateProjectDialog } from '@/chat/sidebar/dialogs';
import { useMobileSidebarStore } from '@/lib/store/mobile-sidebar-store';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { useFeatureFlagsStore, selectProjectsEnabled } from '@/lib/store/feature-flags-store';

const PROJECTS_SKELETON_COUNT = 4;
const SIDEBAR_PROJECTS_FETCH_LIMIT = 50;

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
  const isMobile = useIsMobile();
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
    setIsProjectsLoading(true);
    setProjectsError(null);
    try {
      const { projects: rows } = await ProjectApi.list({
        scope: 'all',
        limit: SIDEBAR_PROJECTS_FETCH_LIMIT,
        includeArchived: false,
      });
      setProjects(rows);
    } catch {
      setProjectsError(t('chat.projects.failedToLoad'));
      setProjects([]);
    } finally {
      setIsProjectsLoading(false);
    }
  }, [setProjects, setIsProjectsLoading, setProjectsError, t]);

  useEffect(() => {
    if (!projectsEnabled) return;
    load();
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
            <Text size="1" style={{ padding: 'var(--space-2) var(--space-3)', color: '#ef4444' }}>
              {t('chat.projects.failedToLoad')}
            </Text>
          ) : !isSearchActive && projectsError ? (
            <Text size="1" style={{ padding: 'var(--space-2) var(--space-3)', color: '#ef4444' }}>
              {projectsError}
            </Text>
          ) : isListLoading ? (
            <Flex direction="column" gap="1">
              {Array.from({ length: PROJECTS_SKELETON_COUNT }, (_, i) => (
                <ChatItemSkeleton key={i} />
              ))}
            </Flex>
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
