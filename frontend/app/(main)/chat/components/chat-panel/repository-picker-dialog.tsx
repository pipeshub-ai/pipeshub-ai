'use client';

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Box, Button, Dialog, Flex, Select, Spinner, Text, TextField } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { useRouter } from 'next/navigation';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { formatRelativeTime } from '@/lib/utils/formatters';
import {
  RepositoryApi,
  type GithubBranch,
  type GithubConnection,
  type GithubRepository,
} from '@/chat/repository-api';
import { useRepositoryStore } from '@/chat/repository-store';
import { repositoryRefusalText } from '@/chat/harness-errors';

/** Where a person connects GitHub: their own actions, opened on the GitHub toolset. */
export const CONNECT_GITHUB_HREF = '/workspace/actions/personal?toolsetType=github';

const SEARCH_DELAY_MS = 250;

type Load<T> = { state: 'loading' } | { state: 'failed' } | { state: 'ready'; value: T };

export interface RepositoryPickerDialogProps {
  /** The saved chat it saves to; null for a new chat, whose first message carries the choice. */
  session: { agentId?: string; sessionId: string } | null;
  /** The agent whose new chat it is (null: the assistant). */
  agentId: string | null;
}

/**
 * "Work on a repository" (WP-26 GIT-FLOW): a GitHub connection, one of its repositories and the branch to start
 * from. A new chat holds the choice for its first message; a saved chat saves it at once, and a refusal reads as a
 * plain line in the dialog.
 */
export function RepositoryPickerDialog({ session, agentId }: RepositoryPickerDialogProps) {
  const open = useRepositoryStore((s) => s.pickerOpen);
  const close = useRepositoryStore((s) => s.closePicker);
  const isMobile = useIsMobile();
  return (
    <Dialog.Root open={open} onOpenChange={(next) => (next ? undefined : close())}>
      <Dialog.Content
        data-testid="repo-picker"
        aria-describedby={undefined}
        style={
          isMobile
            ? {
                width: '100vw',
                maxWidth: '100vw',
                height: '100dvh',
                maxHeight: '100dvh',
                borderRadius: 0,
                padding: 'var(--space-4)',
                display: 'flex',
                flexDirection: 'column',
              }
            : { width: 520, maxWidth: 'calc(100vw - 32px)', padding: 'var(--space-4)' }
        }
      >
        {open ? <PickerBody session={session} agentId={agentId} isMobile={isMobile} onClose={close} /> : null}
      </Dialog.Content>
    </Dialog.Root>
  );
}

function PickerBody({
  session,
  agentId,
  isMobile,
  onClose,
}: RepositoryPickerDialogProps & { isMobile: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const router = useRouter();
  const [connections, setConnections] = useState<Load<GithubConnection[]>>({ state: 'loading' });
  const [instanceId, setInstanceId] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const [repos, setRepos] = useState<Load<{ items: GithubRepository[]; page: number; hasMore: boolean }>>({ state: 'loading' });
  const [selected, setSelected] = useState<GithubRepository | null>(null);
  const [branches, setBranches] = useState<GithubBranch[]>([]);
  const [branch, setBranch] = useState<string>('');
  const [saving, setSaving] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const searchSeq = useRef(0);

  useEffect(() => {
    let live = true;
    RepositoryApi.connections()
      .then((value) => {
        if (!live) return;
        setConnections({ state: 'ready', value });
        setInstanceId(value[0]?.instanceId ?? null);
      })
      .catch(() => live && setConnections({ state: 'failed' }));
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => setQuery(search), SEARCH_DELAY_MS);
    return () => clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    if (!instanceId) return;
    const seq = ++searchSeq.current;
    setRepos({ state: 'loading' });
    RepositoryApi.repositories(instanceId, query, 1)
      .then((page) => {
        if (seq === searchSeq.current) setRepos({ state: 'ready', value: { items: page.repositories, page: page.page, hasMore: page.hasMore } });
      })
      .catch(() => seq === searchSeq.current && setRepos({ state: 'failed' }));
  }, [instanceId, query]);

  useEffect(() => {
    setBranches([]);
    setBranch(selected?.defaultBranch ?? '');
    if (!selected || !instanceId) return;
    let live = true;
    RepositoryApi.branches(instanceId, selected.fullName)
      .then((list) => {
        if (!live) return;
        setBranches(list);
        const preferred = list.find((b) => b.default)?.name ?? selected.defaultBranch;
        setBranch(preferred || list[0]?.name || '');
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [selected, instanceId]);

  const loadMore = async () => {
    if (repos.state !== 'ready' || !instanceId) return;
    const seq = searchSeq.current;
    try {
      const next = await RepositoryApi.repositories(instanceId, query, repos.value.page + 1);
      if (seq !== searchSeq.current) return;
      setRepos({
        state: 'ready',
        value: { items: [...repos.value.items, ...next.repositories], page: next.page, hasMore: next.hasMore },
      });
    } catch {
      // The list keeps what it has; "Load more" stays to try again.
    }
  };

  const branchOptions = useMemo<GithubBranch[]>(() => {
    if (branches.length > 0) return branches;
    return selected?.defaultBranch ? [{ name: selected.defaultBranch, default: true }] : [];
  }, [branches, selected]);

  const use = async () => {
    if (!selected || !instanceId || saving) return;
    const choice = { url: selected.url, ...(branch ? { baseRef: branch } : {}), toolsetInstanceId: instanceId };
    if (!session) {
      useRepositoryStore.getState().setPending({ ...choice, agentId, fullName: selected.fullName });
      onClose();
      return;
    }
    setSaving(true);
    setRefusal(null);
    const answer = await RepositoryApi.put(session, choice);
    setSaving(false);
    if ('refused' in answer) {
      setRefusal(repositoryRefusalText(answer.refused));
      return;
    }
    useRepositoryStore.getState().setView(session.sessionId, answer.view);
    onClose();
  };

  const title = (
    <Dialog.Title size="4" style={{ margin: 0 }}>
      {t('chat.repository.picker.title')}
    </Dialog.Title>
  );
  const rowHeight = isMobile ? 44 : 40;

  if (connections.state === 'loading') {
    return (
      <Flex direction="column" gap="4">
        {title}
        <Flex align="center" gap="2" data-testid="repo-picker-loading">
          <Spinner size="1" />
          <Text size="2" color="gray">
            {t('chat.repository.picker.loading')}
          </Text>
        </Flex>
      </Flex>
    );
  }

  if (connections.state === 'failed' || connections.value.length === 0) {
    const failed = connections.state === 'failed';
    return (
      <Flex direction="column" gap="4" data-testid={failed ? 'repo-picker-connections-failed' : 'repo-picker-connect'}>
        {title}
        <Box
          style={{
            background: failed ? 'var(--red-a3)' : 'var(--amber-a3)',
            color: failed ? 'var(--red-11)' : 'var(--amber-11)',
            borderRadius: 'var(--radius-2)',
            padding: 'var(--space-3)',
          }}
        >
          <Text size="2">{failed ? t('chat.repository.picker.connectionsFailed') : t('chat.repository.picker.connectFirst')}</Text>
        </Box>
        <Flex justify="end" gap="2" style={{ marginTop: isMobile ? 'auto' : undefined }}>
          <Button variant="soft" color="gray" size={isMobile ? '3' : '2'} onClick={onClose}>
            {t('chat.repository.picker.cancel')}
          </Button>
          {failed ? null : (
            <Button
              size={isMobile ? '3' : '2'}
              data-testid="repo-picker-connect-github"
              onClick={() => {
                onClose();
                router.push(CONNECT_GITHUB_HREF);
              }}
            >
              {t('chat.repository.picker.connectGithub')}
            </Button>
          )}
        </Flex>
      </Flex>
    );
  }

  const list = connections.value;
  return (
    <Flex direction="column" gap="4" style={isMobile ? { flex: 1, minHeight: 0 } : undefined}>
      {title}
      <Flex direction="column" gap="1">
        <Text size="1" weight="bold" as="label">
          {t('chat.repository.picker.connection')}
        </Text>
        <Select.Root value={instanceId ?? undefined} onValueChange={(value) => { setSelected(null); setInstanceId(value); }} size={isMobile ? '3' : '2'}>
          <Select.Trigger data-testid="repo-picker-connection" aria-label={t('chat.repository.picker.connection')} />
          <Select.Content position="popper">
            {list.map((c) => (
              <Select.Item key={c.instanceId} value={c.instanceId}>
                {c.name}
              </Select.Item>
            ))}
          </Select.Content>
        </Select.Root>
      </Flex>

      <Flex direction="column" gap="1" style={isMobile ? { flex: 1, minHeight: 0 } : undefined}>
        <Text size="1" weight="bold" as="label" htmlFor="repo-picker-search">
          {t('chat.repository.picker.repository')}
        </Text>
        <TextField.Root
          id="repo-picker-search"
          data-testid="repo-picker-search"
          size={isMobile ? '3' : '2'}
          value={search}
          placeholder={t('chat.repository.picker.search')}
          onChange={(e) => setSearch(e.target.value)}
        >
          <TextField.Slot side="right">
            <MaterialIcon name="search" size={16} color="var(--slate-11)" />
          </TextField.Slot>
        </TextField.Root>
        <Box
          role="listbox"
          aria-label={t('chat.repository.picker.repository')}
          data-testid="repo-picker-list"
          style={{
            border: '1px solid var(--olive-5)',
            borderRadius: 'var(--radius-2)',
            overflowY: 'auto',
            maxHeight: isMobile ? undefined : 220,
            flex: isMobile ? 1 : undefined,
            minHeight: rowHeight * 2,
          }}
        >
          {repos.state === 'loading' ? (
            <Flex align="center" gap="2" style={{ padding: 'var(--space-3)' }}>
              <Spinner size="1" />
              <Text size="2" color="gray">
                {t('chat.repository.picker.loading')}
              </Text>
            </Flex>
          ) : repos.state === 'failed' ? (
            <Text as="p" size="2" color="gray" style={{ padding: 'var(--space-3)', margin: 0 }}>
              {t('chat.repository.picker.reposFailed')}
            </Text>
          ) : repos.value.items.length === 0 ? (
            <Text as="p" size="2" color="gray" data-testid="repo-picker-empty" style={{ padding: 'var(--space-3)', margin: 0 }}>
              {query.trim() ? t('chat.repository.picker.noMatch') : t('chat.repository.picker.none')}
            </Text>
          ) : (
            <>
              {repos.value.items.map((repo, index) => (
                <RepositoryRow
                  key={repo.fullName}
                  repo={repo}
                  first={index === 0}
                  height={rowHeight}
                  selected={selected?.fullName === repo.fullName}
                  onSelect={() => setSelected(repo)}
                />
              ))}
              {repos.value.hasMore ? (
                <Flex justify="center" style={{ borderTop: '1px solid var(--olive-5)' }}>
                  <Button variant="ghost" size="2" onClick={() => void loadMore()} style={{ minHeight: rowHeight, margin: 0 }}>
                    {t('chat.repository.picker.loadMore')}
                  </Button>
                </Flex>
              ) : null}
            </>
          )}
        </Box>
      </Flex>

      <Flex direction="column" gap="1">
        <Text size="1" weight="bold" as="label">
          {t('chat.repository.picker.branch')}
        </Text>
        <Select.Root value={branch || undefined} onValueChange={setBranch} disabled={!selected} size={isMobile ? '3' : '2'}>
          <Select.Trigger
            data-testid="repo-picker-branch"
            aria-label={t('chat.repository.picker.branch')}
            placeholder={t('chat.repository.picker.branchFirst')}
          />
          <Select.Content position="popper">
            {branchOptions.map((b) => (
              <Select.Item key={b.name} value={b.name}>
                {b.default ? t('chat.repository.picker.defaultBranch', { branch: b.name }) : b.name}
              </Select.Item>
            ))}
          </Select.Content>
        </Select.Root>
      </Flex>

      {selected ? (
        <Box data-testid="repo-picker-note" style={{ background: 'var(--olive-2)', borderRadius: 'var(--radius-2)', padding: 'var(--space-3)' }}>
          <Text as="p" size="2" color="gray" style={{ margin: 0 }}>
            {t('chat.repository.picker.whatHappens')}
          </Text>
          <Box asChild style={{ margin: 'var(--space-1) 0 0', paddingLeft: 18, color: 'var(--slate-11)', fontSize: 'var(--font-size-2)' }}>
            <ul>
              <li>{t('chat.repository.picker.noteCopy', { repository: selected.fullName })}</li>
              <li>{t('chat.repository.picker.noteBranch')}</li>
              <li>{t('chat.repository.picker.noteToken', { branch: branch || selected.defaultBranch })}</li>
              <li>{t('chat.repository.picker.notePullRequest', { branch: branch || selected.defaultBranch })}</li>
            </ul>
          </Box>
        </Box>
      ) : null}

      {refusal ? (
        <Text as="p" size="2" role="alert" data-testid="repo-picker-refusal" style={{ color: 'var(--red-11)', margin: 0 }}>
          {refusal}
        </Text>
      ) : null}

      <Flex justify="end" gap="2">
        <Button variant="soft" color="gray" size={isMobile ? '3' : '2'} onClick={onClose}>
          {t('chat.repository.picker.cancel')}
        </Button>
        <Button
          size={isMobile ? '3' : '2'}
          data-testid="repo-picker-use"
          disabled={!selected || saving}
          loading={saving}
          onClick={() => void use()}
        >
          {t('chat.repository.picker.use')}
        </Button>
      </Flex>
    </Flex>
  );
}

function RepositoryRow({
  repo,
  first,
  height,
  selected,
  onSelect,
}: {
  repo: GithubRepository;
  first: boolean;
  height: number;
  selected: boolean;
  onSelect: () => void;
}) {
  const { t } = useTranslation();
  const [hovered, setHovered] = useState(false);
  const updated = Date.parse(repo.updatedAt);
  return (
    <Flex
      role="option"
      aria-selected={selected}
      tabIndex={0}
      data-testid="repo-picker-row"
      data-repository={repo.fullName}
      align="center"
      justify="between"
      gap="2"
      onClick={onSelect}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onSelect();
        }
      }}
      style={{
        minHeight: height,
        padding: '0 var(--space-3)',
        borderTop: first ? undefined : '1px solid var(--olive-5)',
        background: selected ? 'var(--accent-a3)' : hovered ? 'var(--olive-3)' : undefined,
        cursor: 'pointer',
      }}
    >
      <Flex align="center" gap="2" style={{ minWidth: 0 }}>
        <Text size="2" weight={selected ? 'medium' : undefined} style={{ overflowWrap: 'anywhere', minWidth: 0 }}>
          {repo.fullName}
        </Text>
        {repo.private ? (
          <Text
            size="1"
            data-testid="repo-picker-private"
            style={{ color: 'var(--slate-11)', border: '1px solid var(--olive-6)', borderRadius: 'var(--radius-1)', padding: '0 4px', flexShrink: 0 }}
          >
            {t('chat.repository.picker.private')}
          </Text>
        ) : null}
      </Flex>
      {Number.isFinite(updated) ? (
        <Text size="1" color="gray" style={{ flexShrink: 0 }}>
          {t('chat.repository.picker.updated', { when: formatRelativeTime(updated) })}
        </Text>
      ) : null}
    </Flex>
  );
}
