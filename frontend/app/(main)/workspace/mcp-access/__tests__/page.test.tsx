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
