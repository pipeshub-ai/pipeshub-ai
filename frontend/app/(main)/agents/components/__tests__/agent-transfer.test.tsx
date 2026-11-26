/**
 * WP-52 C4.08: export and import of an agent's configuration in the UI.
 * AT-1 a file is read locally and refused before anything is sent when it is
 * not an export or of a newer schema; AT-2 the import shows the file by name,
 * imports, keeps the result and opens the builder; AT-3 a server refusal is
 * shown and nothing opens; AT-4 the builder's pinned list says what needs
 * choosing and what to set again; AT-5 the export defaults to the live
 * version and offers the others and the draft; AT-6 the import result and
 * the file name helpers; AT-7 a model reads by the importer's display name for it,
 * else "provider / model"; AT-8 a preview row stacks tightly on a phone (its
 * label sets a width, never a flex basis, which a column turns into a height).
 */
import React from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';

const { client, push, versions } = vi.hoisted(() => ({
  client: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  push: vi.fn(),
  versions: vi.fn(),
}));

vi.mock('next/navigation', () => ({ useRouter: () => ({ push, replace: vi.fn() }) }));
vi.mock('../../api', () => ({ AgentsApi: { listAgentVersions: versions } }));
vi.mock('@/lib/api', () => ({ apiClient: client }));

import { ImportAgentDialog, PreviewRow } from '../import-agent-dialog';
import { ImportNeedsChoice } from '../import-needs-choice';
import { ExportAgentDialog } from '../export-agent-dialog';
import {
  exportFileName,
  readAgentExport,
  readImportResult,
  saveImportResult,
  clearImportResult,
  modelDisplayName,
  modelLabelDisplayName,
  toImportResult,
} from '../../config-transfer';

const EXPORT = {
  format: 'pipeshub.agent-config',
  schemaVersion: 1,
  exportedAt: 1,
  source: { agentVersion: 3, digest: null },
  agent: {
    name: 'Support triage',
    models: [{ provider: 'openai', modelName: 'gpt-x' }],
    toolsets: [{ name: 'slack', displayName: 'Slack', type: 'app', instanceName: 'Slack', tools: [] }],
    mcpServers: [],
    knowledge: [{ name: 'Support Drive', type: 'drive', scope: 'whole', limitedItems: 0 }],
    skills: ['xlsx'],
  },
  policy: { budgets: { maxTurns: 20, maxCostUsd: 2 } },
  excluded: ['sharing'],
};

const RESULT = {
  agentKey: 'agent-new',
  name: 'Support triage',
  bound: [{ kind: 'model', name: 'openai / gpt-x' }, { kind: 'toolset', name: 'Slack' }],
  rebind: [
    { kind: 'toolset', name: 'Jira Cloud', reason: 'not_found' },
    { kind: 'knowledge', name: 'Handbook', reason: 'limited' },
    { kind: 'model', name: 'anthropic / x', reason: 'ambiguous' },
  ],
  excluded: ['sharing', 'approvals'],
};

const fileOf = (content: unknown) =>
  new File([typeof content === 'string' ? content : JSON.stringify(content)], 'support.agent.json', { type: 'application/json' });

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});

beforeEach(() => {
  globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} } as never;
  vi.clearAllMocks();
  window.sessionStorage.clear();
});
afterEach(cleanup);

const openImport = () =>
  render(
    <Theme>
      <ImportAgentDialog open onOpenChange={vi.fn()} />
    </Theme>,
  );

const chooseFile = (file: File) => fireEvent.change(screen.getByLabelText('Agent file'), { target: { files: [file] } });

describe('C4.08 agent import and export (UI)', () => {
  it('AT-1: a file that is not an export, or of a newer schema, is refused before anything is sent', async () => {
    openImport();
    chooseFile(fileOf('not json'));
    expect(await screen.findByText('This is not an agent export PipesHub can import.')).toBeTruthy();
    chooseFile(fileOf({ ...EXPORT, schemaVersion: 3 }));
    expect(await screen.findByText('This export is schema version 3; this PipesHub reads versions 1 and 2.')).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Import' }) as HTMLButtonElement).disabled).toBe(true);
    expect(client.post).not.toHaveBeenCalled();
  });

  it('AT-2: the file is shown by name; Import makes the agent, keeps what needs choosing and opens the builder', async () => {
    client.post.mockResolvedValue({ data: RESULT });
    client.get.mockResolvedValue({ data: { models: [] } });
    openImport();
    chooseFile(fileOf(EXPORT));
    expect(await screen.findByText('Import “Support triage”')).toBeTruthy();
    expect(screen.getByText('Exported from version 3. It will be a new agent of yours, not shared.')).toBeTruthy();
    expect(screen.getByText('openai / gpt-x')).toBeTruthy();
    expect(screen.getByText('20 turns, $2 per run')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
    await waitFor(() => expect(push).toHaveBeenCalledWith('/agents/edit?agentKey=agent-new&imported=1'));
    expect(client.post).toHaveBeenCalledWith('/api/v1/agents/import', expect.objectContaining({ format: 'pipeshub.agent-config' }), expect.anything());
    expect(readImportResult('agent-new')?.rebind).toHaveLength(3);
  });

  it("AT-3: the server's refusal is shown and nothing opens", async () => {
    client.get.mockResolvedValue({ data: { models: [] } });
    client.post.mockRejectedValue({
      originalError: { response: { data: { error: { message: 'The limits in this file are above your organization’s.' } } } },
    });
    openImport();
    chooseFile(fileOf(EXPORT));
    await screen.findByText('Import “Support triage”');
    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
    expect(await screen.findByText(/The limits in this file are above your organization/)).toBeTruthy();
    expect(push).not.toHaveBeenCalled();
  });

  it('AT-4: the pinned list names each thing to choose with why, and what to set again; Choose opens the palette', () => {
    const onChoose = vi.fn();
    const onClose = vi.fn();
    render(
      <Theme>
        <ImportNeedsChoice result={toImportResult(RESULT)!} onChoose={onChoose} onClose={onClose} />
      </Theme>,
    );
    expect(screen.getByText('3 things need your choice')).toBeTruthy();
    expect(screen.getByText('Matched here: openai / gpt-x, Slack.')).toBeTruthy();
    expect(screen.getByText('No action here is called “Jira Cloud”.')).toBeTruthy();
    expect(screen.getByText('It searched only some folders or files of this source; choose them again.')).toBeTruthy();
    expect(screen.getByText("Set again yourself: who it's shared with, the organization's approval rules for it.")).toBeTruthy();
    // A model is chosen on the agent itself: no palette button for it.
    const choose = screen.getAllByRole('button', { name: 'Choose' });
    expect(choose).toHaveLength(2);
    fireEvent.click(choose[0]!);
    expect(onChoose).toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(onClose).toHaveBeenCalled();
  });

  it('AT-5: the export defaults to the live version, and downloads what was chosen', async () => {
    versions.mockResolvedValue({
      versions: [{ version: 2 }, { version: 3 }],
      latestVersion: 3,
      latestDigest: '',
      unpublishedChanges: true,
    });
    client.get.mockResolvedValue({ data: EXPORT });
    URL.createObjectURL = vi.fn(() => 'blob:x') as never;
    URL.revokeObjectURL = vi.fn();
    const onOpenChange = vi.fn();
    render(
      <Theme>
        <ExportAgentDialog open onOpenChange={onOpenChange} agentKey="agent-a" agentName="Support triage" />
      </Theme>,
    );
    const live = await screen.findByRole('radio', { name: 'Version 3 (live)' });
    expect((live as HTMLButtonElement).getAttribute('aria-checked')).toBe('true');
    expect(screen.getByRole('radio', { name: 'Version 2' })).toBeTruthy();
    expect(screen.getByRole('radio', { name: 'Unpublished draft' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Download' }));
    await waitFor(() =>
      expect(client.get).toHaveBeenCalledWith('/api/v1/agents/agent-a/export', expect.objectContaining({ params: { version: '3' } })),
    );
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
  });

  it('AT-6: helpers: a result with unknown kinds or no key is read safely; file names are safe; the list is forgotten when closed', () => {
    expect(toImportResult({ name: 'x' })).toBeNull();
    expect(toImportResult({ agentKey: 'k', rebind: [{ kind: 'weird', name: 'n', reason: 'odd' }], excluded: ['sharing', 'nope'] })).toEqual({
      agentKey: 'k',
      name: '',
      bound: [],
      rebind: [{ kind: 'model', name: 'n', reason: 'not_found' }],
      excluded: ['sharing'],
      notImported: [],
    });
    expect(exportFileName('Support / triage!')).toBe('Support-triage.agent.json');
    expect(exportFileName('★')).toBe('agent.agent.json');
    expect(readAgentExport(JSON.stringify(EXPORT)).ok).toBe(true);
    saveImportResult(toImportResult(RESULT)!);
    expect(readImportResult('agent-new')?.name).toBe('Support triage');
    clearImportResult('agent-new');
    expect(readImportResult('agent-new')).toBeNull();
  });

  it('AT-9: a part this edition does not offer is named as not imported, in plain words', () => {
    const result = toImportResult({
      ...RESULT,
      notImported: [
        { section: 'sectionA', reason: 'not_offered' },
        { section: 'sectionB', reason: 'not_offered' },
        { section: 'somethingNew', reason: 'not_offered' },
        { section: 7, reason: 'not_offered' },
      ],
    })!;
    expect(result.notImported).toEqual([
      { section: 'sectionA', reason: 'not_offered' },
      { section: 'sectionB', reason: 'not_offered' },
      { section: 'somethingNew', reason: 'not_offered' },
    ]);
    render(
      <Theme>
        <ImportNeedsChoice result={result} onChoose={() => {}} onClose={() => {}} />
      </Theme>,
    );
    const notice = screen.getByTestId('import-not-imported');
    expect(notice.textContent).toContain("A setting (sectionA) isn't available in this edition, so it wasn't imported.");
    expect(notice.textContent).toContain("A setting (sectionB) isn't available in this edition, so it wasn't imported.");
    expect(notice.textContent).toContain("A setting (somethingNew) isn't available in this edition, so it wasn't imported.");
  });

  it('AT-9: nothing left out shows no notice', () => {
    render(
      <Theme>
        <ImportNeedsChoice result={toImportResult(RESULT)!} onChoose={() => {}} onClose={() => {}} />
      </Theme>,
    );
    expect(screen.queryByTestId('import-not-imported')).toBeNull();
  });

  it('AT-7: a model reads by the display name the importer gives it, else "provider / model", in the preview and the pinned list', async () => {
    const configured = [{ provider: 'openAI', modelName: 'gpt-x', modelFriendlyName: 'GPT X (fast)' }];
    expect(modelDisplayName({ provider: 'openai', modelName: 'gpt-x' }, configured)).toBe('GPT X (fast)');
    expect(modelDisplayName({ provider: 'anthropic', modelName: 'claude-x' }, configured)).toBe('anthropic / claude-x');
    expect(modelDisplayName({ provider: 'openai', modelName: 'gpt-x' }, [{ provider: 'openai', modelName: 'gpt-x', modelFriendlyName: ' ' }])).toBe('openai / gpt-x');
    expect(modelLabelDisplayName('openai/gpt-x', configured)).toBe('GPT X (fast)');
    expect(modelLabelDisplayName('default: openai/gpt-x', configured)).toBe('default: GPT X (fast)');
    expect(modelLabelDisplayName('Slack', configured)).toBe('Slack');

    client.get.mockImplementation(async (url: string) =>
      url.endsWith('/ai-models/available/llm') ? { data: { models: [{ ...configured[0], modelKey: 'k-1', isReasoning: true }] } } : { data: {} },
    );
    openImport();
    chooseFile(fileOf(EXPORT));
    expect(await screen.findByText('GPT X (fast)')).toBeTruthy();
    expect(screen.queryByText('openai / gpt-x')).toBeNull();
    expect(document.body.textContent).not.toContain('k-1');
    cleanup();

    render(
      <Theme>
        <ImportNeedsChoice
          result={toImportResult({ ...RESULT, bound: [{ kind: 'model', name: 'openai/gpt-x' }] })!}
          configuredModels={configured}
          onChoose={vi.fn()}
          onClose={vi.fn()}
        />
      </Theme>,
    );
    expect(screen.getByText('Matched here: GPT X (fast).')).toBeTruthy();
  });

  it('AT-8: a preview row stacks tightly on a phone: the label has a width, never a flex basis', () => {
    render(
      <Theme>
        <PreviewRow label="Models" values={['GPT X']} />
      </Theme>,
    );
    const row = screen.getByTestId('import-preview-row');
    const label = within(row).getByText('Models');
    // A basis on a child of a column is its height: the 200px gap at 390 px.
    // The declared style, as React wrote it (jsdom's longhand getters invent a basis from flex-shrink).
    const declared = label.getAttribute('style') ?? '';
    expect(declared).not.toMatch(/flex-basis|flex:/);
    expect(declared).toContain('min-width: 96px');
    expect(row.className).toMatch(/rt-r-fd-column/);
    expect(row.className).toMatch(/sm:rt-r-fd-row/);
  });
});

describe('WP-56 agent gates in the import preview', () => {
  it('WP56-GATES-7: the preview lists the Safety and checks the new agent will run with', async () => {
    client.get.mockResolvedValue({ data: { models: [] } });
    openImport();
    chooseFile(
      fileOf({
        ...EXPORT,
        schemaVersion: 2,
        agent: {
          ...EXPORT.agent,
          gates: { planApproval: 'required', verify: { checks: [{ kind: 'command', command: 'pytest' }], onFail: 'flag' } },
        },
      }),
    );
    expect(await screen.findByText('Safety and checks')).toBeTruthy();
    expect(screen.getByText('Require plan approval, A command passes (tests)')).toBeTruthy();
  });
});
