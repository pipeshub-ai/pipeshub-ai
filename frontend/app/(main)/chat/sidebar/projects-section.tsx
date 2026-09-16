'use client';

import React, { useEffect, useCallback } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ICON_SIZE_DEFAULT } from '@/app/components/sidebar';
import { useChatStore } from '@/chat/store';
import { useMobileSidebarStore } from '@/lib/store/mobile-sidebar-store';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { ProjectApi } from '@/chat/project-api';
import { buildChatHref } from '@/chat/build-chat-url';
import { ChatSectionHeader } from './chat-section-header';
import { SidebarItem } from './sidebar-item';
import { ChatItemSkeleton } from './chat-section-element';
import { CreateProjectDialog } from './dialogs';
import { MAX_VISIBLE_PROJECTS_IN_SIDEBAR } from '../constants';
import { useIsMainChatRoute } from '@/chat/hooks/use-is-main-chat-route';

const PROJECTS_SKELETON_COUNT = 3;
const SIDEBAR_PROJECTS_PREVIEW_FETCH_LIMIT = 10;

/**
 * Main chat sidebar — short list of the user's projects (owned + shared,
 * pinned first) + "New project". Collapsed to nothing when the user has
 * never created one, so it doesn't take up space for accounts that don't
 * use Projects.
 */
export const ProjectsSection = React.memo(function ProjectsSection() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const currentProjectId = searchParams.get('projectId');
  const onChatRoute = useIsMainChatRoute();
  const { t } = useTranslation();
  const isMobile = useIsMobile();

  const projects = useChatStore((s) => s.projects);
  const isProjectsLoading = useChatStore((s) => s.isProjectsLoading);
  const projectsError = useChatStore((s) => s.projectsError);
  const projectsVersion = useChatStore((s) => s.projectsVersion);
  const setProjects = useChatStore((s) => s.setProjects);
  const setIsProjectsLoading = useChatStore((s) => s.setIsProjectsLoading);
  const setProjectsError = useChatStore((s) => s.setProjectsError);
  const upsertProjectInList = useChatStore((s) => s.upsertProjectInList);

  const [createOpen, setCreateOpen] = React.useState(false);

  const load = useCallback(async () => {
    setIsProjectsLoading(true);
    setProjectsError(null);
    try {
      const { projects: rows } = await ProjectApi.list({
        scope: 'all',
        limit: SIDEBAR_PROJECTS_PREVIEW_FETCH_LIMIT,
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
    load();
  }, [load, projectsVersion]);

  const sorted = [...projects].sort((a, b) => {
    if (a.isPinned !== b.isPinned) return a.isPinned ? -1 : 1;
    return b.lastActivityAt - a.lastActivityAt;
  });
  const visible = sorted.slice(0, MAX_VISIBLE_PROJECTS_IN_SIDEBAR);

  const openProject = () => {
    if (isMobile) useMobileSidebarStore.getState().close();
  };

  const goCreateProject = useCallback(() => {
    setCreateOpen(true);
  }, []);

  // Collapse entirely for accounts that have never created/joined a project.
  if (!isProjectsLoading && !projectsError && projects.length === 0) {
    return (
      <>
        <Flex direction="column" style={{ flexShrink: 0 }}>
          <ChatSectionHeader
            title={t('chat.projects.title')}
            onAdd={goCreateProject}
            addAriaLabel={t('chat.projects.newProject')}
          />
        </Flex>
        <CreateProjectDialog
          open={createOpen}
          onOpenChange={setCreateOpen}
          onCreated={(project) => {
            upsertProjectInList({ ...project, conversationCount: 0 });
            if (isMobile) useMobileSidebarStore.getState().close();
            router.push(buildChatHref({ projectId: project._id }));
          }}
        />
      </>
    );
  }

  return (
    <Flex direction="column" style={{ flexShrink: 0 }}>
      <ChatSectionHeader
        title={t('chat.projects.title')}
        onAdd={goCreateProject}
        addAriaLabel={t('chat.projects.newProject')}
      />

      {projectsError ? (
        <Text size="1" style={{ padding: 'var(--space-2) var(--space-3)', color: '#ef4444' }}>
          {projectsError}
        </Text>
      ) : (
        <Flex direction="column" gap="1">
          {isProjectsLoading ? (
            <Flex direction="column" gap="1">
              {Array.from({ length: PROJECTS_SKELETON_COUNT }, (_, i) => (
                <ChatItemSkeleton key={i} />
              ))}
            </Flex>
          ) : (
            visible.map((project) => {
              const isActive = onChatRoute && currentProjectId === project._id;
              return (
                <SidebarItem
                  key={project._id}
                  icon={
                    <MaterialIcon
                      name="folder"
                      size={ICON_SIZE_DEFAULT}
                      color={project.color || 'var(--slate-11)'}
                    />
                  }
                  label={project.name}
                  isActive={isActive}
                  href={buildChatHref({ projectId: project._id })}
                  onClick={openProject}
                />
              );
            })
          )}

        </Flex>
      )}

      <CreateProjectDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreated={(project) => {
          upsertProjectInList({ ...project, conversationCount: 0 });
          if (isMobile) useMobileSidebarStore.getState().close();
          router.push(buildChatHref({ projectId: project._id }));
        }}
      />
    </Flex>
  );
});
