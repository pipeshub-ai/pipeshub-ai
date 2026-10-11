import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { cleanup, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { McpMyServerEntry, McpServerTemplate } from '../types';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => null }));

import { isOfferedForNewServers, replacementFor } from '../catalog-replacement';
import { McpCatalogLayout } from '../team/components/mcp-catalog-layout';
import { McpAddServerDialog } from '../personal/components/mcp-add-server-dialog';

function template(overrides: Partial<McpServerTemplate> = {}): McpServerTemplate {
  return {
    typeId: 'slack',
    displayName: 'Slack (community)',
    description: 'The archived community Slack server.',
    transport: 'stdio',
    defaultAuthMode: 'api_token',
    supportedAuthModes: ['api_token'],
    args: [],
    requiredEnv: [],
    optionalEnv: [],
    defaultScopes: [],
    supportsDcr: false,
    tags: [],
    replacedBy: 'slack_official',
    ...overrides,
  };
}

const OFFICIAL = template({
  typeId: 'slack_official',
  displayName: 'Slack',
  description: "Slack's own hosted server.",
  transport: 'streamable_http',
  defaultAuthMode: 'oauth',
  supportedAuthModes: ['oauth'],
  replacedBy: null,
});

function instance(typeId: string): McpMyServerEntry {
  return {
    _id: `inst-${typeId}`, orgId: 'org-1', createdBy: 'u-1', name: 'Team Slack', typeId, transport: 'stdio',
    authMode: 'api_token', useAdminAuth: false, args: [], requiredEnv: [], optionalEnv: [], scopes: [],
    isCustom: false, createdAt: 1, updatedAt: 1, isAuthenticated: true, tools: [],
  } as McpMyServerEntry;
}

function renderCatalog(templates: McpServerTemplate[], instances: McpMyServerEntry[]) {
  const noop = vi.fn();
  renderInTheme(
    <McpCatalogLayout
      templates={templates}
      instances={instances}
      isLoading={false}
      searchQuery=""
      onSearchChange={noop}
      onAddCustom={noop}
      onSetupTemplate={noop}
      onAddInstanceForTemplate={noop}
      onManageTemplate={noop}
      onEditInstance={noop}
      onDeleteInstance={noop}
      onRefresh={noop}
      userCreatedInstances={[]}
      ownerNames={{}}
      onDeleteUserCreated={noop}
    />
  );
}

beforeEach(() => {
  installBrowserShims();
});

afterEach(() => {
  cleanup();
});

describe('replaced catalog entries', () => {
  it('know their replacement', () => {
    expect(isOfferedForNewServers(template())).toBe(false);
    expect(isOfferedForNewServers(OFFICIAL)).toBe(true);
    expect(replacementFor(template(), [template(), OFFICIAL])).toBe(OFFICIAL);
    expect(replacementFor(OFFICIAL, [template(), OFFICIAL])).toBeNull();
  });

  it('are not offered for setup when nobody uses them', () => {
    renderCatalog([template(), OFFICIAL], []);
    expect(screen.queryByText('Slack (community)')).toBeNull();
    expect(screen.getByText('Slack')).toBeTruthy();
  });

  it('still show where servers made from them exist, with the replacement and no "+"', () => {
    renderCatalog([template(), OFFICIAL], [instance('slack')]);
    expect(screen.getByText('Slack (community)')).toBeTruthy();
    expect(screen.getByText('No longer maintained. Add Slack instead.')).toBeTruthy();
    // The official entry is unused, so its card offers Setup rather than "+"; the replaced one offers neither.
    expect(screen.queryByRole('button', { name: 'Add Another Instance' })).toBeNull();
  });

  it('leave "+" on an entry still offered', () => {
    renderCatalog([template(), OFFICIAL], [instance('slack'), instance('slack_official')]);
    expect(screen.getAllByRole('button', { name: 'Add Another Instance' })).toHaveLength(1);
  });

  it('are left out of the personal "add server" list', () => {
    renderInTheme(
      <McpAddServerDialog
        open
        onOpenChange={vi.fn()}
        templates={[template({ transport: 'streamable_http' }), OFFICIAL]}
        onPickTemplate={vi.fn()}
        onPickCustom={vi.fn()}
      />
    );
    expect(screen.queryByText('Slack (community)')).toBeNull();
    expect(screen.getByText('Slack')).toBeTruthy();
  });
});
