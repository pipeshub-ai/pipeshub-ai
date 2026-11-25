/**
 * SLASH3: the Sandbox group of built-in /commands in the browser. The registry carries the group and the new
 * needs; the menu lists the group whole after Built-in; each command greys out with its own reason (a
 * participant: "Only the chat's owner can use the sandbox"); /files is a keyboard tree; /open never renders
 * workspace content as markup; /download saves a file or a zip; /logs opens the Activity drawer at Commands.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import i18n from 'i18next';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }) }));

const api = vi.hoisted(() => ({
  listWorkspaceFiles: vi.fn(),
  readWorkspaceFile: vi.fn(),
  downloadWorkspaceArchive: vi.fn(),
  getSessionSandbox: vi.fn(),
  endKeptSandbox: vi.fn(),
  getRunResolved: vi.fn(),
  getRunTimeline: vi.fn(),
  runSandboxCommand: vi.fn(),
  getCommandLog: vi.fn(),
  getWorkspaceDiff: vi.fn(),
  uploadWorkspaceFiles: vi.fn(),
  setSessionCodingEngine: vi.fn(),
  listWorkspaceVersions: vi.fn(),
}));
const engineView = vi.hoisted(() => ({
  view: {
    engines: [
      { kind: 'codex', available: true, reason: null },
      { kind: 'claude_code', available: true, reason: null },
      { kind: 'opencode', available: false, reason: 'engine_unavailable' },
    ],
    orgDefault: null,
    choice: 'org_default',
  },
}));
const putCodingEngine = vi.hoisted(() => vi.fn());
vi.mock('@/app/(main)/workspace/ai-accounts/api', () => ({
  ModelAccountsApi: {
    codingEngine: async () => engineView.view,
    putCodingEngine: (...a: unknown[]) => putCodingEngine(...a),
  },
}));
const followed = vi.hoisted(() => ({ calls: [] as unknown[][] }));
vi.mock('../streaming', () => ({
  followDurableRunInSlot: async (...a: unknown[]) => {
    followed.calls.push(a);
  },
}));
vi.mock('../api', () => ({
  ChatApi: {
    listWorkspaceFiles: (...a: unknown[]) => api.listWorkspaceFiles(...a),
    readWorkspaceFile: (...a: unknown[]) => api.readWorkspaceFile(...a),
    downloadWorkspaceArchive: (...a: unknown[]) => api.downloadWorkspaceArchive(...a),
    getSessionSandbox: (...a: unknown[]) => api.getSessionSandbox(...a),
    endKeptSandbox: (...a: unknown[]) => api.endKeptSandbox(...a),
    getRunResolved: (...a: unknown[]) => api.getRunResolved(...a),
    getRunTimeline: (...a: unknown[]) => api.getRunTimeline(...a),
    runSandboxCommand: (...a: unknown[]) => api.runSandboxCommand(...a),
    getCommandLog: (...a: unknown[]) => api.getCommandLog(...a),
    getWorkspaceDiff: (...a: unknown[]) => api.getWorkspaceDiff(...a),
    uploadWorkspaceFiles: (...a: unknown[]) => api.uploadWorkspaceFiles(...a),
    setSessionCodingEngine: (...a: unknown[]) => api.setSessionCodingEngine(...a),
    listWorkspaceVersions: (...a: unknown[]) => api.listWorkspaceVersions(...a),
  },
  statusOf: (e: { statusCode?: number }) => e?.statusCode,
}));
const saved = vi.hoisted(() => ({ downloads: [] as Array<{ name: string }> }));
vi.mock('@/app/components/file-preview/utils', () => ({
  downloadPreviewFile: (input: { name: string }) => saved.downloads.push({ name: input.name }),
}));

import { BUILTIN_COMMANDS } from '@/lib/contracts/harness/generated/ext/pipeshub/policy-data';
import { RESERVED_COMMAND_NAMES } from '@/lib/commands/render';
import { builtinReason, findBuiltin, SHIPPED_BUILTINS, type BuiltinCommand, type BuiltinContext } from '../builtin-commands';
import { composerCommandState, type MenuEntry } from '../commands';
import { runBuiltin, type BuiltinDeps } from '../builtin-actions';
import { useBuiltinResults, useBuiltinRows, useWorkspaceHeads, type BuiltinRow } from '../builtin-results';
import { builtinReasonText, SlashCommandMenu } from '../components/chat-panel/slash-command-menu';
import { BuiltinResultRows } from '../components/message-area/builtin-result-rows';
import { treeLines, FOLDER_MAX_SHOWN } from '../components/message-area/sandbox-result-rows';
import { csvRows, engineKinds, runArgs, runCodeOf, runHintKey, uploadRefusalKey, workspaceArg } from '../sandbox-commands';
import { askBlock, exitOf, RunCommandActions } from '../components/message-area/run-command-actions';
import { FILL_COMPOSER_COMMAND, useCommandStore } from '@/lib/store/command-store';
import { useActivityPanelStore } from '../components/activity-panel/activity-panel-store';
import type { WorkspaceFileList, WorkspaceFileRead } from '../types';
import { useChatStore } from '../store';

const t = i18n.t.bind(i18n) as unknown as BuiltinDeps['t'];
const SANDBOX = ['files', 'open', 'download', 'upload', 'run', 'sandbox', 'engine', 'repo', 'logs'];

const ALL: BuiltinContext = {
  running: false,
  conversation: true,
  answer: true,
  harness: true,
  forkable: true,
  reasoningModel: true,
  planStrategy: true,
  mcp: true,
  knowledge: true,
  memory: true,
  skills: true,
  workspace: true,
  agentChat: true,
  agentEditor: true,
  answers: 2,
  runCode: 'granted',
  engineChoice: 'choose',
};

/** Every registry command, shipped or not: a later package's command is checked before it ships. */
const command = (name: string): BuiltinCommand => {
  const c = BUILTIN_COMMANDS.commands.find((x) => x.name === name);
  if (!c) throw new Error(name);
  return c;
};

const LIST: WorkspaceFileList = {
  workspaceId: 'ws:conv-1',
  revision: 7,
  createdAt: Date.now() - 40_000,
  files: [
    { path: '.claude/settings.json', sizeBytes: 10, hidden: true },
    { path: 'analyse.py', sizeBytes: 2200 },
    { path: 'out/chart.png', sizeBytes: 1_300_000 },
    { path: 'out/summary.csv', sizeBytes: 3100 },
  ],
  totalBytes: 1_305_310,
  omitted: 0,
};

function deps(over: Partial<BuiltinDeps> = {}): BuiltinDeps {
  return {
    t,
    rowKey: 'slot-1',
    afterPairs: 1,
    slot: { convId: 'conv-1', threadAgentId: 'agent-1', messages: [] } as unknown as BuiltinDeps['slot'],
    agentId: 'agent-1',
    running: false,
    harness: true,
    isAdmin: false,
    models: [],
    currentModel: null,
    selectModel: vi.fn(),
    setEffort: vi.fn(),
    setStrategy: vi.fn(),
    modeLabel: null,
    openModelPanel: vi.fn(),
    openKnowledgePanel: vi.fn(),
    knowledgeSelection: [],
    stop: vi.fn(),
    navigate: vi.fn(),
    ...over,
  };
}

const rows = (): BuiltinRow[] => useBuiltinResults.getState().rows['slot-1'] ?? [];

function Rows() {
  const list = useBuiltinRows('slot-1');
  return (
    <Theme>
      <BuiltinResultRows rows={list} rowKey="slot-1" afterPairs={1} last />
    </Theme>
  );
}

const read = (body: string | Uint8Array, contentType: string, over: Partial<WorkspaceFileRead> = {}): WorkspaceFileRead => ({
  blob: new Blob([body], { type: contentType }),
  contentType,
  sizeBytes: typeof body === 'string' ? body.length : body.length,
  truncated: false,
  revision: 7,
  ...over,
});

beforeEach(() => {
  useBuiltinResults.setState({ rows: {}, pairs: {} });
  useActivityPanelStore.setState({ open: false, sheetOpen: false, run: null, focusSection: null });
  for (const fn of Object.values(api)) fn.mockReset();
  saved.downloads = [];
  followed.calls = [];
  useCommandStore.setState({ handlers: {}, pending: {} });
});

afterEach(() => cleanup());

describe('the registry (SLASH3 §3.9)', () => {
  it('holds the 9 Sandbox commands, /env an alias of /sandbox, and reserves secrets and clear-workspace', () => {
    const sandbox = BUILTIN_COMMANDS.commands.filter((c) => c.group === 'sandbox');
    expect(sandbox.map((c) => c.name)).toEqual(SANDBOX);
    expect(command('sandbox').aliases).toEqual(['env']);
    for (const c of sandbox) {
      // `/engine` and `/repo` work before the first message (CMDUI2, WP-26): the choice is held for it.
      const needs = c.name === 'engine' || c.name === 'repo' ? ['harness', 'owner'] : ['conversation', 'harness', 'owner'];
      expect(c.needs, c.name).toEqual(expect.arrayContaining(needs));
      expect(c.executors).toEqual(['any']);
      expect(typeof i18n.t(`${c.i18n}.description`)).toBe('string');
      expect(i18n.t(`${c.i18n}.description`)).not.toBe(`${c.i18n}.description`);
    }
    expect(command('run').needs).toContain('runCode');
    expect(command('engine').needs).toContain('engineChoice');
    for (const word of ['secrets', 'clear-workspace', 'env', 'files', 'run']) expect(RESERVED_COMMAND_NAMES.has(word), word).toBe(true);
  });

  it('every general built-in has no group or the general one', () => {
    for (const c of BUILTIN_COMMANDS.commands.filter((x) => !SANDBOX.includes(x.name))) {
      expect([undefined, 'general'], c.name).toContain(c.group);
    }
  });
});

describe('the menu: a Sandbox group after Built-in, before Your commands', () => {
  const menu = (text: string, ctx: BuiltinContext = ALL): MenuEntry[] => {
    const state = composerCommandState(text, [{ name: 'triage', description: 'Triage', version: '1', owner: 'you', arguments: [] }], {}, {
      reason: (c) => builtinReason(c, ctx),
    });
    return state.phase === 'menu' ? state.entries : [];
  };

  it('lists every shipped Sandbox command whole, outside the first page of 8', () => {
    const entries = menu('/');
    const groups = entries.map((e) => e.group);
    const firstSandbox = groups.indexOf('sandbox');
    expect(groups.slice(0, firstSandbox).every((g) => g === 'builtin')).toBe(true);
    expect(entries.filter((e) => e.group === 'builtin' && e.kind === 'builtin')).toHaveLength(8);
    const shipped = SHIPPED_BUILTINS.filter((c) => c.group === 'sandbox').map((c) => c.name);
    expect(entries.filter((e) => e.group === 'sandbox').map((e) => (e.kind === 'builtin' ? e.command.name : ''))).toEqual(shipped);
    expect(groups.at(-1)).toBe('you');
  });

  it('a query filters the Sandbox group too, and /env finds /sandbox when it ships', () => {
    expect(menu('/fil').map((e) => e.id)).toContain('builtin-files');
    expect(menu('/lo').map((e) => e.id)).toContain('builtin-logs');
  });

  it('renders the group with its heading, each row with aria-disabled and its reason when greyed', () => {
    const entries = menu('/', { ...ALL, notOwner: true });
    render(
      <Theme>
        <SlashCommandMenu open entries={entries} highlighted={0} onHighlight={vi.fn()} onSelect={vi.fn()} onDismiss={vi.fn()} inputRef={{ current: null }} />
      </Theme>,
    );
    expect(screen.getByText('Sandbox')).toBeTruthy();
    const files = screen.getByTestId('slash-command-option-builtin-files');
    expect(files.getAttribute('aria-disabled')).toBe('true');
    expect(files.textContent).toContain("Only the chat's owner can use the sandbox");
  });
});

describe('greyed out with a reason (O8)', () => {
  it('a participant of a shared chat: every Sandbox command, before any other reason', () => {
    for (const name of SANDBOX) {
      const reason = builtinReason(command(name), { ...ALL, notOwner: true, running: true, workspace: false });
      expect(reason, name).toBe('notOwner');
      expect(builtinReasonText(t, command(name), reason!)).toBe("Only the chat's owner can use the sandbox");
    }
    // /compact keeps its own words.
    expect(builtinReasonText(t, command('compact'), 'notOwner')).not.toBe("Only the chat's owner can use the sandbox");
  });

  it('a classic chat: "Sandbox commands need the new harness"', () => {
    for (const name of SANDBOX) {
      const reason = builtinReason(command(name), { ...ALL, harness: false });
      expect(reason, name).toBe('legacy');
      expect(builtinReasonText(t, command(name), reason!)).toBe('Sandbox commands need the new harness');
    }
  });

  it('/files /open /download need files; /upload /run wait while a reply runs; /engine is for the next message', () => {
    for (const name of ['files', 'open', 'download']) expect(builtinReason(command(name), { ...ALL, workspace: false }), name).toBe('noWorkspace');
    for (const name of ['upload', 'run']) {
      expect(builtinReason(command(name), { ...ALL, running: true }), name).toBe('wait');
      expect(builtinReasonText(t, command(name), 'wait')).toBe('Wait for this run to finish');
    }
    for (const name of ['files', 'open', 'download', 'sandbox', 'logs', 'engine']) {
      expect(builtinReason(command(name), { ...ALL, running: true }), name).toBeNull();
    }
    expect(builtinReason(command('upload'), { ...ALL, workspace: false })).toBeNull();
  });

  it('/run: no code tool, or a rule that blocks it', () => {
    expect(builtinReason(command('run'), { ...ALL, runCode: 'missing' })).toBe('noRunCode');
    expect(builtinReasonText(t, command('run'), 'noRunCode')).toBe("This chat can't run code");
    expect(builtinReason(command('run'), { ...ALL, runCode: 'denied' })).toBe('runCodeDenied');
    expect(builtinReasonText(t, command('run'), 'runCodeDenied')).toBe("Running code is blocked by your organization's policy");
    // Unknown until GET …/sandbox answers: offered, and the server is the backstop.
    expect(builtinReason(command('run'), { ...ALL, runCode: undefined })).toBeNull();
  });

  it('/engine: an agent that fixes its engine, and Quick chat', () => {
    expect(builtinReasonText(t, command('engine'), builtinReason(command('engine'), { ...ALL, engineChoice: 'fixed' })!)).toBe(
      "This agent's engine is set by its owner",
    );
    expect(builtinReasonText(t, command('engine'), builtinReason(command('engine'), { ...ALL, engineChoice: 'quickChat' })!)).toBe(
      'Coding engines run in Agent mode',
    );
  });
});

describe('/files', () => {
  it('lists the head as a tree with folder totals, the hidden tag, and a summary line', async () => {
    api.listWorkspaceFiles.mockResolvedValue(LIST);
    render(<Rows />);
    await act(async () => runBuiltin(findBuiltin('files')!, '', deps()));
    expect(api.listWorkspaceFiles).toHaveBeenCalledWith({ agentId: 'agent-1', sessionId: 'conv-1' }, undefined);
    const tree = await screen.findByTestId('sandbox-files-tree');
    expect(tree.getAttribute('role')).toBe('tree');
    expect(screen.getByText(/Version 7 · files: 4/)).toBeTruthy();
    expect(tree.textContent).toContain('hidden from engines');
    expect(tree.textContent).toContain('out/');
  });

  it('/files out/ asks for the folder; while a reply runs it says the list is as of the last save', async () => {
    api.listWorkspaceFiles.mockResolvedValue(LIST);
    render(<Rows />);
    await act(async () => runBuiltin(findBuiltin('files')!, './out/', deps({ running: true })));
    expect(api.listWorkspaceFiles).toHaveBeenCalledWith(expect.anything(), 'out');
    expect(await screen.findByText(/As of the last save/)).toBeTruthy();
  });

  it('arrow keys move through the tree and Enter on a file opens it with /open', async () => {
    api.listWorkspaceFiles.mockResolvedValue(LIST);
    api.readWorkspaceFile.mockResolvedValue(read('print(1)\n', 'text/plain; charset=utf-8'));
    render(<Rows />);
    await act(async () => runBuiltin(findBuiltin('files')!, '', deps()));
    const items = await screen.findAllByRole('treeitem');
    items[0].focus();
    fireEvent.keyDown(items[0], { key: 'ArrowDown' });
    expect(document.activeElement).toBe(items[1]);
    fireEvent.keyDown(items[1], { key: 'ArrowDown' });
    expect(document.activeElement?.textContent).toContain('analyse.py');
    fireEvent.keyDown(document.activeElement as Element, { key: 'Enter' });
    await waitFor(() => expect(api.readWorkspaceFile).toHaveBeenCalledWith(expect.anything(), 'analyse.py', { maxBytes: 256 * 1024 }));
    expect(rows().at(-1)?.typed).toBe('/open analyse.py');
  });

  it('a missing folder says so', async () => {
    api.listWorkspaceFiles.mockRejectedValue({ statusCode: 404 });
    await runBuiltin(findBuiltin('files')!, 'nope', deps());
    expect(rows()[0].result).toEqual({ kind: 'note', text: 'No file or folder at nope' });
  });

  it('a folder past 200 files collapses the rest to "+N more"', () => {
    const files = Array.from({ length: FOLDER_MAX_SHOWN + 5 }, (_, i) => ({ path: `big/f${String(i).padStart(4, '0')}`, sizeBytes: 1 }));
    const lines = treeLines(files);
    expect(lines[0]).toMatchObject({ folder: true, label: 'big/', size: FOLDER_MAX_SHOWN + 5 });
    expect(lines.filter((l) => l.path)).toHaveLength(FOLDER_MAX_SHOWN);
    expect(lines.at(-1)).toMatchObject({ label: '+5' });
  });
});

describe('/open', () => {
  it('shows HTML as text, never as markup', async () => {
    api.readWorkspaceFile.mockResolvedValue(read('<img src=x onerror="window.pwned=1"><b>bold</b>', 'text/plain; charset=utf-8'));
    render(<Rows />);
    await act(async () => runBuiltin(findBuiltin('open')!, 'page.html', deps()));
    const pre = await screen.findByTestId('sandbox-open-text');
    expect(pre.textContent).toContain('<img src=x onerror="window.pwned=1"><b>bold</b>');
    expect(pre.querySelector('img, b')).toBeNull();
  });

  it('an image asks for up to 10 MiB and shows it from a blob URL', async () => {
    const created = vi.fn(() => 'blob:x');
    URL.createObjectURL = created as unknown as typeof URL.createObjectURL;
    URL.revokeObjectURL = vi.fn();
    api.readWorkspaceFile.mockResolvedValue(read(new Uint8Array([0x89, 0x50]), 'image/png'));
    render(<Rows />);
    await act(async () => runBuiltin(findBuiltin('open')!, 'out/chart.png', deps()));
    expect(api.readWorkspaceFile).toHaveBeenCalledWith(expect.anything(), 'out/chart.png', { maxBytes: 10 * 1024 * 1024 });
    const img = await screen.findByRole('img');
    expect(img.getAttribute('src')).toBe('blob:x');
  });

  it('CSV shows its first rows as a table; binary says so with a download button', async () => {
    api.readWorkspaceFile.mockResolvedValueOnce(read('trip,cost\n"Paris, FR",12\n', 'text/plain; charset=utf-8'));
    render(<Rows />);
    await act(async () => runBuiltin(findBuiltin('open')!, 'out/summary.csv', deps()));
    expect(await screen.findByRole('columnheader', { name: 'trip' })).toBeTruthy();
    expect(screen.getByRole('cell', { name: 'Paris, FR' })).toBeTruthy();
    api.readWorkspaceFile.mockResolvedValueOnce(read(new Uint8Array([0, 1]), 'application/octet-stream', { sizeBytes: 2_100_000 }));
    await act(async () => runBuiltin(findBuiltin('open')!, 'model.bin', deps()));
    expect(await screen.findByText(/model.bin · binary file/)).toBeTruthy();
  });

  it('a missing file says so', async () => {
    api.readWorkspaceFile.mockResolvedValue({ refused: 404 });
    await runBuiltin(findBuiltin('open')!, 'gone.txt', deps());
    expect(rows()[0].result).toEqual({ kind: 'note', text: 'No file or folder at gone.txt' });
  });
});

describe('/download', () => {
  it('a file downloads as itself; a folder or nothing as a zip named for the chat and version', async () => {
    api.readWorkspaceFile.mockResolvedValue(read('x', 'text/plain'));
    await runBuiltin(findBuiltin('download')!, 'out/summary.csv', deps());
    expect(api.readWorkspaceFile).toHaveBeenCalledWith(expect.anything(), 'out/summary.csv', { download: true });
    expect(saved.downloads.at(-1)).toEqual({ name: 'summary.csv' });

    api.downloadWorkspaceArchive.mockResolvedValue({ blob: new Blob(['z']), revision: 7 });
    await runBuiltin(findBuiltin('download')!, 'out/', deps());
    expect(api.downloadWorkspaceArchive).toHaveBeenLastCalledWith(expect.anything(), 'out');
    expect(saved.downloads.at(-1)).toEqual({ name: 'out-v7.zip' });
    await runBuiltin(findBuiltin('download')!, '', deps());
    expect(api.downloadWorkspaceArchive).toHaveBeenLastCalledWith(expect.anything(), undefined);
    expect(saved.downloads.at(-1)?.name).toMatch(/-v7\.zip$/);
  });

  it('a path that is a folder, typed without a slash, is zipped; past 2 GiB it says to download a folder', async () => {
    api.readWorkspaceFile.mockResolvedValue({ refused: 404 });
    api.downloadWorkspaceArchive.mockResolvedValueOnce({ blob: new Blob(['z']), revision: 3 });
    await runBuiltin(findBuiltin('download')!, 'out', deps());
    expect(saved.downloads.at(-1)).toEqual({ name: 'out-v3.zip' });
    api.downloadWorkspaceArchive.mockResolvedValueOnce({ refused: 413 });
    await runBuiltin(findBuiltin('download')!, '', deps());
    expect(rows().at(-1)?.result).toMatchObject({ kind: 'note', text: expect.stringContaining('larger than 2 GiB') });
  });
});

describe('/sandbox (alias /env)', () => {
  const VIEW = {
    image: { name: 'pipeshub/sandbox:latest', isolation: 'container_gvisor' },
    network: false,
    codeRuns: 'allow' as const,
    workspace: { revision: 8, files: 7, bytes: 1_400_000 },
    keptSignIn: { idleMinutes: 30, maxHours: 12 },
  };
  const slot = { convId: 'conv-1', threadAgentId: 'agent-1', messages: [], lastDurableRun: { agentId: 'agent-1', sessionId: 'conv-1', runId: 'run-9' } };

  it('/env is /sandbox: the card names the executor, the image and isolation, network, approvals and the workspace', async () => {
    api.getSessionSandbox.mockResolvedValue({ ...VIEW, keptSignIn: null });
    api.getRunResolved.mockRejectedValue(new Error('no pin'));
    api.getRunTimeline.mockResolvedValue({ executor: 'claude_code' });
    render(<Rows />);
    await act(async () => runBuiltin(findBuiltin('env')!, '', deps({ slot: slot as unknown as BuiltinDeps['slot'] }), 'env'));
    const card = await screen.findByTestId('sandbox-card');
    // QAFIX3 W4: the card echoes what was typed, the alias, not the command it stands for.
    expect(rows()[0].typed).toBe('/env');
    const typedEnv = composerCommandState('/env ', [], {});
    expect(typedEnv).toMatchObject({ phase: 'builtin', command: { name: 'sandbox' }, typed: 'env' });
    expect(card.textContent).toContain('pipeshub/sandbox:latest · gVisor');
    expect(card.textContent).toContain('Code runs without asking (own sandbox)');
    expect(card.textContent).toContain('Version 8 · files: 7');
    expect(card.textContent).toContain('No sandbox is running. Each message starts a fresh one from your files.');
    expect(card.textContent).not.toContain('End now');
  });

  it('a kept sign-in sandbox: End now asks first, then revokes; the card says the next message signs in again', async () => {
    api.getSessionSandbox.mockResolvedValue({ ...VIEW, network: true, codeRuns: 'ask' });
    render(<Rows />);
    await act(async () => runBuiltin(findBuiltin('sandbox')!, '', deps()));
    expect(await screen.findByText(/ends after 30 min idle/)).toBeTruthy();
    expect(screen.getByText('Asks before running code')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'End now…' }));
    expect(api.endKeptSandbox).not.toHaveBeenCalled();
    api.endKeptSandbox.mockResolvedValue(undefined);
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'End now' })));
    expect(api.endKeptSandbox).toHaveBeenCalledWith({ agentId: 'agent-1', sessionId: 'conv-1' });
    expect(await screen.findByText(/The kept sandbox has ended/)).toBeTruthy();
    expect(screen.queryByText(/ends after 30 min idle/)).toBeNull();
  });
});

describe('/logs', () => {
  it('opens the Activity drawer at Commands for the last run', async () => {
    const slot = { convId: 'conv-1', threadAgentId: 'agent-1', messages: [], lastDurableRun: { agentId: 'agent-1', sessionId: 'conv-1', runId: 'run-9' } };
    await runBuiltin(findBuiltin('logs')!, '', deps({ slot: slot as unknown as BuiltinDeps['slot'] }));
    expect(useActivityPanelStore.getState()).toMatchObject({ open: true, run: { runId: 'run-9' }, focusSection: 'commands' });
    await runBuiltin(findBuiltin('logs')!, '', deps({ slot: slot as unknown as BuiltinDeps['slot'], isMobile: true }));
    expect(useActivityPanelStore.getState().sheetOpen).toBe(true);
  });

  it('with no run yet it says no commands have run', async () => {
    await runBuiltin(findBuiltin('logs')!, '', deps());
    expect(rows()[0].result).toEqual({ kind: 'note', text: 'No commands have run in this chat' });
  });
});

describe('helpers', () => {
  it('workspaceArg drops ./, a leading slash and a trailing slash', () => {
    expect(workspaceArg(' ./out/ ')).toBe('out');
    expect(workspaceArg('/a/b.txt')).toBe('a/b.txt');
  });

  it('csvRows reads quoted cells and doubled quotes, and stops at the limit', () => {
    expect(csvRows('a,"b,c","say ""hi"""\n1,2,3', 10)).toEqual({ rows: [['a', 'b,c', 'say "hi"'], ['1', '2', '3']], more: false });
    expect(csvRows('h\n1\n2\n3', 2)).toEqual({ rows: [['h'], ['1']], more: true });
  });
});

describe('/run (SB3b)', () => {
  it("runs the line on the chat's session with its next turn's mode and model, then follows the run as the chat's turn", async () => {
    api.runSandboxCommand.mockResolvedValue({ runId: 'run-7' });
    await runBuiltin(findBuiltin('run')!, '--timeout 120 pytest -q', deps({
      currentModel: { modelKey: 'm-1', modelName: 'gpt-x', modelFriendlyName: 'GPT X', modelProvider: 'openAI' },
    }));
    const [session, body] = api.runSandboxCommand.mock.calls[0] as [unknown, Record<string, unknown>];
    expect(session).toEqual({ agentId: 'agent-1', sessionId: 'conv-1' });
    expect(body).toMatchObject({ command: 'pytest -q', timeoutS: 120, modelKey: 'm-1', modelProvider: 'openAI' });
    expect(typeof body.chatMode).toBe('string');
    expect(String(body.idempotencyKey)).toMatch(/^slash-run-/);
    expect(followed.calls).toEqual([['slot-1', 'agent-1', 'conv-1', 'run-7', '/run pytest -q']]);
    expect(rows()).toEqual([]);
  });

  it('an interactive program or an install gets a hint and never runs', async () => {
    for (const line of ['vim notes.md', 'top', 'pip install pandas', 'sudo apt-get install jq']) {
      await runBuiltin(findBuiltin('run')!, line, deps());
    }
    expect(api.runSandboxCommand).not.toHaveBeenCalled();
    expect(rows().map((r) => (r.result.kind === 'note' ? r.result.text : ''))).toEqual([
      'No interactive programs: /run runs one command and returns.',
      'No interactive programs: /run runs one command and returns.',
      "Packages can't be installed with /run; ask the agent to install them.",
      "Packages can't be installed with /run; ask the agent to install them.",
    ]);
  });

  it('a refusal says why: a run in progress, the rate limit with its wait', async () => {
    api.runSandboxCommand.mockResolvedValueOnce({ refused: 'HTTP_AGENT_HARNESS_RUN_ACTIVE' });
    await runBuiltin(findBuiltin('run')!, 'ls', deps());
    api.runSandboxCommand.mockResolvedValueOnce({ refused: 'HTTP_429', retryAfterS: 12 });
    await runBuiltin(findBuiltin('run')!, 'ls', deps());
    expect(rows().map((r) => (r.result.kind === 'note' ? r.result.text : ''))).toEqual([
      'Wait for this run to finish',
      'Too many /run commands. Try again in 12 s.',
    ]);
    expect(followed.calls).toEqual([]);
  });

  it("parses --timeout within run_code's maximum; the grant greys /run outside an agent turn", () => {
    expect(runArgs('--timeout 9999 make')).toEqual({ line: 'make', timeoutS: 600 });
    expect(runArgs(' ls -la ')).toEqual({ line: 'ls -la' });
    expect(runHintKey('vimdiff a b')).toBeNull();
    expect(runCodeOf(false, undefined)).toBe('missing');
    expect(runCodeOf(true, { codeRuns: 'deny' } as never)).toBe('denied');
    expect(runCodeOf(true, null)).toBe('granted');
  });

  it('"Ask about this" attaches the output\'s end as a block for the next message; nothing is sent', async () => {
    expect(exitOf('Exit code 1.')).toBe('exit 1');
    expect(askBlock('pytest -q', 'Exit code 1.', 'x'.repeat(5000))).toMatch(/^Output of `\/run pytest -q` \(exit 1\):\n```\n…x+\n```\n\n$/);
    const fill = vi.fn();
    useCommandStore.getState().register(FILL_COMPOSER_COMMAND, fill);
    api.getCommandLog.mockResolvedValue('3 failed, 2 passed\n');
    api.listWorkspaceVersions.mockResolvedValue({ workspaceId: 'ws', head: 0, versions: [] });
    const run = { agentId: 'agent-1', sessionId: 'conv-1', runId: 'run-7' };
    render(<Theme><RunCommandActions question="/run pytest -q" answer="Exit code 1." run={run} /></Theme>);
    expect(screen.getByText('This output is not sent to the model unless you ask about it.')).toBeTruthy();
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Ask about this' })));
    expect(api.getCommandLog).toHaveBeenCalledWith(run, 't1.0');
    expect(fill).toHaveBeenCalledWith({ text: 'Output of `/run pytest -q` (exit 1):\n```\n3 failed, 2 passed\n```\n\n' });
    fireEvent.click(screen.getByRole('button', { name: 'View full output' }));
    expect(useActivityPanelStore.getState()).toMatchObject({ run: { runId: 'run-7' }, focusSection: 'commands' });
  });

  it('Undo shows the files the /run changed for a confirm (the /undo flow)', async () => {
    api.listWorkspaceVersions.mockResolvedValue({ workspaceId: 'ws', head: 4, versions: [{ revision: 4, runId: 'run-17' }] });
    api.getWorkspaceDiff.mockResolvedValue({ workspaceId: 'ws', head: 4, from: 3, to: 4, files: [{ path: 'out.txt', change: 'added', sizeBytes: 3, additions: 1, deletions: 0 }], additions: 1, deletions: 0, omitted: 0 });
    useChatStore.setState({ activeSlotId: 'slot-1' });
    render(<Theme><RunCommandActions question="/run echo hi > out.txt" answer="Exit code 0." run={{ sessionId: 'conv-1', runId: 'run-17' }} /></Theme>);
    const undo = await screen.findByRole('button', { name: 'Undo' });
    await act(async () => fireEvent.click(undo));
    expect(api.getWorkspaceDiff).toHaveBeenCalledWith({ sessionId: 'conv-1' }, 'turn');
    expect(rows().at(-1)?.result.kind).toBe('undoConfirm');
  });

  it('QAFIX3 W3: a /run that changed no files offers no Undo; a refusal (not the owner) offers none either', async () => {
    api.listWorkspaceVersions.mockResolvedValue({ workspaceId: 'ws', head: 4, versions: [{ revision: 4, runId: 'run-other' }, { revision: 3 }] });
    render(<Theme><RunCommandActions question="/run ls" answer="Exit code 0." run={{ sessionId: 'conv-1', runId: 'run-8' }} /></Theme>);
    await waitFor(() => expect(api.listWorkspaceVersions).toHaveBeenCalledWith({ sessionId: 'conv-1' }, { limit: 50 }));
    await act(async () => undefined);
    expect(screen.getByRole('button', { name: 'Ask about this' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Undo' })).toBeNull();
    cleanup();
    api.listWorkspaceVersions.mockRejectedValue(new Error('404'));
    render(<Theme><RunCommandActions question="/run ls" answer="Exit code 0." run={{ sessionId: 'conv-2', runId: 'run-9' }} /></Theme>);
    await act(async () => undefined);
    expect(screen.queryByRole('button', { name: 'Undo' })).toBeNull();
  });

  it('shows nothing under an answer that is not a /run', () => {
    render(<Theme><RunCommandActions question="what changed?" answer="ok" run={{ sessionId: 'c', runId: 'r' }} /></Theme>);
    expect(screen.queryByTestId('run-command-actions')).toBeNull();
  });
});

describe('/upload (SB2)', () => {
  const picked = (...names: string[]) => async () => names.map((name) => new File(['x'], name, { type: 'text/plain' }));

  it('puts the picked files under uploads/ as one version; the row lists them, says they are not an attachment, offers Undo', async () => {
    api.listWorkspaceFiles.mockResolvedValue({ ...LIST, revision: 4 });
    api.uploadWorkspaceFiles.mockResolvedValue({ revision: 5, paths: ['uploads/data.csv'], replaced: [] });
    render(<Rows />);
    await act(async () => runBuiltin(findBuiltin('upload')!, '', deps({ pickFiles: picked('data.csv') })));
    const [, request] = api.uploadWorkspaceFiles.mock.calls[0] as [unknown, Record<string, unknown>];
    expect(request).toMatchObject({ folder: 'uploads', baseRevision: 4, replace: false });
    expect(String(request.idempotencyKey)).toMatch(/^slash-upload-/);
    expect(rows().map((r) => r.id)).toEqual(['upload-revision-5']);
    // The menu's "no workspace yet" lifts at once after a chat's first upload, not when the head's cache expires.
    expect(useWorkspaceHeads.getState().heads['conv-1']?.head).toBe(5);
    const row = await screen.findByTestId('sandbox-uploaded');
    expect(row.textContent).toContain('version 5');
    expect(row.textContent).toContain('uploads/data.csv');
    expect(row.textContent).toContain('not attached to your next message');
    expect(screen.getByRole('button', { name: 'Undo' })).toBeTruthy();
  });

  it('a file that would replace one asks "Replace files: N?" first; Replace sends it with the confirm on the same head', async () => {
    api.listWorkspaceFiles.mockResolvedValue({ ...LIST, revision: 7, files: [{ path: 'in/out.txt', sizeBytes: 1 }] });
    api.uploadWorkspaceFiles.mockResolvedValue({ revision: 8, paths: ['in/out.txt'], replaced: ['in/out.txt'] });
    render(<Rows />);
    await act(async () => runBuiltin(findBuiltin('upload')!, 'in/', deps({ pickFiles: picked('out.txt') })));
    expect(api.uploadWorkspaceFiles).not.toHaveBeenCalled();
    expect(await screen.findByText('Replace files: 1?')).toBeTruthy();
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Replace' })));
    expect(api.uploadWorkspaceFiles.mock.calls[0]?.[1]).toMatchObject({ folder: 'in', baseRevision: 7, replace: true });
    expect(rows().map((r) => r.id)).toEqual(['upload-revision-8']);
  });

  it('QAFIX3: a failed upload says the files were not added, not "Could not load this"', async () => {
    api.listWorkspaceFiles.mockResolvedValue({ ...LIST, revision: 1, files: [] });
    api.uploadWorkspaceFiles.mockRejectedValue(new Error('network'));
    render(<Rows />);
    await act(async () => runBuiltin(findBuiltin('upload')!, '', deps({ pickFiles: picked('a.txt') })));
    expect(await screen.findByText('Could not add the files to the workspace. Try again.')).toBeTruthy();
    expect(screen.queryByText('Could not load this. Try again.')).toBeNull();
    // A refusal the API names no reason for (QA's "Invalid upload fields" 400) reads the same way.
    expect(uploadRefusalKey('HTTP_AGENT_HARNESS_INVALID_REQUEST')).toBe('slashCommands.result.upload.failed');
  });

  it('a closed picker does nothing; a busy writer says to wait', async () => {
    await runBuiltin(findBuiltin('upload')!, '', deps({ pickFiles: async () => [] }));
    expect(rows()).toEqual([]);
    api.listWorkspaceFiles.mockResolvedValue({ ...LIST, revision: 1, files: [] });
    api.uploadWorkspaceFiles.mockResolvedValue({ refused: 'HTTP_AGENT_HARNESS_WORKSPACE_WRITER_BUSY' });
    render(<Rows />);
    await act(async () => runBuiltin(findBuiltin('upload')!, '', deps({ pickFiles: picked('a.txt') })));
    expect(await screen.findByText('Wait for this run to finish')).toBeTruthy();
  });
});

describe('/engine (SB4)', () => {
  const SANDBOX = {
    image: { name: 'pipeshub/sandbox:latest', isolation: null },
    network: false,
    codeRuns: 'allow',
    workspace: { revision: 0, files: 0, bytes: 0 },
    keptSignIn: null,
    codingEngine: 'codex',
  };

  beforeEach(() => {
    useChatStore.setState({ agentEngines: [] });
    api.getSessionSandbox.mockResolvedValue(SANDBOX);
  });

  it('shows the picker for this chat: its choice checked, an engine the org cannot use disabled with its reason', async () => {
    render(<Rows />);
    await act(async () => runBuiltin(findBuiltin('engine')!, '', deps()));
    const picker = await screen.findByTestId('sandbox-engine');
    expect(within(picker).getByRole('radiogroup', { name: 'Coding engine for this chat' })).toBeTruthy();
    expect((screen.getByRole('radio', { name: 'Codex' }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole('radio', { name: /opencode/i }) as HTMLInputElement).disabled).toBe(true);
    api.setSessionCodingEngine.mockResolvedValue({ engine: 'none' });
    await act(async () => fireEvent.click(screen.getByRole('radio', { name: 'None – built-in agent loop' })));
    expect(api.setSessionCodingEngine).toHaveBeenCalledWith({ agentId: 'agent-1', sessionId: 'conv-1' }, 'none');
    expect(await screen.findByText(/Saved. Your next message uses it/)).toBeTruthy();
  });

  it('/engine claude_code saves it at once; a refused engine says it was not saved', async () => {
    api.setSessionCodingEngine.mockResolvedValueOnce({ engine: 'claude_code' });
    await runBuiltin(findBuiltin('engine')!, 'claude_code', deps());
    expect(api.setSessionCodingEngine).toHaveBeenLastCalledWith(expect.anything(), 'claude_code');
    expect(rows().at(-1)?.result).toMatchObject({ kind: 'engine', current: 'claude_code', saved: true });
    api.setSessionCodingEngine.mockResolvedValueOnce({ refused: 'HTTP_AGENT_HARNESS_ENGINE_UNAVAILABLE' });
    await runBuiltin(findBuiltin('engine')!, 'opencode', deps());
    expect(rows().at(-1)?.result).toMatchObject({ kind: 'engine', current: 'codex', refused: 'HTTP_AGENT_HARNESS_ENGINE_UNAVAILABLE' });
  });

  it("in an agent's chat it offers only the agent's engines and never None", async () => {
    useChatStore.setState({ agentEngines: ['codex', 'claude_code'] });
    await runBuiltin(findBuiltin('engine')!, '', deps());
    const result = rows().at(-1)?.result as { options: Array<{ kind: string }>; none: boolean };
    expect(result.options.map((o) => o.kind)).toEqual(['codex', 'claude_code']);
    expect(result.none).toBe(false);
    expect(engineKinds({ engines: ['codex@0.160.0', 'codex', 'opencode'] })).toEqual(['codex', 'opencode']);
    expect(engineKinds(null)).toEqual([]);
  });
});

describe('CMDUI: /engine as the person names it, and their default', () => {
  const SANDBOX = {
    image: { name: 'pipeshub/sandbox:latest', isolation: null },
    network: false,
    codeRuns: 'allow',
    workspace: { revision: 0, files: 0, bytes: 0 },
    keptSignIn: null,
    codingEngine: null as string | null,
  };
  const VIEW = engineView.view;

  beforeEach(() => {
    useChatStore.setState({ agentEngines: [] });
    api.getSessionSandbox.mockResolvedValue({ ...SANDBOX });
    api.setSessionCodingEngine.mockImplementation(async (_s: unknown, engine: string | null) => ({ engine }));
    putCodingEngine.mockReset();
    engineView.view = { ...VIEW, engines: VIEW.engines.map((e) => ({ ...e, available: true, reason: null })), choice: 'org_default', orgDefault: null };
  });
  afterEach(() => {
    engineView.view = VIEW;
  });

  it('takes the name the picker shows, any case, or a unique start of it', async () => {
    for (const [typed, saved] of [
      ['Claude Code', 'claude_code'],
      ['claude', 'claude_code'],
      ['CODEX', 'codex'],
      ['open code', 'opencode'],
      ['None', 'none'],
      ['default', null],
    ] as const) {
      await runBuiltin(findBuiltin('engine')!, typed, deps({ agentId: null, slot: { convId: 'conv-1', messages: [] } as unknown as BuiltinDeps['slot'] }));
      expect(api.setSessionCodingEngine, typed).toHaveBeenLastCalledWith({ sessionId: 'conv-1' }, saved);
      expect(rows().at(-1)?.result, typed).toMatchObject({ kind: 'engine', current: saved, saved: true });
    }
  });

  it('saves nothing for a name it does not know, and says which engines there are', async () => {
    render(<Rows />);
    await act(async () => runBuiltin(findBuiltin('engine')!, 'gemini', deps()));
    expect(api.setSessionCodingEngine).not.toHaveBeenCalled();
    expect(rows().at(-1)?.result).toMatchObject({ kind: 'engine', unknown: 'gemini' });
    expect(await screen.findByText('No coding engine called “gemini”. Choose one below.')).toBeTruthy();
  });

  it("in an agent's chat, saves only an engine its owner listed: another is not saved, and None is not offered", async () => {
    useChatStore.setState({ agentEngines: ['codex', 'claude_code'] });
    await runBuiltin(findBuiltin('engine')!, 'opencode', deps());
    await runBuiltin(findBuiltin('engine')!, 'none', deps());
    expect(api.setSessionCodingEngine).not.toHaveBeenCalled();
    expect(rows().map((r) => (r.result as { unknown?: string }).unknown)).toEqual(['opencode', 'none']);
    await runBuiltin(findBuiltin('engine')!, 'claude code', deps());
    expect(api.setSessionCodingEngine).toHaveBeenLastCalledWith({ agentId: 'agent-1', sessionId: 'conv-1' }, 'claude_code');
  });

  it('says what "As my default" is now: the person\'s own choice, else the org\'s', async () => {
    engineView.view = { ...engineView.view, choice: 'codex', orgDefault: 'opencode' };
    render(<Rows />);
    await act(async () => runBuiltin(findBuiltin('engine')!, '', deps()));
    expect(await screen.findByRole('radio', { name: 'As my default · Codex' })).toBeTruthy();
    cleanup();
    useBuiltinResults.setState({ rows: {} });
    engineView.view = { ...engineView.view, choice: 'org_default', orgDefault: null };
    render(<Rows />);
    await act(async () => runBuiltin(findBuiltin('engine')!, '', deps()));
    expect(await screen.findByRole('radio', { name: 'As my default · None' })).toBeTruthy();
  });

  it('"Make this my default" saves the chat\'s engine as the person\'s default, and says so', async () => {
    api.getSessionSandbox.mockResolvedValue({ ...SANDBOX, codingEngine: 'claude_code' });
    putCodingEngine.mockResolvedValue({ ...engineView.view, choice: 'claude_code' });
    render(<Rows />);
    await act(async () => runBuiltin(findBuiltin('engine')!, '', deps()));
    await act(async () => fireEvent.click(await screen.findByRole('button', { name: 'Make this my default' })));
    expect(putCodingEngine).toHaveBeenCalledWith('claude_code');
    expect(await screen.findByText('Saved as your default for new chats.')).toBeTruthy();
    expect(screen.getByRole('radio', { name: 'As my default · Claude Code' })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'My AI accounts' }).getAttribute('href')).toBe('/workspace/ai-accounts');
  });

  it('"Make this my default" that the server refuses says it was not saved', async () => {
    api.getSessionSandbox.mockResolvedValue({ ...SANDBOX, codingEngine: 'codex' });
    putCodingEngine.mockRejectedValue({ kind: 'invalid', status: 400, message: 'Not allowed' });
    render(<Rows />);
    await act(async () => runBuiltin(findBuiltin('engine')!, '', deps()));
    await act(async () => fireEvent.click(await screen.findByRole('button', { name: 'Make this my default' })));
    expect(await screen.findByText('Your default was not changed. Try again from My AI accounts.')).toBeTruthy();
  });

  it('a chat that follows the default offers no "Make this my default", only the way to change it', async () => {
    render(<Rows />);
    await act(async () => runBuiltin(findBuiltin('engine')!, '', deps()));
    await screen.findByTestId('sandbox-engine');
    expect(screen.queryByRole('button', { name: 'Make this my default' })).toBeNull();
    expect(screen.getByRole('link', { name: 'My AI accounts' }).getAttribute('href')).toBe('/workspace/ai-accounts');
  });
});
