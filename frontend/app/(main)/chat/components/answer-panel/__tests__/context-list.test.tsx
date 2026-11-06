import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { SWRConfig } from 'swr';
import '@/lib/__tests__/test-i18n';

const api = vi.hoisted(() => ({ listCollectionsForChat: vi.fn() }));
vi.mock('@/app/(main)/chat/api', () => ({ ChatApi: api }));

import { useChatStore } from '../../../store';
import type { AppliedFilters, AttachmentRef, Conversation } from '../../../types';
import type { MessagePair } from '../../message-area/message-pairs';
import { ContextList, PanelContext, connectorDetail, contextGroups } from '../context-list';

const node = (id: string, name: string, connector: string, nodeType = 'app') => ({ id, name, connector, nodeType });
const FILTERS: AppliedFilters = {
  apps: [
    node('kb-eng', 'Engineering Handbook', 'KB'),
    node('kb-old', 'Old handbook', ''),
    node('jira-a', 'Acme Jira', 'JIRA'),
    node('jira-b', 'Labs Jira', 'JIRA'),
    node('conf', 'Confluence', 'CONFLUENCE'),
  ],
  kb: [],
  records: [node('rec-1', 'Q3 plan.pdf', 'KB', 'record')],
};
const UPLOAD: AttachmentRef = { recordId: 'up-1', recordName: 'notes.txt', mimeType: 'text/plain', extension: 'txt', virtualRecordId: 'v1' };

const initialChatState = useChatStore.getState();
let slotId = '';
beforeEach(() => {
  useChatStore.setState(initialChatState, true);
  slotId = useChatStore.getState().createSlot('conv-1');
  api.listCollectionsForChat.mockReset();
});
afterEach(() => cleanup());

const withSwr = (ui: React.ReactElement) =>
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <Theme>{ui}</Theme>
    </SWRConfig>,
  );
const pairOf = (patch: Partial<MessagePair>) => ({ key: 'a1', question: 'Q', answer: 'A', isStreaming: false, ...patch }) as MessagePair;
const listing = (byOrigin: Record<string, Array<{ id: string; name: string; connector: string }>>) =>
  api.listCollectionsForChat.mockImplementation(async ({ origins }: { origins: string }) => ({
    knowledgeBases: (byOrigin[origins] ?? []).map((item) => ({ ...item, nodeType: 'app' })),
    requestedLimit: 20,
    nextCursor: null,
    serverPagination: null,
  }));

describe('contextGroups and connectorDetail', () => {
  it('sorts a question\'s sources into collections, one row per kind of connector, and files', () => {
    const groups = contextGroups(FILTERS, [UPLOAD]);
    expect(groups.collections.map((row) => row.name)).toEqual(['Engineering Handbook', 'Old handbook']);
    expect(groups.connectors.map((row) => [row.label, connectorDetail(row)])).toEqual([
      ['Jira', 'Acme Jira · Labs Jira'],
      // The instance only repeats the connector's name: no second line.
      ['Confluence', null],
    ]);
    expect(groups.files.map((row) => row.name)).toEqual(['Q3 plan.pdf', 'notes.txt']);
  });

  it('draws only the groups that have rows', () => {
    withSwr(<ContextList filters={{ apps: [node('kb-eng', 'Engineering Handbook', 'KB')], kb: [] }} />);
    expect(screen.getByText('Collections')).toBeTruthy();
    expect(screen.getByText('Engineering Handbook')).toBeTruthy();
    expect(screen.queryByText('Connectors')).toBeNull();
    expect(screen.queryByText('Files')).toBeNull();
  });
});

/** One section's rows, each as its lines of text ("name | secondary line"). */
const rowsIn = (label: string) => {
  const section = screen.getByText(label).parentElement!;
  return Array.from(section.children)
    .slice(2)
    .map((row) => Array.from(row.querySelectorAll('.rt-Text')).map((text) => text.textContent).join(' | '));
};

// Every kind a question can be pointed at, as the composer stores them: an app, record groups and a folder of a
// connector, a collection (as the hub lists it, as the apps panel lists it, and under `kb`), a folder in a collection,
// records from a collection and from a connector, a record picked alone, and an upload.
const EVERY_KIND: AppliedFilters = {
  apps: [node('app-linear', 'Linear', 'LINEAR'), node('kb-eng', 'Engineering Handbook', 'KB')],
  kb: [node('kb-legal', 'Legal holds', 'KB')],
  recordGroups: [
    node('rg-es', 'Enterprise Search', 'JIRA', 'recordGroup'),
    node('rg-pp', 'PipesHub Projects', 'JIRA', 'recordGroup'),
    node('kb-private', "darshan's Private", 'KB', 'recordGroup'),
  ],
  records: [
    node('fol-test', 'test-folder', 'KB', 'folder'),
    node('fol-design', 'Design docs', 'CONFLUENCE', 'folder'),
    node('rec-cb', 'connector-builder (1)', 'KB', 'record'),
    node('rec-pipes', 'PIPES-12: Login fails', 'JIRA', 'record'),
  ],
  recordsExact: [node('rec-q3', 'Q3 plan.pdf', 'KB', 'record')],
};

describe('one row per picked source, in its own section', () => {
  it('every kind: record groups and folders of a connector are their own rows, under the connector\'s name; a folder in a collection is not a collection', () => {
    withSwr(<ContextList filters={EVERY_KIND} attachments={[UPLOAD]} />);
    expect(rowsIn('Collections')).toEqual(['Engineering Handbook', 'Legal holds', "darshan's Private", 'test-folder | Folder in a collection']);
    expect(rowsIn('Connectors')).toEqual(['Linear', 'Enterprise Search | Jira', 'PipesHub Projects | Jira', 'Design docs | Confluence']);
    expect(rowsIn('Files')).toEqual(['connector-builder (1)', 'PIPES-12: Login fails', 'Q3 plan.pdf', 'notes.txt']);
  });

  it('only two record groups of Jira picked: two rows, and no row for all of Jira', () => {
    withSwr(<ContextList filters={{ apps: [], kb: [], recordGroups: EVERY_KIND.recordGroups!.slice(0, 2) }} />);
    expect(rowsIn('Connectors')).toEqual(['Enterprise Search | Jira', 'PipesHub Projects | Jira']);
    expect(screen.queryByText('Collections')).toBeNull();
  });

  it('all of Jira and one of its projects: the connector row and the project row', () => {
    withSwr(<ContextList filters={{ apps: [node('app-jira', 'Jira', 'JIRA')], kb: [], recordGroups: EVERY_KIND.recordGroups!.slice(0, 1) }} />);
    expect(rowsIn('Connectors')).toEqual(['Jira', 'Enterprise Search | Jira']);
  });

  it('a folder in a collection alone: one row under Collections, marked as a folder', () => {
    withSwr(<ContextList filters={{ apps: [], kb: [], records: [node('fol-test', 'test-folder', 'KB', 'folder')] }} />);
    expect(rowsIn('Collections')).toEqual(['test-folder | Folder in a collection']);
  });
});

describe('PanelContext', () => {
  it('a narrowed question lists exactly what it was pointed at, and asks for nothing', () => {
    withSwr(<PanelContext slotId={slotId} pair={pairOf({ appliedFilters: FILTERS, attachments: [UPLOAD] })} />);
    expect(screen.getByText('Engineering Handbook')).toBeTruthy();
    expect(screen.getByText('Acme Jira · Labs Jira')).toBeTruthy();
    expect(screen.getByText('notes.txt')).toBeTruthy();
    expect(api.listCollectionsForChat).not.toHaveBeenCalled();
  });

  it('an unnarrowed question lists what the reader can reach, collections then connectors, without an error toast', async () => {
    listing({ COLLECTION: [{ id: 'c1', name: 'Sales', connector: 'KB' }, { id: 'c2', name: 'Legal', connector: 'KB' }], CONNECTOR: [{ id: 'j1', name: 'Acme Jira', connector: 'JIRA' }] });
    withSwr(<PanelContext slotId={slotId} pair={pairOf({ modelInfo: { chatMode: 'internal_search' } as MessagePair['modelInfo'] })} />);
    expect(await screen.findByText('Legal')).toBeTruthy();
    expect(screen.getByText('Sales')).toBeTruthy();
    expect(screen.getByText('Acme Jira')).toBeTruthy();
    expect(api.listCollectionsForChat).toHaveBeenCalledWith({ limit: 20, origins: 'COLLECTION' }, { suppressErrorToast: true });
    expect(api.listCollectionsForChat).toHaveBeenCalledWith({ limit: 20, origins: 'CONNECTOR' }, { suppressErrorToast: true });
  });

  it('nothing it could search: web answers, an agent or project chat with no stored sources, or a reader who reaches nothing', async () => {
    withSwr(<PanelContext slotId={slotId} pair={pairOf({ modelInfo: { chatMode: 'web_search' } as MessagePair['modelInfo'] })} />);
    expect(screen.getByText('Nothing attached yet')).toBeTruthy();
    cleanup();

    useChatStore.getState().updateSlot(slotId, { threadAgentId: 'agent-1' });
    withSwr(<PanelContext slotId={slotId} pair={pairOf({ appliedFilters: { apps: [], kb: [] } })} />);
    expect(screen.getByText('Nothing attached yet')).toBeTruthy();
    expect(api.listCollectionsForChat).not.toHaveBeenCalled();
    cleanup();

    useChatStore.getState().updateSlot(slotId, { threadAgentId: undefined });
    listing({});
    withSwr(<PanelContext slotId={slotId} pair={pairOf({})} />);
    expect(await screen.findByText('Nothing attached yet')).toBeTruthy();
  });

  it("someone a chat was shared with reads its owner's unnarrowed question as the owner's reach, named, without asking for theirs", () => {
    useChatStore.getState().updateSlot(slotId, { isOwner: false });
    useChatStore.setState({ sharedConversations: [{ id: 'conv-1', sharedBy: { userId: 'u-owner', name: 'Upg Admin' } } as Conversation] });
    withSwr(<PanelContext slotId={slotId} pair={pairOf({})} />);
    expect(screen.getByTestId('context-anything').textContent).toContain('Could use anything Upg Admin can open');
    expect(api.listCollectionsForChat).not.toHaveBeenCalled();
  });

  it("with no name for the owner, the line names the chat's owner", () => {
    useChatStore.getState().updateSlot(slotId, { isOwner: false });
    withSwr(<PanelContext slotId={slotId} pair={pairOf({})} />);
    expect(screen.getByTestId('context-anything').textContent).toContain("Could use anything the chat's owner can open");
    expect(api.listCollectionsForChat).not.toHaveBeenCalled();
  });

  it("the chat's owner still sees what they can reach", async () => {
    useChatStore.getState().updateSlot(slotId, { isOwner: true });
    listing({ COLLECTION: [{ id: 'c1', name: 'Sales', connector: 'KB' }] });
    withSwr(<PanelContext slotId={slotId} pair={pairOf({})} />);
    expect(await screen.findByText('Sales')).toBeTruthy();
    expect(screen.queryByTestId('context-anything')).toBeNull();
  });

  it('told the reader asked it, a question lists the reader\'s reach even in a chat they do not own', async () => {
    useChatStore.getState().updateSlot(slotId, { isOwner: false });
    listing({ COLLECTION: [{ id: 'c1', name: 'Bob handbook', connector: 'KB' }] });
    withSwr(<PanelContext slotId={slotId} pair={pairOf({})} askedByReader askerName="Bob Tester" />);
    expect(await screen.findByText('Bob handbook')).toBeTruthy();
    expect(api.listCollectionsForChat).toHaveBeenCalled();
    expect(screen.queryByTestId('context-anything')).toBeNull();
  });

  it("told someone else asked it, it could use anything they can open, naming none of their picks and asking for no listing", () => {
    useChatStore.getState().updateSlot(slotId, { isOwner: true });
    const picks: AppliedFilters = { apps: [], kb: [], records: [node('rec-1', 'Q3 plan.pdf', 'KB', 'record')] };
    withSwr(<PanelContext slotId={slotId} pair={pairOf({ appliedFilters: picks, attachments: [UPLOAD] })} askedByReader={false} askerName="Maya Chen" />);
    expect(screen.getByTestId('context-anything').textContent).toContain('Could use anything Maya Chen can open');
    expect(screen.queryByText('Q3 plan.pdf')).toBeNull();
    expect(screen.queryByText('notes.txt')).toBeNull();
    expect(api.listCollectionsForChat).not.toHaveBeenCalled();
  });

  it('a listing that fails says it could use anything you can open', async () => {
    api.listCollectionsForChat.mockRejectedValue(new Error('offline'));
    withSwr(<PanelContext slotId={slotId} pair={pairOf({})} />);
    expect(await screen.findByText('Could use anything you can open')).toBeTruthy();
  });
});
