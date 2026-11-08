import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { cleanup, fireEvent, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { McpMyServerEntry, McpServerTemplate } from '../../../types';

// These tests cover the default notices, whatever the build's config provides.
vi.mock('@/config', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/config')>()),
  EnterpriseFeatureNotice: (await import('@/app/components/ui/enterprise-badge')).EnterpriseFeatureNotice,
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => null }));
const viewport = vi.hoisted(() => ({ mobile: false }));
vi.mock('@/lib/hooks/use-is-mobile', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/hooks/use-is-mobile')>()),
  useIsMobile: () => viewport.mobile,
}));

import { McpCatalogLayout } from '../mcp-catalog-layout';

afterEach(() => {
  cleanup();
  viewport.mobile = false;
});

function template(typeId: string, displayName: string): McpServerTemplate {
  return {
    typeId,
    displayName,
    description: `${displayName} server`,
    transport: 'streamable_http',
    defaultAuthMode: 'none',
    supportedAuthModes: ['none'],
    args: [],
    requiredEnv: [],
    optionalEnv: [],
    defaultScopes: [],
    supportsDcr: false,
    tags: [],
  };
}

const instance = {
  _id: 'i1',
  name: 'GitHub team',
  typeId: 'github',
  transport: 'streamable_http',
  authMode: 'none',
  isCustom: false,
} as unknown as McpMyServerEntry;

const noop = () => {};

function renderLayout(onOpenPersonal = noop) {
  installBrowserShims();
  renderInTheme(
    <McpCatalogLayout
      templates={[template('github', 'GitHub'), template('exa', 'Exa')]}
      instances={[instance]}
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
      onOpenPersonal={onOpenPersonal}
    />
  );
}

describe('team MCP catalog', () => {
  it('filters by configured and not configured, like the other catalogs', () => {
    renderLayout();
    expect(screen.getByText('GitHub')).toBeTruthy();
    expect(screen.getByText('Exa')).toBeTruthy();

    fireEvent.click(screen.getByRole('radio', { name: 'Not configured (1)' }));
    expect(screen.queryByText('GitHub')).toBeNull();
    expect(screen.getByText('Exa')).toBeTruthy();

    fireEvent.click(screen.getByRole('radio', { name: 'Configured (1)' }));
    expect(screen.getByText('GitHub')).toBeTruthy();
    expect(screen.queryByText('Exa')).toBeNull();
  });

  it('links to the personal MCP servers page', () => {
    const onOpenPersonal = vi.fn();
    renderLayout(onOpenPersonal);
    fireEvent.click(screen.getByRole('button', { name: 'Your MCP servers' }));
    expect(onOpenPersonal).toHaveBeenCalledTimes(1);
  });

  it('on phones puts compact actions above search and tabs, each with a touch hit area', () => {
    viewport.mobile = true;
    renderLayout();
    const add = screen.getByRole('button', { name: 'New server' });
    const more = screen.getByRole('button', { name: 'More options' });
    const link = screen.getByRole('button', { name: 'Your MCP servers' });
    for (const el of [add, more, link]) expect(el.classList.contains('touch-hit')).toBe(true);
    const search = screen.getByPlaceholderText('Search MCP servers...');
    // DOCUMENT_POSITION_FOLLOWING: the search box comes after the actions
    expect(add.compareDocumentPosition(search) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(link.compareDocumentPosition(search) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});
