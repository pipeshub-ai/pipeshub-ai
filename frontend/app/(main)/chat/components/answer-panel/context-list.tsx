'use client';

import React, { type ReactNode } from 'react';
import { Box, Flex, Text } from '@radix-ui/themes';
import useSWR from 'swr';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ConnectorIcon, resolveConnectorType } from '@/app/components/ui/ConnectorIcon';
import { FileIcon } from '@/app/components/ui/file-icon';
import { KbNodeNameIcon } from '@/app/(main)/knowledge-base/utils/kb-node-name-icon';
import type { NodeType } from '@/app/(main)/knowledge-base/types';
import { ListSkeleton } from '@/app/components/ui/list-skeleton';
import { useDeferredLoading } from '@/app/components/ui/use-deferred-loading';
import { ChatApi } from '../../api';
import { useChatStore } from '../../store';
import type { AppliedFilterNode, AppliedFilters, AttachmentRef } from '../../types';
import { BELOW_APP_KEYS, hasAnyFilter, selectedNodeKind } from '../../utils/tree-selection';
import { getConnectorConfig } from '../message-area/response-tabs/citations/utils';
import type { MessagePair } from '../message-area/message-pairs';
import { GroupLabel } from '../group-label';
import { PanelEmpty } from './panel-empty';

/**
 * A connector, once, with the names of its instances the question used; or one part of a connector the question was
 * pointed at (a record group or folder), as its own row: its name, with the connector's under it.
 */
interface ConnectorRow {
  key: string;
  connector: string;
  label: string;
  names: string[];
  /** Set on a part of a connector: the node, for its own mark. */
  part?: AppliedFilterNode;
}

interface FileRow {
  key: string;
  name: string;
  extension?: string;
  mimeType?: string;
}

export interface ContextGroups {
  /** `folder` is set on a folder inside a collection: its own row, not a collection. */
  collections: Array<{ key: string; name: string; folder?: AppliedFilterNode }>;
  connectors: ConnectorRow[];
  files: FileRow[];
}

const NO_FILTERS: AppliedFilters = { apps: [], kb: [] };

/** A collection's own app reads `KB`; one saved before the connector was kept reads empty. */
function isCollection(connector: string | undefined): boolean {
  if (!connector) return true;
  const type = resolveConnectorType(connector);
  return type === 'kb' || type === 'knowledge-base';
}

/**
 * What a question could draw on, in the column's groups: collections (and folders in them), connectors (one row per
 * kind of connector, with the names of the instances it used, and one row per record group or folder of a connector)
 * and files (picked records and uploads). Each thing once, as stored.
 */
export function contextGroups(filters: AppliedFilters | undefined, attachments?: readonly AttachmentRef[]): ContextGroups {
  const groups: ContextGroups = { collections: [], connectors: [], files: [] };
  const seen = new Set<string>();
  const apps = new Map<string, ConnectorRow>();
  const place = (node: AppliedFilterNode, kind: 'connector' | 'collection' | 'folder' | 'file') => {
    if (!node.id || seen.has(node.id)) return;
    seen.add(node.id);
    if (kind === 'file') {
      groups.files.push({ key: node.id, name: node.name, extension: node.extension, mimeType: node.mimeType });
    } else if (isCollection(node.connector)) {
      groups.collections.push(kind === 'folder' && node.nodeType === 'folder' ? { key: node.id, name: node.name, folder: node } : { key: node.id, name: node.name });
    } else if (kind !== 'connector') {
      // Only this part of the connector was searched, not all of it.
      groups.connectors.push({ key: node.id, connector: node.connector, label: node.name, names: [getConnectorConfig(node.connector).label], part: node });
    } else {
      const type = resolveConnectorType(node.connector);
      let row = apps.get(type);
      if (!row) {
        row = { key: type, connector: node.connector, label: getConnectorConfig(node.connector).label, names: [] };
        apps.set(type, row);
        groups.connectors.push(row);
      }
      if (node.name && !row.names.includes(node.name)) row.names.push(node.name);
    }
  };
  for (const node of filters?.apps ?? []) place(node, node.nodeType === 'app' ? 'connector' : 'collection');
  for (const node of filters?.kb ?? []) place(node, 'collection');
  for (const bucket of BELOW_APP_KEYS) {
    for (const node of filters?.[bucket] ?? []) place(node, selectedNodeKind(bucket, node.nodeType));
  }
  for (const file of attachments ?? []) {
    if (!file.recordId || seen.has(file.recordId)) continue;
    seen.add(file.recordId);
    groups.files.push({ key: file.recordId, name: file.recordName, extension: file.extension, mimeType: file.mimeType });
  }
  return groups;
}

/** The muted line under a connector row: the names it used (or its connector's), unless that only repeats its own. */
export function connectorDetail(row: Pick<ConnectorRow, 'label' | 'names'>): string | null {
  const names = row.names.map((name) => name.trim()).filter(Boolean);
  if (names.length === 0) return null;
  if (names.length === 1 && names[0].toLowerCase() === row.label.trim().toLowerCase()) return null;
  return names.join(' · ');
}

/** True when the question was pointed at nothing the column lists. */
export const isContextEmpty = (groups: ContextGroups) =>
  groups.collections.length + groups.connectors.length + groups.files.length === 0;

const NAME_STYLE = { color: 'var(--text-primary)', minWidth: 0, overflowWrap: 'anywhere' } as const;

/** One row as a collection is drawn: a 16px glyph and the name, on one line where it fits. */
function PlainRow({ icon, name }: { icon: ReactNode; name: string }) {
  return (
    <Flex align="center" gap="2" py="1">
      <span aria-hidden style={{ display: 'inline-flex', flexShrink: 0 }}>
        {icon}
      </span>
      <Text size="2" style={NAME_STYLE}>
        {name}
      </Text>
    </Flex>
  );
}

/** A row with a muted second line, as a connector is drawn. */
function DetailRow({ icon, name, detail }: { icon: ReactNode; name: string; detail: string | null }) {
  return (
    <Flex align="start" gap="2" py="1">
      <Box mt="1" style={{ flexShrink: 0, lineHeight: 0 }} aria-hidden>
        {icon}
      </Box>
      <Flex direction="column" style={{ minWidth: 0, rowGap: 'calc(var(--space-1) / 4)' }}>
        <Text size="2" style={NAME_STYLE}>
          {name}
        </Text>
        {detail && (
          <Text size="1" style={{ color: 'var(--text-secondary)', overflowWrap: 'anywhere' }}>
            {detail}
          </Text>
        )}
      </Flex>
    </Flex>
  );
}

/** A picked folder or record group, marked as the composer's chip marks it. */
const partIcon = (node: AppliedFilterNode) => (
  <KbNodeNameIcon isKnowledgeHub nodeType={(node.nodeType || 'folder') as NodeType} connector={node.connector || null} name={node.name} size={16} />
);

function Group({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Flex direction="column" gap="1">
      <GroupLabel>{label}</GroupLabel>
      <Box style={{ height: 'calc(var(--space-1) / 2)' }} />
      {children}
    </Flex>
  );
}

/**
 * The Context tab's list: what the question was pointed at, in the column's groups (Collections, Connectors, Files),
 * each skipped when empty. `after` takes rows a caller adds of its own, in the same row shape. Nothing to list: null.
 */
export function ContextList({
  filters,
  attachments,
  after,
}: {
  filters: AppliedFilters | undefined;
  attachments?: readonly AttachmentRef[];
  after?: ReactNode;
}) {
  const { t } = useTranslation();
  const groups = contextGroups(filters, attachments);
  if (isContextEmpty(groups) && !after) return null;
  return (
    <Flex direction="column" gap="4" data-testid="context-list">
      {groups.collections.length > 0 && (
        <Group label={t('chat.answerPanel.contextCollections')}>
          {groups.collections.map((row) =>
            row.folder ? (
              <DetailRow key={row.key} name={row.name} icon={partIcon(row.folder)} detail={t('chat.answerPanel.contextFolderInCollection')} />
            ) : (
              <PlainRow key={row.key} name={row.name} icon={<MaterialIcon name="folder" variant="filled" size={16} color="var(--accent-9)" />} />
            ),
          )}
        </Group>
      )}
      {groups.connectors.length > 0 && (
        <Group label={t('chat.answerPanel.contextConnectors')}>
          {groups.connectors.map((row) => (
            <DetailRow
              key={row.key}
              name={row.label}
              detail={connectorDetail(row)}
              icon={row.part ? partIcon(row.part) : <ConnectorIcon type={row.connector} size={16} />}
            />
          ))}
        </Group>
      )}
      {groups.files.length > 0 && (
        <Group label={t('chat.answerPanel.contextFiles')}>
          {groups.files.map((row) => (
            <PlainRow key={row.key} name={row.name} icon={<FileIcon filename={row.name} extension={row.extension} mimeType={row.mimeType} size={16} />} />
          ))}
        </Group>
      )}
      {after}
    </Flex>
  );
}

/** A row the reader may not see the name of, drawn as a collection row with a lock. */
export function ContextLockedRow({ label }: { label: string }) {
  return <PlainRow name={label} icon={<MaterialIcon name="lock" size={16} color="var(--text-secondary)" />} />;
}

/** The tab's own skeleton while its list loads, shown only once the wait is long enough to notice. */
export function ContextLoading({ exiting }: { exiting: boolean }) {
  const { t } = useTranslation();
  return (
    <Box>
      <ListSkeleton
        variant="menu-row"
        glyphSize={14}
        count={2}
        // The hook already waited out a fast load; no second delay here.
        appearDelayMs={0}
        statusLabel={t('loading.default')}
        exiting={exiting}
      />
    </Box>
  );
}

/** Nothing the question was pointed at, and nothing it searched: the design's empty Context. */
export function ContextEmpty() {
  const { t } = useTranslation();
  return <PanelEmpty icon="hub" testId="context-empty" title={t('chat.answerPanel.contextEmptyTitle')} body={t('chat.answerPanel.contextEmptyBody')} />;
}

type Origin = 'COLLECTION' | 'CONNECTOR';

// shortcut: one page at the composer's apps panel page size (20), as the panel shows before it is scrolled; a reader
// with more apps sees the first 20 here. Follow the cursor if that reads short.
const REACH_PAGE = 20;

async function reachOf([, origins]: readonly [string, Origin]): Promise<AppliedFilterNode[]> {
  const res = await ChatApi.listCollectionsForChat({ limit: REACH_PAGE, origins }, { suppressErrorToast: true });
  return res.knowledgeBases.map((item) => ({ id: item.id, name: item.name, nodeType: item.nodeType, connector: item.connector ?? '' }));
}

// Asked once per session: what a person can reach changes rarely, and the tab only lists it.
const ONCE = { revalidateOnFocus: false, revalidateIfStale: false, revalidateOnReconnect: false, shouldRetryOnError: false } as const;

/**
 * What the reader can reach now, as the composer's apps panel lists it: their collections, then their connectors.
 * Undefined while it loads, null when it could not be listed.
 */
export function useReachableApps(): AppliedFilters | null | undefined {
  const collections = useSWR(['chat-context-reach', 'COLLECTION'] as const, reachOf, ONCE);
  const connectors = useSWR(['chat-context-reach', 'CONNECTOR'] as const, reachOf, ONCE);
  if (collections.error || connectors.error) return null;
  if (!collections.data || !connectors.data) return undefined;
  return { apps: connectors.data, kb: collections.data };
}

/** An unnarrowed question searched what its asker can reach: listed as it stands now, with the files it came with. */
export function ReachContext({ attachments }: { attachments?: readonly AttachmentRef[] }) {
  const { t } = useTranslation();
  const reach = useReachableApps();
  const loading = useDeferredLoading(reach === undefined);
  if (loading.visible) return <ContextLoading exiting={loading.exiting} />;
  if (reach === undefined) return null;
  if (reach === null) return <PanelEmpty icon="hub" testId="context-anything" title={t('chat.answerPanel.anythingYouCanOpen')} />;
  // A reader who reaches nothing searched nothing of theirs.
  if (isContextEmpty(contextGroups(reach, attachments))) return <ContextEmpty />;
  return <ContextList filters={reach} attachments={attachments} />;
}

/**
 * The Context tab for one question: what it was pointed at; with nothing picked, what it searched, which is what its
 * asker can reach. The reader's own question lists the reader's reach; anyone else's says it could use whatever they
 * can open, as that is theirs. A search that reaches nothing of anyone's (web or image answers, or a chat scoped to an
 * agent or a project that stored no sources) shows the empty Context.
 */
export function PanelContext({
  pair,
  slotId,
  askedByReader,
  askerName,
}: {
  pair: MessagePair;
  slotId: string;
  /**
   * Who asked, when the caller knows: the reader, or someone else (named `askerName`), whose picks the caller lists
   * itself as the reader may see them. Unset: every question is the chat's owner's, so a reader who does not own the
   * chat reads the owner's.
   */
  askedByReader?: boolean;
  askerName?: string;
}) {
  const { t } = useTranslation();
  const scoped = useChatStore((s) => Boolean(s.slots[slotId]?.threadAgentId || s.slots[slotId]?.projectId));
  const notOwner = useChatStore((s) => s.slots[slotId]?.isOwner === false);
  // The owner's name comes from the chats shared with the reader, when that list has this one.
  const ownerName = useChatStore((s) => {
    const convId = s.slots[slotId]?.convId;
    return (convId && s.sharedConversations.find((c) => c.id === convId)?.sharedBy?.name?.trim()) || null;
  });
  const byReader = askedByReader ?? !notOwner;
  const theirName = askedByReader === undefined ? ownerName : askerName?.trim() || null;
  // A caller that names the asker lists that person's picks and files itself, as the reader may see them.
  const listsPicks = askedByReader !== false;
  const filters = pair.appliedFilters;
  if (listsPicks && hasAnyFilter(filters)) return <ContextList filters={filters} attachments={pair.attachments} />;
  const mode = pair.modelInfo?.chatMode;
  if (scoped || mode === 'web_search' || mode === 'image') {
    return listsPicks && pair.attachments?.length ? <ContextList filters={NO_FILTERS} attachments={pair.attachments} /> : <ContextEmpty />;
  }
  if (!byReader) {
    return (
      <PanelEmpty
        icon="hub"
        testId="context-anything"
        title={theirName ? t('chat.answerPanel.anythingTheyCanOpen', { name: theirName }) : t('chat.answerPanel.anythingOwnerCanOpen')}
      />
    );
  }
  return <ReachContext attachments={pair.attachments} />;
}
