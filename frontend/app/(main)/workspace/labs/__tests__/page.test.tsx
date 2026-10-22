import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import en from '@/lib/i18n/locales/en-US.json';

type AvailableFlag = {
  key: string;
  label: string;
  description: string;
  defaultEnabled: boolean;
  advanced?: boolean;
};

const FLAGS: AvailableFlag[] = [
  { key: 'ENABLE_MCP', label: 'Enable MCP Servers', description: 'mcp', defaultEnabled: true },
  { key: 'ENABLE_ACTIONS', label: 'Enable Actions', description: 'actions', defaultEnabled: true },
  { key: 'ENABLE_PROJECTS', label: 'Enable Projects', description: 'projects', defaultEnabled: false },
  { key: 'ENABLE_SKILLS', label: 'Enable Skills (Beta)', description: 'skills', defaultEnabled: true },
  {
    key: 'ENABLE_VECTOR_STORE_REBUILD',
    label: 'Enable Vector Store Rebuild',
    description: 'rebuild',
    defaultEnabled: false,
    advanced: true,
  },
  {
    key: 'ENABLE_SOFT_DELETE',
    label: 'Move Deleted Records to the Trash',
    description: 'trash',
    defaultEnabled: false,
    advanced: true,
  },
  {
    key: 'ENABLE_NAMED_ENTITY_EXTRACTION',
    label: 'Extract Named Entities',
    description: 'entities',
    defaultEnabled: false,
    advanced: true,
  },
  {
    key: 'ENABLE_USER_CONTEXT',
    label: 'Send User & Organization Context to Agents',
    description: 'context',
    defaultEnabled: true,
    advanced: true,
  },
];

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: vi.fn() }),
}));

vi.mock('@/lib/store/user-store', () => ({
  useUserStore: (selector: (s: { isAdmin: boolean; isProfileInitialized: boolean }) => unknown) =>
    selector({ isAdmin: true, isProfileInitialized: true }),
  selectIsAdmin: (s: { isAdmin: boolean }) => s.isAdmin,
  selectIsProfileInitialized: (s: { isProfileInitialized: boolean }) => s.isProfileInitialized,
}));

vi.mock('@/lib/store/feature-flags-store', () => ({
  useFeatureFlagsStore: (selector: (s: { fetchFlags: () => Promise<void> }) => unknown) =>
    selector({ fetchFlags: vi.fn() }),
}));

vi.mock('@/lib/store/toast-store', () => ({
  useToastStore: (selector: (s: { addToast: () => void }) => unknown) =>
    selector({ addToast: vi.fn() }),
}));

vi.mock('@/app/components/ui/lottie-loader', () => ({
  LottieLoader: () => <div>loading</div>,
}));

vi.mock('../../connectors/components/vector-store-actions', () => ({
  VectorStoreActions: () => <div>vector-store-actions</div>,
}));

vi.mock('../../components', () => ({
  ConfirmationDialog: () => null,
  SettingsSaveBar: () => null,
  SettingsSection: ({
    children,
    title,
  }: {
    children: React.ReactNode;
    title?: string;
  }) => (
    <div>
      {title}
      {children}
    </div>
  ),
  SettingsRow: () => null,
}));

vi.mock('react-i18next', async () => {
  const { default: locale } = await import('@/lib/i18n/locales/en-US.json');
  return {
    useTranslation: () => ({
      t: (key: string, fallback?: string) => {
        const parts = key.split('.');
        let cur: unknown = locale;
        for (const part of parts) {
          if (typeof cur !== 'object' || cur === null || !(part in cur)) {
            return typeof fallback === 'string' ? fallback : key;
          }
          cur = (cur as Record<string, unknown>)[part];
        }
        return typeof cur === 'string' ? cur : typeof fallback === 'string' ? fallback : key;
      },
    }),
    initReactI18next: { type: '3rdParty', init: () => undefined },
  };
});

vi.mock('../api', () => ({
  bytesToMb: (bytes: number) => Math.round(bytes / (1024 * 1024)),
  mbToBytes: (mb: number) => mb * 1024 * 1024,
  LabsApi: {
    getSettings: vi.fn(),
    getAvailableFlags: vi.fn(),
    saveSettings: vi.fn(),
  },
}));

import LabsPage from '../page';
import { LabsApi } from '../api';
import { useLabsStore } from '../store';

const ADVANCED_LABELS = [
  'Enable Vector Store Rebuild',
  'Move Deleted Records to the Trash',
  'Extract Named Entities',
  'Send User & Organization Context to Agents',
];

const REGULAR_LABELS = [
  'Enable MCP Servers',
  'Enable Actions',
  'Enable Projects',
  'Enable Skills (Beta)',
];

beforeEach(() => {
  useLabsStore.setState({
    form: { fileSizeLimitMb: '', featureFlags: {} },
    savedForm: { fileSizeLimitMb: '', featureFlags: {} },
    availableFlags: [],
    errors: {},
    discardDialogOpen: false,
    isLoading: true,
  });
  vi.mocked(LabsApi.getSettings).mockResolvedValue({
    fileUploadMaxSizeBytes: 30 * 1024 * 1024,
    featureFlags: {},
  });
  vi.mocked(LabsApi.getAvailableFlags).mockResolvedValue(FLAGS);
});

afterEach(() => {
  cleanup();
});

function renderPage() {
  return render(
    <Theme>
      <LabsPage />
    </Theme>,
  );
}

describe('LabsPage feature flags', () => {
  it('shows regular flags and keeps advanced flags behind the accordion', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Enable MCP Servers')).toBeTruthy();
    });

    for (const label of REGULAR_LABELS) {
      expect(screen.getByText(label)).toBeTruthy();
      expect(screen.getByRole('switch', { name: label })).toBeTruthy();
    }

    expect(screen.getByText(en.workspace.labs.featureFlags.advancedTitle)).toBeTruthy();
    expect(
      screen.getByRole('button', { name: /Show advanced feature flags/i }).getAttribute('aria-expanded'),
    ).toBe('false');

    for (const label of ADVANCED_LABELS) {
      expect(screen.queryByText(label)).toBeNull();
    }
  });

  it('reveals the five advanced flags when the accordion is opened', async () => {
    renderPage();

    const accordion = await screen.findByRole('button', {
      name: /Show advanced feature flags/i,
    });
    fireEvent.click(accordion);

    for (const label of ADVANCED_LABELS) {
      expect(screen.getByText(label)).toBeTruthy();
      expect(screen.getByRole('switch', { name: label })).toBeTruthy();
    }
    expect(accordion.getAttribute('aria-expanded')).toBe('true');
  });
});
