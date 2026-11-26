'use client';

import React, { useEffect, useId, useMemo, useState } from 'react';
import { Box, Flex, Spinner, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { formatBytes } from '../../activity';
import { ChatApi } from '../../api';
import type { DurableAgentRunHandle, WorkspaceFileList } from '../../types';
import { ReferenceLines, treeLines } from '../message-area/sandbox-result-rows';

type TreeState = { status: 'closed' } | { status: 'loading' } | { status: 'ready'; list: WorkspaceFileList } | { status: 'failed' };

/**
 * C10.06 (WP-89): the conversation's saved files as a read-only tree, only here in the activity
 * drawer and folded until opened, so the chat's main view keeps no workspace of its own. It reads `/files`' list
 * (sizes only, never content) when opened; reading a file stays the chat's `/open` and `/download`.
 */
export function WorkspaceTree({ run }: { run: DurableAgentRunHandle }) {
  const { t, i18n } = useTranslation();
  const [state, setState] = useState<TreeState>({ status: 'closed' });
  const contentId = useId();
  const open = state.status !== 'closed';
  useEffect(() => {
    if (state.status !== 'loading') return undefined;
    let alive = true;
    ChatApi.listWorkspaceFiles(run)
      .then((list) => alive && setState({ status: 'ready', list }))
      .catch(() => alive && setState({ status: 'failed' }));
    return () => {
      alive = false;
    };
  }, [state.status, run]);
  const lines = useMemo(() => (state.status === 'ready' ? treeLines(state.list.files) : []), [state]);
  const size = (bytes: number | undefined) => (bytes === undefined ? '' : formatBytes(bytes, i18n.language));
  return (
    <Box data-testid="activity-section-workspace">
      <h3 style={{ margin: 0 }}>
        <button
          type="button"
          aria-expanded={open}
          aria-controls={contentId}
          onClick={() => setState(open ? { status: 'closed' } : { status: 'loading' })}
          style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', width: '100%', minHeight: 32, background: 'none', border: 0, padding: 0, cursor: 'pointer', color: 'var(--slate-12)', font: 'inherit' }}
        >
          <MaterialIcon name={open ? 'expand_less' : 'expand_more'} size={16} color="var(--slate-11)" />
          <Text size="2" weight="medium">{t('activityPanel.section.workspace')}</Text>
        </button>
      </h3>
      {open ? (
        <Box id={contentId} style={{ marginTop: 'var(--space-2)' }}>
          {state.status === 'loading' ? <Spinner size="1" /> : null}
          {state.status === 'failed' ? <Text size="1" color="gray">{t('activityPanel.workspace.failed')}</Text> : null}
          {state.status === 'ready' && state.list.files.length === 0 && (state.list.references ?? []).length === 0 ? (
            <Text size="1" color="gray">{t('slashCommands.reason.noWorkspace')}</Text>
          ) : null}
          {state.status === 'ready' && state.list.files.length > 0 ? (
            <Flex direction="column" gap="1">
              <Text size="1" color="gray">
                {t('slashCommands.result.files.summary', {
                  revision: state.list.revision,
                  count: state.list.files.length + state.list.omitted,
                  size: size(state.list.totalBytes),
                })}
              </Text>
              <Box role="tree" aria-label={t('activityPanel.workspace.treeLabel')} data-testid="workspace-tree" style={{ fontFamily: 'var(--code-font-family, monospace)' }}>
                {lines.map((line) => (
                  <Flex
                    key={line.key}
                    role="treeitem"
                    aria-level={line.depth + 1}
                    aria-expanded={line.folder ? true : undefined}
                    align="center"
                    gap="2"
                    style={{ paddingLeft: `calc(${String(line.depth)} * var(--space-4))`, minHeight: 24 }}
                  >
                    <Text size="1" weight={line.folder ? 'bold' : 'regular'} style={{ flex: 1, minWidth: 0, overflowWrap: 'anywhere' }}>
                      {line.path === undefined && !line.folder ? t('slashCommands.result.files.more', { label: line.label }) : line.label}
                    </Text>
                    <Text size="1" color="gray" style={{ whiteSpace: 'nowrap' }}>{size(line.size)}</Text>
                  </Flex>
                ))}
              </Box>
            </Flex>
          ) : null}
          {state.status === 'ready' ? <ReferenceLines references={state.list.references ?? []} /> : null}
          {state.status === 'ready' ? <Text size="1" color="gray">{t('activityPanel.workspace.readOnly')}</Text> : null}
        </Box>
      ) : null}
    </Box>
  );
}
