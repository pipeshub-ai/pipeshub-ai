import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

const api = vi.hoisted(() => ({
  addProvider: vi.fn(),
  updateProvider: vi.fn(),
  deleteProvider: vi.fn(),
  revealProviderApiKey: vi.fn(),
  getProviderUsage: vi.fn(),
}));

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/api', () => ({ isProcessedError: () => false }));
vi.mock('@/lib/hooks/use-secret-reveal-available', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/hooks/use-secret-reveal-available')>()),
  useSecretRevealAvailable: () => false,
}));
vi.mock('@/config', () => ({ InheritedConfigNotice: () => null }));
vi.mock('../../api', () => ({ WebSearchApi: api }));
vi.mock('../../../components/confirmation-dialog', () => ({ ConfirmationDialog: () => null }));
vi.mock('../../../components/workspace-right-panel', () => ({
  WorkspaceRightPanel: ({
    open,
    children,
    onPrimaryClick,
    primaryDisabled,
  }: {
    open: boolean;
    children: React.ReactNode;
    onPrimaryClick?: () => void;
    primaryDisabled?: boolean;
  }) =>
    open ? (
      <div>
        {children}
        <button type="button" disabled={primaryDisabled} onClick={onPrimaryClick}>
          save
        </button>
      </div>
    ) : null,
}));

import { ConfigurePanel } from '../configure-panel';
import { WEB_SEARCH_PROVIDER_META, type ConfigurableProvider } from '../../types';

function open(provider: ConfigurableProvider) {
  const meta = WEB_SEARCH_PROVIDER_META.find((m) => m.type === provider) ?? null;
  return render(
    <Theme>
      <ConfigurePanel
        open
        provider={provider}
        providerMeta={meta}
        existingProvider={null}
        onClose={vi.fn()}
        onSaveSuccess={vi.fn()}
        onDeleteSuccess={vi.fn()}
      />
    </Theme>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('ConfigurePanel — each provider asks for what it needs', () => {
  it('a self-hosted searcher asks for its server address, not an API key', async () => {
    open('searxng');

    expect(screen.getByText('Server URL')).toBeTruthy();
    expect(screen.queryByText('API Key')).toBeNull();
    const save = screen.getByRole('button', { name: 'save' }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);

    fireEvent.change(screen.getByPlaceholderText('http://searxng:8080'), { target: { value: ' http://searxng:8080 ' } });
    expect(save.disabled).toBe(false);
    fireEvent.click(save);

    await waitFor(() => expect(api.addProvider).toHaveBeenCalled());
    expect(api.addProvider.mock.calls[0][0]).toEqual({
      provider: 'searxng',
      configuration: { baseUrl: 'http://searxng:8080' },
      isDefault: false,
    });
  });

  it('Firecrawl needs nothing for its cloud server but sends what is filled in', async () => {
    open('firecrawl');

    const save = screen.getByRole('button', { name: 'save' }) as HTMLButtonElement;
    expect(save.disabled).toBe(false);
    fireEvent.change(screen.getByPlaceholderText('Enter your Firecrawl API key'), { target: { value: 'fc-1' } });
    fireEvent.click(save);

    await waitFor(() => expect(api.addProvider).toHaveBeenCalled());
    expect(api.addProvider.mock.calls[0][0].configuration).toEqual({ apiKey: 'fc-1' });
  });

  it('an API provider still needs its key', () => {
    open('serper');

    expect((screen.getByRole('button', { name: 'save' }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByPlaceholderText('Enter your Serper API key')).toBeTruthy();
  });
});
