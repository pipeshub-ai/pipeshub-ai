import React from 'react';
import { render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { useUserStore } from '@/lib/store/user-store';
import { useToastStore } from '@/lib/store/toast-store';
import type {
  AuthSchemaField,
  Connector,
  ConnectorConfig,
  ConnectorInstance,
  ConnectorSchemaResponse,
} from '../types';

export type ConnectorSchema = ConnectorSchemaResponse['schema'];

export function renderInTheme(ui: React.ReactElement) {
  return render(<Theme>{ui}</Theme>);
}

/** jsdom has neither; Radix primitives call both. */
export function installDomShims() {
  class NoopResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  globalThis.ResizeObserver = NoopResizeObserver as unknown as typeof ResizeObserver;
  Element.prototype.scrollIntoView = () => {};
}

export function signInAs(role: 'admin' | 'member') {
  useUserStore.setState({
    isInitialized: true,
    profile: {
      userId: role === 'admin' ? 'u-admin' : 'u-member',
      firstName: null,
      lastName: null,
      fullName: null,
      email: null,
      isAdmin: role === 'admin',
      avatarUrl: null,
      hasLoggedIn: true,
    },
  });
}

export function signOut() {
  useUserStore.setState({ isInitialized: false, profile: null });
}

export function toastTitles(): string[] {
  return useToastStore.getState().toasts.map((t) => t.title);
}

export function toasts() {
  return useToastStore.getState().toasts;
}

export function clearToasts() {
  useToastStore.setState({ toasts: [] });
}

/**
 * The schema form's labels are plain text beside the input rather than `<label for>`,
 * so a field is found by its visible label and then the input inside the same field.
 */
export function inputByLabel(label: string): HTMLInputElement {
  const field = screen.getByText(label).closest('[data-ph-field]');
  const input = field?.querySelector('input:not([type="file"]), textarea');
  if (!input) throw new Error(`No input next to the label "${label}"`);
  return input as HTMLInputElement;
}

export function makeConnector(overrides: Partial<Connector> = {}): Connector {
  return {
    name: 'Jira',
    type: 'Jira',
    appGroup: 'Atlassian',
    appDescription: 'Sync issues and comments',
    appCategories: ['project'],
    iconPath: '/icons/jira.svg',
    supportedAuthTypes: ['API_TOKEN'],
    supportsRealtime: false,
    supportsSync: true,
    supportsAgent: false,
    scope: 'team',
    isActive: false,
    isAgentActive: false,
    isConfigured: false,
    isAuthenticated: false,
    ...overrides,
  };
}

export function makeInstance(overrides: Partial<ConnectorInstance> = {}): ConnectorInstance {
  return {
    ...makeConnector({ isConfigured: true, isAuthenticated: true }),
    _key: 'conn-1',
    name: 'Jira (Engineering)',
    ...overrides,
  };
}

export const apiTokenFields: AuthSchemaField[] = [
  { name: 'baseUrl', displayName: 'Site URL', fieldType: 'URL', required: true },
  { name: 'email', displayName: 'Account email', fieldType: 'EMAIL', required: true },
  {
    name: 'apiToken',
    displayName: 'API token',
    fieldType: 'PASSWORD',
    required: true,
    isSecret: true,
  },
];

export function makeSchema(
  authSchemas: Record<string, AuthSchemaField[]> = { API_TOKEN: apiTokenFields },
  extra: Partial<ConnectorSchema['auth']> = {}
): ConnectorSchema {
  return {
    iconPath: '/icons/jira.svg',
    supportsRealtime: false,
    supportsSync: true,
    supportsAgent: false,
    documentationLinks: [],
    hideConnector: false,
    auth: {
      supportedAuthTypes: Object.keys(authSchemas),
      schemas: Object.fromEntries(
        Object.entries(authSchemas).map(([type, fields]) => [type, { fields }])
      ),
      values: {},
      ...extra,
    },
    sync: {
      supportedStrategies: ['MANUAL', 'SCHEDULED'],
      selectedStrategy: 'MANUAL',
      customFields: [],
      customValues: {},
      values: {},
    },
    filters: {},
  } as unknown as ConnectorSchema;
}

export function makeConfig(overrides: Partial<ConnectorConfig> = {}): ConnectorConfig {
  return {
    name: 'Jira (Engineering)',
    type: 'Jira',
    appGroup: 'Atlassian',
    appGroupId: 'atlassian',
    authType: 'API_TOKEN',
    isActive: false,
    isConfigured: true,
    isAuthenticated: true,
    supportsRealtime: false,
    appDescription: 'Sync issues and comments',
    appCategories: ['project'],
    iconPath: '/icons/jira.svg',
    config: { auth: {}, sync: {}, filters: {} },
    ...overrides,
  };
}
