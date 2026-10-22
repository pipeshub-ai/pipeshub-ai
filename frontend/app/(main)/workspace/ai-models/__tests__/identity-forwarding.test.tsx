import '@/lib/__tests__/test-i18n';
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import type { ConfiguredModel, IdentityPolicy } from '../types';

const api = vi.hoisted(() => ({
  setConnectionIdentityForwarding: vi.fn(),
  previewIdentity: vi.fn(),
  getIdentityPolicy: vi.fn(),
  putIdentityPolicy: vi.fn(),
}));
vi.mock('../api', () => ({ AIModelsApi: api }));

const toastMock = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock('@/lib/store/toast-store', () => ({ toast: toastMock }));

import { IdentityForwardingDialog } from '../components/identity-forwarding-dialog';
import { IdentityPolicyDialog } from '../components/identity-policy-dialog';
import { supportsIdentityForwarding } from '../types';

const model = (configuration: Record<string, unknown> = {}, configuredFields: string[] = []): ConfiguredModel => ({
  modelKey: 'm1',
  provider: 'litellmProxy',
  modelType: 'llm',
  configuration,
  isDefault: false,
  connectionId: 'conn-1',
  configuredFields,
});

const policy: IdentityPolicy = {
  subjectSource: 'userId',
  includeEmail: false,
  includeDisplayName: false,
  groups: { enabled: true, allowList: ['g1'], maxForwarded: 50 },
  pseudonymize: false,
  onMissingIdentity: 'service',
  queryEmbeddingIdentity: 'user',
  serviceIdentityPrefix: 'svc:pipeshub',
};

const withTheme = (node: React.ReactElement) => <Theme>{node}</Theme>;

describe('identity forwarding', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Radix's scroll area measures itself on mount; jsdom has no ResizeObserver.
    vi.stubGlobal(
      'ResizeObserver',
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    );
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('offers forwarding only for gateway providers', () => {
    expect(supportsIdentityForwarding('litellmProxy')).toBe(true);
    expect(supportsIdentityForwarding('openRouter')).toBe(true);
    expect(supportsIdentityForwarding('anthropic')).toBe(false);
  });

  it('saves the connection with the chosen settings and never sends a key it was not given', async () => {
    api.setConnectionIdentityForwarding.mockResolvedValue({});
    const onSaved = vi.fn();
    const onClose = vi.fn();
    render(withTheme(<IdentityForwardingDialog model={model()} onClose={onClose} onSaved={onSaved} />));

    fireEvent.click(screen.getAllByRole('switch')[0]);
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(api.setConnectionIdentityForwarding).toHaveBeenCalled());
    const [connectionId, body] = api.setConnectionIdentityForwarding.mock.calls[0];
    expect(connectionId).toBe('conn-1');
    expect(body).toMatchObject({ enabled: true, mode: 'headers', preset: 'litellm' });
    expect('serviceApiKey' in body).toBe(false);
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(onClose).toHaveBeenCalled();
  });

  it('starts from what the connection already has', () => {
    render(
      withTheme(
        <IdentityForwardingDialog
          model={model({ identityForwardingEnabled: true }, ['identityForwardingServiceApiKey'])}
          onClose={vi.fn()}
          onSaved={vi.fn()}
        />,
      ),
    );
    expect(screen.getAllByRole('switch')[0].getAttribute('aria-checked')).toBe('true');
    expect(screen.getByPlaceholderText('A key is saved. Type to replace it.')).toBeTruthy();
  });

  it('shows the headers a preview returns', async () => {
    api.previewIdentity.mockResolvedValue({
      success: true,
      identity: { subject: 'u1', subjectKind: 'user', groups: [], groupsSource: 'none', groupsComplete: true },
      headers: { 'x-litellm-end-user-id': 'u1' },
      notes: ['No groups were resolved for this person'],
    });
    render(
      withTheme(
        <IdentityForwardingDialog
          model={model({ identityForwardingEnabled: true })}
          onClose={vi.fn()}
          onSaved={vi.fn()}
        />,
      ),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
    expect(await screen.findByText('x-litellm-end-user-id')).toBeTruthy();
    expect(screen.getByText('No groups were resolved for this person')).toBeTruthy();
    expect(api.previewIdentity.mock.calls[0][0]).toMatchObject({ connectionId: 'conn-1', workload: 'chat' });
  });

  it('saves the policy with the group list from the text box', async () => {
    api.getIdentityPolicy.mockResolvedValue({ policy, damaged: false });
    api.putIdentityPolicy.mockResolvedValue(policy);
    render(withTheme(<IdentityPolicyDialog open onClose={vi.fn()} />));

    const box = await screen.findByDisplayValue('g1');
    fireEvent.change(box, { target: { value: 'g1\n\n g2 ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(api.putIdentityPolicy).toHaveBeenCalled());
    expect(api.putIdentityPolicy.mock.calls[0][0].groups.allowList).toEqual(['g1', 'g2']);
  });

  it('says so when the policy cannot be loaded', async () => {
    api.getIdentityPolicy.mockRejectedValue(new Error('boom'));
    render(withTheme(<IdentityPolicyDialog open onClose={vi.fn()} />));
    expect(await screen.findByText('Could not load the identity policy')).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('keeps a signed-token connection signed, with its audience and service auth, when saved', async () => {
    api.setConnectionIdentityForwarding.mockResolvedValue({});
    render(
      withTheme(
        <IdentityForwardingDialog
          model={model({
            identityForwardingEnabled: true,
            identityForwardingMode: 'signedJwt',
            identityForwardingJwtAudience: 'litellm',
            identityForwardingServiceAuth: 'jwt',
          })}
          onClose={vi.fn()}
          onSaved={vi.fn()}
        />,
      ),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(api.setConnectionIdentityForwarding).toHaveBeenCalled());
    expect(api.setConnectionIdentityForwarding.mock.calls[0][1]).toMatchObject({
      mode: 'signedJwt',
      jwtAudience: 'litellm',
      serviceAuth: 'jwt',
    });
  });

  it('gives every policy switch an accessible name', async () => {
    api.getIdentityPolicy.mockResolvedValue({ policy, damaged: false });
    render(withTheme(<IdentityPolicyDialog open onClose={vi.fn()} />));
    await screen.findByDisplayValue('g1');
    for (const toggle of screen.getAllByRole('switch')) {
      expect(toggle.getAttribute('aria-label')).toBeTruthy();
    }
  });

  it('cannot save the previous policy while the next load is pending', async () => {
    api.getIdentityPolicy.mockResolvedValueOnce({ policy, damaged: false });
    const view = render(withTheme(<IdentityPolicyDialog open onClose={vi.fn()} />));
    await screen.findByDisplayValue('g1');

    view.rerender(withTheme(<IdentityPolicyDialog open={false} onClose={vi.fn()} />));
    api.getIdentityPolicy.mockReturnValueOnce(new Promise(() => undefined));
    view.rerender(withTheme(<IdentityPolicyDialog open onClose={vi.fn()} />));

    await waitFor(() =>
      expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true),
    );
    expect(screen.queryByDisplayValue('g1')).toBeNull();
  });

  it('warns when the stored policy was unreadable', async () => {
    api.getIdentityPolicy.mockResolvedValue({ policy, damaged: true });
    render(withTheme(<IdentityPolicyDialog open onClose={vi.fn()} />));
    expect(await screen.findByText(/could not be read/)).toBeTruthy();
  });
});
