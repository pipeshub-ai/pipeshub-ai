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
