import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import { compiledSpec, toolset, toolsetConfig } from './fixtures';

const api = vi.hoisted(() => ({
  validateSpec: vi.fn(),
  getConfig: vi.fn(),
  createInstance: vi.fn(),
  updateInstance: vi.fn(),
  saveCredentials: vi.fn(),
  deleteCredentials: vi.fn(),
  getAuthorizationUrl: vi.fn(),
  listInstances: vi.fn(),
  testConnection: vi.fn(),
}));
vi.mock('../api', () => ({ ApiToolsetsApi: api }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { ApiToolsetEditorPanel } from '../components/api-toolset-editor-panel';
import { ApiActionConnectionPanel } from '../components/api-action-connection-panel';
import { ApiActionCard } from '../components/api-action-card';
import { operation } from './fixtures';

beforeEach(() => {
  installBrowserShims();
  Object.values(api).forEach((fn) => fn.mockReset());
});
afterEach(cleanup);

function checkbox(name: RegExp) {
  return screen.getByText(name).closest('label')!.querySelector('[role="checkbox"]')!;
}

describe('ApiToolsetEditorPanel', () => {
  it('previews a pasted spec, preselects read operations and saves the selection', async () => {
    api.validateSpec.mockResolvedValue(compiledSpec({ warnings: ['Skipped GET /legacy: no responses'] }));
    api.createInstance.mockResolvedValue(toolsetConfig());
    const onSaved = vi.fn();
    renderInTheme(<ApiToolsetEditorPanel open editingId={null} onOpenChange={() => {}} onSaved={onSaved} />);

    fireEvent.change(screen.getByPlaceholderText(/openapi: 3.1.0/), { target: { value: 'openapi: 3.1.0' } });
    fireEvent.click(screen.getByRole('button', { name: 'Read spec' }));

    expect(await screen.findByText('Skipped GET /legacy: no responses')).toBeTruthy();
    expect(screen.getByText('Operations (1 of 3 selected)')).toBeTruthy();
    expect(checkbox(/^\/listOrders$/).getAttribute('aria-checked')).toBe('true');
    expect(checkbox(/^\/deleteOrder$/).getAttribute('aria-checked')).toBe('false');
    expect(screen.getByDisplayValue('Orders API')).toBeTruthy();
    expect(screen.getByDisplayValue('https://orders.internal/v1')).toBeTruthy();

    fireEvent.click(checkbox(/^\/createOrder$/));
    fireEvent.click(screen.getByRole('button', { name: 'Add API action' }));

    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(api.createInstance).toHaveBeenCalledWith(
      expect.objectContaining({
        spec: 'openapi: 3.1.0',
        name: 'Orders API',
        authSchemeName: 'apiKey',
        operations: ['listOrders', 'createOrder'],
      }),
    );
  });

  it('without authentication, warns about selected operations that need credentials and deselects them', async () => {
    const open = { ...operation('listNews', 'read'), requiresAuth: false };
    api.validateSpec.mockResolvedValue(compiledSpec({ authSchemes: [], operations: [operation('listOrders', 'read'), open] }));
    renderInTheme(<ApiToolsetEditorPanel open editingId={null} onOpenChange={() => {}} onSaved={() => {}} />);
    fireEvent.change(screen.getByPlaceholderText(/openapi: 3.1.0/), { target: { value: 'openapi: 3.1.0' } });
    fireEvent.click(screen.getByRole('button', { name: 'Read spec' }));

    // Only the public read is preselected when the spec offers no sign-in.
    expect(await screen.findByText('Operations (1 of 2 selected)')).toBeTruthy();
    fireEvent.click(checkbox(/^\/listOrders$/));
    expect(screen.getByText('1 selected operation needs credentials, so it fails without authentication.')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Deselect them' }));
    expect(screen.getByText('Operations (1 of 2 selected)')).toBeTruthy();
    expect(checkbox(/^\/listOrders$/).getAttribute('aria-checked')).toBe('false');
  });

  it('does not save a replacement spec until it has been read', async () => {
    api.getConfig.mockResolvedValue(toolsetConfig());
    api.validateSpec.mockResolvedValue(compiledSpec());
    renderInTheme(<ApiToolsetEditorPanel open editingId={'a'.repeat(32)} onOpenChange={() => {}} onSaved={() => {}} />);
    fireEvent.click(await screen.findByRole('button', { name: /Replace spec/ }));
    fireEvent.change(screen.getByPlaceholderText(/openapi: 3.1.0/), { target: { value: 'openapi: 3.1.0 # v2' } });

    const save = screen.getByRole('button', { name: 'Save' });
    expect(save.hasAttribute('disabled')).toBe(true);
    fireEvent.click(save);
    expect(api.updateInstance).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: /Read spec/ }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save' }).hasAttribute('disabled')).toBe(false));
  });

  it('offers the sample spec as YAML and JSON downloads', () => {
    renderInTheme(<ApiToolsetEditorPanel open editingId={null} onOpenChange={() => {}} onSaved={() => {}} />);
    const yaml = screen.getByRole('link', { name: /Sample YAML/ });
    const json = screen.getByRole('link', { name: /Sample JSON/ });
    expect(yaml.getAttribute('href')).toBe('/samples/api-action-sample.yaml');
    expect(json.getAttribute('href')).toBe('/samples/api-action-sample.json');
    expect(yaml.hasAttribute('download') && json.hasAttribute('download')).toBe(true);
  });

  it("shows the backend's reason when the spec is refused", async () => {
    api.validateSpec.mockRejectedValue({ type: 'VALIDATION_ERROR', message: 'Swagger 2.0 specs are not supported.', statusCode: 400 });
    renderInTheme(<ApiToolsetEditorPanel open editingId={null} onOpenChange={() => {}} onSaved={() => {}} />);
    fireEvent.change(screen.getByPlaceholderText(/openapi: 3.1.0/), { target: { value: 'swagger: "2.0"' } });
    fireEvent.click(screen.getByRole('button', { name: 'Read spec' }));
    expect(await screen.findByText('Swagger 2.0 specs are not supported.')).toBeTruthy();
  });

  it('edits without a new spec: stored operations, locked scheme, no spec sent', async () => {
    api.getConfig.mockResolvedValue(toolsetConfig());
    api.updateInstance.mockResolvedValue(toolsetConfig());
    const id = 'a'.repeat(32);
    renderInTheme(<ApiToolsetEditorPanel open editingId={id} onOpenChange={() => {}} onSaved={() => {}} />);

    expect(await screen.findByText('Upload the spec again to change how PipesHub authenticates.')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(api.updateInstance).toHaveBeenCalled());
    const [calledId, payload] = api.updateInstance.mock.calls[0];
    expect(calledId).toBe(id);
    expect(payload).not.toHaveProperty('spec');
    expect(payload.operations).toEqual(['listOrders', 'createOrder']);
  });
});

describe('ApiActionConnectionPanel', () => {
  const render = (overrides: Parameters<typeof toolset>[0], isAdmin = false) =>
    renderInTheme(<ApiActionConnectionPanel toolset={toolset(overrides)} isAdmin={isAdmin} onOpenChange={() => {}} onChanged={() => {}} />);

  it("asks for username and password for basic auth and saves the member's own credential", async () => {
    api.saveCredentials.mockResolvedValue(undefined);
    render({ authType: 'http_basic' });
    fireEvent.change(screen.getByLabelText(/Username/), { target: { value: 'me' } });
    fireEvent.change(screen.getByLabelText(/Password/), { target: { value: 'pw' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(api.saveCredentials).toHaveBeenCalledWith('a'.repeat(32), { username: 'me', password: 'pw' }, false),
    );
  });

  it('offers sign-in instead of fields for the OAuth sign-in flow', () => {
    render({ authType: 'oauth2_authorization_code' });
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
  });

  it('lets an admin manage the shared credential and shows members it is managed for them', () => {
    render({ authType: 'oauth2_client_credentials', useSharedCredential: true }, true);
    expect(screen.getByLabelText(/Client ID/)).toBeTruthy();
    expect(screen.getByText('Every member uses the credential you set here.')).toBeTruthy();
    cleanup();
    render({ authType: 'oauth2_client_credentials', useSharedCredential: true, isConnected: true }, false);
    expect(screen.getByText('Your admin manages the credential for this API action.')).toBeTruthy();
    expect(screen.queryByLabelText(/Client ID/)).toBeNull();
    expect(screen.queryByRole('button', { name: 'Disconnect' })).toBeNull();
  });

  it('shows the status and start of the body from a test call', async () => {
    api.testConnection.mockResolvedValue({ ok: false, status: 401, body: '{"error":"bad key"}', truncated: true });
    render({ authType: 'api_key', isConnected: true });
    fireEvent.click(screen.getByRole('button', { name: 'Test connection' }));
    expect(await screen.findByText('401')).toBeTruthy();
    expect(screen.getByText('The API answered with an error.')).toBeTruthy();
    expect(screen.getByText('{"error":"bad key"}')).toBeTruthy();
    expect(screen.getByText('Only the start of the response is shown.')).toBeTruthy();
  });
});

describe('ApiActionCard', () => {
  const render = (overrides: Parameters<typeof toolset>[0], admin = false) => {
    const handlers = { onOpen: vi.fn(), onEdit: vi.fn(), onDelete: vi.fn() };
    renderInTheme(
      <ApiActionCard toolset={toolset(overrides)} isAdmin={admin} onOpen={handlers.onOpen}
        onEdit={admin ? handlers.onEdit : undefined} onDelete={admin ? handlers.onDelete : undefined} />,
    );
    return handlers;
  };

  it('asks a member to connect their own credential and opens the connection panel', () => {
    const { onOpen } = render({ authType: 'api_key', isConnected: false });
    expect(screen.getByText('Not connected')).toBeTruthy();
    expect(screen.getByText('1 read · 1 write')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it('says an API without authentication needs no sign-in, and shows members only the details', () => {
    render({ authType: 'none', isConnected: true });
    expect(screen.getByText('No sign-in needed')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Details' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'More options for Orders' })).toBeNull();
  });

  it('gives admins a menu to edit and delete', () => {
    render({ authType: 'api_key', isConnected: true, useSharedCredential: true }, true);
    expect(screen.getByRole('button', { name: 'Manage connection' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'More options for Orders' })).toBeTruthy();
  });
});
