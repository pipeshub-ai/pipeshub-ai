'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Box, Flex, Text, TextField } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { ProjectApi } from '@/chat/project-api';
import type { ProjectSummary } from '@/chat/project-types';
import { useChatStore } from '@/chat/store';
import { CreateProjectDialog } from '@/chat/sidebar/dialogs';
import { formatRelativeTime } from '@/lib/utils/formatters';
import { SidebarExpandButton } from '@/app/components/sidebar/sidebar-expand-button';

const PROJECT_LIST_PAGE_SIZE = 30;

function ProjectCard({ project, onOpen }: { project: ProjectSummary; onOpen: () => void }) {
  const { t } = useTranslation();

  return (
    <Box
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onOpen();
        }
      }}
      style={{
        background: 'var(--olive-2)',
        border: '1px solid var(--olive-4)',
        borderRadius: 'var(--radius-3)',
        padding: 'var(--space-4)',
        cursor: 'pointer',
        display: 'flex',
        flexDirection: 'column',
        gap: 'var(--space-2)',
        minHeight: 130,
      }}
    >
      <Flex align="center" justify="between">
        <Box
          style={{
            width: 32,
            height: 32,
            borderRadius: 'var(--radius-2)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            background: 'var(--accent-3)',
            border: '1px solid var(--accent-6)',
            flexShrink: 0,
          }}
        >
          <MaterialIcon name="folder" size={18} color={project.color || 'var(--accent-11)'} />
        </Box>
        <Flex align="center" gap="2">
          {project.isPinned && <MaterialIcon name="star" size={16} color="var(--amber-9)" />}
          <Text size="1" style={{ color: 'var(--slate-10)' }}>
            {t(`chat.projects.roles.${project.role === 'none' ? 'viewer' : project.role}`)}
          </Text>
        </Flex>
      </Flex>
      <Text
        size="3"
        weight="bold"
        style={{ color: 'var(--slate-12)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
      >
        {project.name}
      </Text>
      <Text
        size="2"
        style={{
          color: 'var(--slate-10)',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          display: '-webkit-box',
          WebkitLineClamp: 2,
          WebkitBoxOrient: 'vertical',
          flex: 1,
        }}
      >
        {project.description?.trim() || t('chat.projects.workspace.noDescription')}
      </Text>
      <Flex align="center" justify="between" style={{ marginTop: 'auto' }}>
        <Text size="1" style={{ color: 'var(--slate-9)' }}>
          {t(
            project.conversationCount === 1 ? 'projects.chatCount_one' : 'projects.chatCount_other',
            { count: project.conversationCount },
          )}
        </Text>
        <Text size="1" style={{ color: 'var(--slate-9)' }}>
          {t('projects.lastActive', { date: formatRelativeTime(project.lastActivityAt) })}
        </Text>
      </Flex>
    </Box>
  );
}

/**
 * `/projects` (no `projectId`) — grid of the user's projects (owned + shared),
 * with search and create. Clicking a card navigates to `/projects?projectId=…`.
 */
export function ProjectList() {
  const router = useRouter();
  const { t } = useTranslation();

  const upsertProjectInList = useChatStore((s) => s.upsertProjectInList);

  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [hasError, setHasError] = useState(false);
  const [search, setSearch] = useState('');
  const [createOpen, setCreateOpen] = useState(false);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);

  // Guards against an in-flight request for a stale query/page overwriting
  // the result of a newer one that resolves first.
  const requestIdRef = useRef(0);

  const load = useCallback(async (query: string, targetPage: number) => {
    const requestId = ++requestIdRef.current;
    if (targetPage === 1) setIsLoading(true);
    else setIsLoadingMore(true);
    setHasError(false);
    try {
      const { projects: rows, pagination } = await ProjectApi.list({
        scope: 'all',
        page: targetPage,
        limit: PROJECT_LIST_PAGE_SIZE,
        includeArchived: false,
        ...(query.trim() ? { search: query.trim() } : {}),
      });
      if (requestId !== requestIdRef.current) return;
      setProjects((prev) => (targetPage === 1 ? rows : [...prev, ...rows]));
      setPage(targetPage);
      setHasMore(targetPage < pagination.totalPages);
    } catch {
      if (requestId !== requestIdRef.current) return;
      setHasError(true);
      if (targetPage === 1) setProjects([]);
    } finally {
      if (requestId === requestIdRef.current) {
        setIsLoading(false);
        setIsLoadingMore(false);
      }
    }
  }, []);

  useEffect(() => {
    const handle = setTimeout(() => void load(search, 1), search ? 250 : 0);
    return () => clearTimeout(handle);
  }, [search, load]);

  const loadMore = () => {
    if (isLoadingMore || !hasMore) return;
    void load(search, page + 1);
  };

  const openProject = (projectId: string) => {
    router.push(`/chat/?projectId=${encodeURIComponent(projectId)}`);
  };

  const sorted = [...projects].sort((a, b) => {
    if (a.isPinned !== b.isPinned) return a.isPinned ? -1 : 1;
    return b.lastActivityAt - a.lastActivityAt;
  });

  return (
    <Flex direction="column" gap="5" style={{ width: '100%', maxWidth: 1100, margin: '0 auto', padding: 'var(--space-6)' }}>
      <Flex align="center" justify="between" wrap="wrap" gap="3">
        <Flex align="center" gap="3" style={{ minWidth: 0 }}>
          <SidebarExpandButton placement="inline" />
          <Text size="6" weight="bold" style={{ color: 'var(--slate-12)' }}>
            {t('projects.pageTitle')}
          </Text>
        </Flex>
        <LoadingButton color="jade" onClick={() => setCreateOpen(true)}>
          <Flex align="center" gap="2">
            <MaterialIcon name="add" size={16} />
            {t('chat.projects.newProject')}
          </Flex>
        </LoadingButton>
      </Flex>

      <TextField.Root
        placeholder={t('projects.searchPlaceholder')}
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        style={{ maxWidth: 360 }}
      >
        <TextField.Slot>
          <MaterialIcon name="search" size={16} color="var(--slate-9)" />
        </TextField.Slot>
      </TextField.Root>

      {isLoading ? (
        <Flex align="center" justify="center" style={{ padding: 'var(--space-8) 0' }}>
          <LottieLoader autoplay loop style={{ width: 48, height: 48 }} />
        </Flex>
      ) : hasError ? (
        <Text size="2" style={{ color: '#ef4444' }}>
          {t('projects.failedToLoad')}
        </Text>
      ) : sorted.length === 0 ? (
        <Flex direction="column" align="center" gap="2" style={{ padding: 'var(--space-8) 0' }}>
          <MaterialIcon name="folder_open" size={40} color="var(--slate-8)" />
          <Text size="3" weight="medium" style={{ color: 'var(--slate-11)' }}>
            {t('projects.emptyState')}
          </Text>
          <Text size="2" style={{ color: 'var(--slate-9)' }}>
            {t('projects.emptyStateHint')}
          </Text>
        </Flex>
      ) : (
        <Flex direction="column" gap="4">
          <Box
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))',
              gap: 'var(--space-4)',
            }}
          >
            {sorted.map((project) => (
              <ProjectCard key={project._id} project={project} onOpen={() => openProject(project._id)} />
            ))}
          </Box>
          {hasMore && (
            <Flex justify="center">
              <LoadingButton variant="outline" loading={isLoadingMore} onClick={loadMore}>
                {t('projects.loadMore')}
              </LoadingButton>
            </Flex>
          )}
        </Flex>
      )}

      <CreateProjectDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreated={(project) => {
          upsertProjectInList({ ...project, conversationCount: 0 });
          openProject(project._id);
        }}
      />
    </Flex>
  );
}
