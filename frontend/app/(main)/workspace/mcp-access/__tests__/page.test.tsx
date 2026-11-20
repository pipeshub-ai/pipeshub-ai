import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import en from '@/lib/i18n/locales/en-US.json';

const strings = en.workspace.mcpAccess;

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: vi.fn() }),
}));

vi.mock('@/lib/store/user-store', () => ({
  useUserStore: (selector: (s: { isAdmin: boolean; isProfileInitialized: boolean }) => unknown) =>
    selector({ isAdmin: true, isProfileInitialized: true }),
  selectIsAdmin: (s: { isAdmin: boolean }) => s.isAdmin,
  selectIsProfileInitialized: (s: { isProfileInitialized: boolean }) => s.isProfileInitialized,
}));

vi.mock('@/lib/store/toast-store', () => ({
  useToastStore: (selector: (s: { addToast: () => void }) => unknown) => selector({ addToast: vi.fn() }),
}));

vi.mock('@/app/components/ui/lottie-loader', () => ({
  LottieLoader: () => <div>loading</div>,
}));

vi.mock('../../components', () => ({
  SettingsSaveBar: ({ visible, onSave }: { visible: boolean; onSave: () => void }) =>
    visible ? <button onClick={onSave}>save</button> : null,
  SettingsSection: ({ children, title }: { children: React.ReactNode; title?: string }) => (
    <div>
      {title}
      {children}
    </div>
  ),
}));

vi.mock('react-i18next', async () => {
  const { default: locale } = await import('@/lib/i18n/locales/en-US.json');
  return {
    useTranslation: () => ({
      t: (key: string) => {
        let cur: unknown = locale;
        for (const part of key.split('.')) {
          if (typeof cur !== 'object' || cur === null || !(part in cur)) return key;
          cur = (cur as Record<string, unknown>)[part];
        }
        return typeof cur === 'string' ? cur : key;
      },
    }),
    initReactI18next: { type: '3rdParty', init: () => undefined },
  };
});

vi.mock('../api', () => ({
  McpAccessApi: { getPolicy: vi.fn(), getTools: vi.fn(), savePolicy: vi.fn() },
}));

import McpAccessPage from '../page';
import { McpAccessApi } from '../api';

beforeEach(() => {
  vi.mocked(McpAccessApi.getPolicy).mockResolvedValue({ enabled: true, allowWriteTools: true, disabledTools: [] });
  vi.mocked(McpAccessApi.getTools).mockResolvedValue([
    { name: 'pipeshub_search', title: 'Semantic search', writes: false },
    { name: 'pipeshub_upload', title: 'Upload documents', writes: true },
  ]);
  vi.mocked(McpAccessApi.savePolicy).mockImplementation(async (policy) => policy);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const renderPage = () =>
  render(
    <Theme>
      <McpAccessPage />
    </Theme>,
  );

describe('McpAccessPage', () => {
  it('shows the access switches and one switch per tool, all on by default', async () => {
    renderPage();
    const access = await screen.findByRole('switch', { name: strings.enabled.label });
    expect(access.getAttribute('aria-checked')).toBe('true');
    expect(screen.getByRole('switch', { name: strings.writeTools.label }).getAttribute('aria-checked')).toBe('true');
    expect(screen.getByRole('switch', { name: 'Semantic search' }).getAttribute('aria-checked')).toBe('true');
    expect(screen.getByRole('switch', { name: 'Upload documents' }).getAttribute('aria-checked')).toBe('true');
    expect(screen.getByText(strings.tools.writes)).toBeTruthy();
    expect(screen.queryByText('save')).toBeNull();
  });

  it('explains what each tool does, next to its name', async () => {
    renderPage();
    expect(await screen.findByText(strings.tools.descriptions.pipeshub_search)).toBeTruthy();
    expect(screen.getByText(strings.tools.descriptions.pipeshub_upload)).toBeTruthy();
    expect(screen.getByText('pipeshub_search')).toBeTruthy();
  });

  it('explains the entity tools PipesHub adds beside the package', async () => {
    vi.mocked(McpAccessApi.getTools).mockResolvedValue([
      { name: 'pipeshub_entities', title: 'Entities', writes: false },
      { name: 'pipeshub_find_records', title: 'Find records', writes: false },
    ]);
    renderPage();
    expect(await screen.findByText(strings.tools.descriptions.pipeshub_entities)).toBeTruthy();
    expect(screen.getByText(strings.tools.descriptions.pipeshub_find_records)).toBeTruthy();
  });

  it('describes every tool the /mcp endpoint can list, in every language it ships', () => {
    // The package's tools (pinned @pipeshub-ai/mcp), then the code-graph and entity tools
    // this app registers (backend/nodejs/apps/src/modules/mcp). A tool missing here shows
    // admins a bare name: add its description to every locale when the server adds a tool.
    const served = [
      'pipeshub_sources', 'pipeshub_search', 'pipeshub_get_record_content', 'pipeshub_download_record',
      'pipeshub_directory', 'pipeshub_create_collection', 'pipeshub_upload', 'pipeshub_indexing_status',
      'pipeshub_connect_source', 'pipeshub_add_web_source', 'pipeshub_chat', 'pipeshub_agents',
      'pipeshub_show_citations', 'pipeshub_preview_record',
      'pipeshub_code_repos', 'pipeshub_code_symbols', 'pipeshub_code_neighbours', 'pipeshub_code_read',
      'pipeshub_code_path', 'pipeshub_code_search', 'pipeshub_code_semantic_search', 'pipeshub_code_outline',
      'pipeshub_code_impact', 'pipeshub_code_impact_of_diff', 'pipeshub_code_history', 'pipeshub_code_entry_points',
      'pipeshub_entities', 'pipeshub_find_records',
    ];
    const locales = import.meta.glob<{ workspace: { mcpAccess: { tools: { descriptions: Record<string, string> } } } }>(
      '../../../../../lib/i18n/locales/*.json',
      { eager: true, import: 'default' },
    );
    expect(Object.keys(locales).length).toBeGreaterThan(1);
    for (const [file, catalogue] of Object.entries(locales)) {
      const described = catalogue.workspace.mcpAccess.tools.descriptions;
      const missing = served.filter((name) => !described[name]?.trim());
      expect(missing, file).toEqual([]);
    }
  });

  it('shows a tool this build has no explanation for by its name alone', async () => {
    vi.mocked(McpAccessApi.getTools).mockResolvedValue([
      { name: 'pipeshub_future', title: 'pipeshub_future', writes: false },
    ]);
    renderPage();
    expect(await screen.findByRole('switch', { name: 'pipeshub_future' })).toBeTruthy();
    expect(screen.getAllByText('pipeshub_future')).toHaveLength(1);
    expect(screen.queryByText(/workspace\.mcpAccess/)).toBeNull();
  });

  it('saves a tool turned off', async () => {
    renderPage();
    fireEvent.click(await screen.findByRole('switch', { name: 'Semantic search' }));
    fireEvent.click(screen.getByText('save'));
    await waitFor(() => {
      expect(McpAccessApi.savePolicy).toHaveBeenCalledWith({
        enabled: true,
        allowWriteTools: true,
        disabledTools: ['pipeshub_search'],
      });
    });
  });

  it('shows write tools as off and locked while write tools are not allowed', async () => {
    renderPage();
    fireEvent.click(await screen.findByRole('switch', { name: strings.writeTools.label }));
    const upload = screen.getByRole('switch', { name: 'Upload documents' });
    expect(upload.getAttribute('aria-checked')).toBe('false');
    expect(upload.hasAttribute('disabled')).toBe(true);
    expect(screen.getByRole('switch', { name: 'Semantic search' }).hasAttribute('disabled')).toBe(false);
  });

  it('says so when the settings cannot be loaded', async () => {
    vi.mocked(McpAccessApi.getPolicy).mockRejectedValue(new Error('403'));
    renderPage();
    expect(await screen.findByText(strings.loadError)).toBeTruthy();
  });
});

describe('McpAccessPage, with what the server tells about each tool', () => {
  const hints = (readOnly: boolean, extra: Partial<{ destructive: boolean; openWorld: boolean }> = {}) => ({
    readOnly, destructive: false, idempotent: readOnly, openWorld: false, ...extra,
  });
  const TOOLS = [
    {
      name: 'pipeshub_search', title: 'Semantic search', writes: false,
      description: 'Vector / semantic search across the org.',
      arguments: [
        { name: 'query', type: 'string', required: true, description: 'Natural language query.' },
        { name: 'mode', type: 'enum', required: false, values: ['fast', 'deep'] },
      ],
      hints: hints(true, { openWorld: true }), scopes: ['semantic:write'],
    },
    {
      name: 'pipeshub_upload', title: 'Upload documents', writes: true,
      description: 'Upload documents into a collection.',
      arguments: [{ name: 'collectionId', type: 'string', required: true }],
      hints: hints(false), scopes: ['kb:upload'],
    },
    {
      name: 'pipeshub_create_collection', title: 'Create collection', writes: true,
      description: 'Create an empty collection.', arguments: [], hints: hints(false), scopes: ['kb:write'],
    },
    {
      name: 'pipeshub_chat', title: 'Chat with PipesHub', writes: false,
      description: 'Ask the assistant.', arguments: [], hints: hints(false, { destructive: true }), scopes: ['conversation:chat'],
    },
    {
      name: 'pipeshub_connect_source', title: 'Connect a source', writes: false,
      description: 'Link to connect a source.', arguments: [], hints: hints(true), scopes: [],
    },
    {
      name: 'pipeshub_code_read', title: 'Read code', writes: false,
      description: 'Read indexed source code.', arguments: [{ name: 'repo', type: 'string', required: true }],
      hints: hints(true), scopes: ['code:read'],
    },
    { name: 'pipeshub_future', title: 'pipeshub_future', writes: false },
  ];

  beforeEach(() => {
    vi.mocked(McpAccessApi.getTools).mockResolvedValue(TOOLS);
  });

  const groupLabel = (group: keyof typeof strings.tools.groups) => strings.tools.groups[group].label;
  // Radix renders a tab's label twice (a hidden copy keeps its width), so its name is matched by its text.
  const tab = (label: string) => screen.getAllByRole('tab').find((el) => el.textContent?.startsWith(label))!;
  const groupSwitches = () => screen.getAllByRole('switch', { name: strings.tools.groupSwitch });

  it('groups the tools, and labels what each can do to data', async () => {
    renderPage();
    expect(await screen.findByText(groupLabel('read'))).toBeTruthy();
    for (const group of ['add', 'chat', 'code', 'other'] as const) expect(screen.getByText(groupLabel(group))).toBeTruthy();
    expect(screen.queryByText(groupLabel('people')), 'a group with no tool is not shown').toBeNull();
    expect(screen.getAllByText(strings.tools.kinds.readOnly).length).toBeGreaterThan(0);
    expect(screen.getAllByText(strings.tools.writes)).toHaveLength(2);
    expect(screen.getByText(strings.tools.kinds.changes)).toBeTruthy();
    expect(screen.getByText(strings.tools.kinds.openWorld)).toBeTruthy();
    expect(screen.getByText(strings.status.on)).toBeTruthy();
  });

  it('shows the endpoint and how each client connects to it', async () => {
    renderPage();
    const endpoint = (await screen.findByLabelText(strings.connect.endpointLabel)) as HTMLInputElement;
    expect(endpoint.value).toBe(`${window.location.origin}/mcp`);
    expect(screen.getByText(strings.connect.clients.claude.steps)).toBeTruthy();
    fireEvent.mouseDown(tab(strings.connect.clients.cursor.label));
    expect(await screen.findByText(strings.connect.clients.cursor.steps)).toBeTruthy();
    expect(screen.getByText(/"mcpServers"/).textContent).toContain(`${window.location.origin}/mcp`);
    fireEvent.mouseDown(tab(strings.connect.clients.claudeCode.label));
    expect(await screen.findByText(`claude mcp add --transport http pipeshub ${window.location.origin}/mcp`)).toBeTruthy();
    expect(screen.getByText(strings.connect.clients.claudeCode.tip)).toBeTruthy();
  });

  it('opens a tool to show its scopes, when it is offered, its arguments and what the AI reads', async () => {
    renderPage();
    const buttons = await screen.findAllByRole('button', { name: strings.tools.showDetails });
    fireEvent.click(buttons[0]);
    expect(screen.getByText('semantic:write')).toBeTruthy();
    expect(screen.getByText(strings.tools.details.scopes)).toBeTruthy();
    expect(screen.getByText(strings.tools.availability.default)).toBeTruthy();
    expect(screen.getByText('query')).toBeTruthy();
    expect(screen.getByText(strings.tools.details.required)).toBeTruthy();
    expect(screen.getByText('Natural language query.')).toBeTruthy();
    expect(screen.getByText(strings.tools.details.oneOf)).toBeTruthy();
    expect(screen.queryByText('Vector / semantic search across the org.')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: strings.tools.details.modelText }));
    expect(screen.getByText('Vector / semantic search across the org.')).toBeTruthy();
  });

  it('says a tool that calls nothing needs no scope, and when a write tool is offered', async () => {
    renderPage();
    await screen.findByText(groupLabel('add'));
    for (const button of screen.getAllByRole('button', { name: strings.tools.showDetails })) fireEvent.click(button);
    expect(screen.getByText(strings.tools.details.noScopes)).toBeTruthy();
    expect(screen.getAllByText(strings.tools.availability.pipeshub_upload).length).toBeGreaterThan(0);
    expect(screen.getByText(strings.tools.groups.code.availability)).toBeTruthy();
    expect(screen.getAllByText(strings.tools.details.noArguments).length).toBeGreaterThan(0);
  });

  it('offers no details for a tool from a server older than this page', async () => {
    renderPage();
    await screen.findByText(groupLabel('other'));
    expect(screen.getAllByRole('button', { name: strings.tools.showDetails })).toHaveLength(TOOLS.length - 1);
    expect(screen.getByRole('switch', { name: 'pipeshub_future' })).toBeTruthy();
  });

  it('finds tools by name, argument or scope, and says when none match', async () => {
    renderPage();
    const search = await screen.findByLabelText(strings.tools.searchLabel);
    fireEvent.change(search, { target: { value: 'collectionId' } });
    expect(screen.getByRole('switch', { name: 'Upload documents' })).toBeTruthy();
    expect(screen.queryByRole('switch', { name: 'Semantic search' })).toBeNull();
    fireEvent.change(search, { target: { value: 'code:read' } });
    expect(screen.getByRole('switch', { name: 'Read code' })).toBeTruthy();
    expect(screen.queryByRole('switch', { name: 'Upload documents' })).toBeNull();
    fireEvent.change(search, { target: { value: 'nothing-like-this' } });
    expect(screen.getByText(strings.tools.noMatch)).toBeTruthy();
  });

  it('filters to the tools that write or change data, and to the ones that are off', async () => {
    vi.mocked(McpAccessApi.getPolicy).mockResolvedValue({ enabled: true, allowWriteTools: true, disabledTools: ['pipeshub_code_read'] });
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: strings.tools.filters.writes }));
    expect(screen.getByRole('button', { name: strings.tools.filters.writes }).getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByRole('switch', { name: 'Upload documents' })).toBeTruthy();
    expect(screen.getByRole('switch', { name: 'Chat with PipesHub' })).toBeTruthy();
    expect(screen.queryByRole('switch', { name: 'Semantic search' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: strings.tools.filters.off }));
    expect(screen.getByRole('switch', { name: 'Read code' })).toBeTruthy();
    expect(screen.queryByRole('switch', { name: 'Upload documents' })).toBeNull();
  });

  it('folds a group while filtering, and opens it again on the next filter', async () => {
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: strings.tools.filters.writes }));
    const addHeader = () =>
      screen.getAllByRole('button').find((b) => b.getAttribute('aria-controls') === 'mcp-tool-group-add');
    fireEvent.click(addHeader());
    expect(addHeader().getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByRole('switch', { name: 'Upload documents' })).toBeNull();
    fireEvent.click(addHeader());
    expect(screen.getByRole('switch', { name: 'Upload documents' })).toBeTruthy();
    fireEvent.click(addHeader());
    fireEvent.click(screen.getByRole('button', { name: strings.tools.filters.all }));
    expect(addHeader().getAttribute('aria-expanded')).toBe('true');
  });

  it('turns a whole group off and saves it', async () => {
    renderPage();
    await screen.findByText(groupLabel('read'));
    // Groups in page order: read, chat, add, code, other.
    fireEvent.click(groupSwitches()[0]);
    expect(screen.getByRole('switch', { name: 'Semantic search' }).getAttribute('aria-checked')).toBe('false');
    fireEvent.click(screen.getByText('save'));
    await waitFor(() =>
      expect(McpAccessApi.savePolicy).toHaveBeenCalledWith({ enabled: true, allowWriteTools: true, disabledTools: ['pipeshub_search'] }),
    );
  });

  it('leaves out of a group switch the tools another setting holds off', async () => {
    renderPage();
    fireEvent.click(await screen.findByRole('switch', { name: strings.writeTools.label }));
    fireEvent.click(groupSwitches()[2]);
    fireEvent.click(screen.getByText('save'));
    await waitFor(() =>
      expect(McpAccessApi.savePolicy).toHaveBeenCalledWith({ enabled: true, allowWriteTools: false, disabledTools: ['pipeshub_connect_source'] }),
    );
  });

  it('says why a write tool is off while write tools are not allowed, but not under every tool while MCP is off', async () => {
    renderPage();
    fireEvent.click(await screen.findByRole('switch', { name: strings.writeTools.label }));
    expect(screen.getAllByText(strings.tools.blocked.writesOff)).toHaveLength(2);
    fireEvent.click(screen.getByRole('switch', { name: strings.enabled.label }));
    expect(screen.queryByText(strings.tools.blocked.writesOff)).toBeNull();
    expect(screen.getByRole('switch', { name: 'Semantic search' }).hasAttribute('disabled')).toBe(true);
    expect(screen.getByText(strings.status.off)).toBeTruthy();
  });

  it('expands and collapses every tool at once', async () => {
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: strings.tools.expandAll }));
    expect(screen.getAllByRole('button', { name: strings.tools.hideDetails })).toHaveLength(TOOLS.length - 1);
    fireEvent.click(screen.getByRole('button', { name: strings.tools.collapseAll }));
    expect(screen.queryAllByRole('button', { name: strings.tools.hideDetails })).toHaveLength(0);
  });
});
