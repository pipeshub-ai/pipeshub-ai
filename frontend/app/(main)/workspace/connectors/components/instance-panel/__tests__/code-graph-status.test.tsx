import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { screen, cleanup } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';

// The flag is read when the module loads, so it is set before the import.
vi.hoisted(() => {
  process.env.NEXT_PUBLIC_GRAPH_EXPLORER_ACTIVE = 'true';
});

const getCodeGraphRepos = vi.fn();
vi.mock('../../../api', () => ({
  ConnectorsApi: { getCodeGraphRepos: () => getCodeGraphRepos() },
}));

import { locales } from '@/lib/i18n/locales';
import { CodeGraphStatus } from '../code-graph-status';
import { installDomShims, renderInTheme } from '../../../__tests__/fixtures';

beforeEach(() => {
  installDomShims();
  getCodeGraphRepos.mockReset();
});
afterEach(() => cleanup());

describe('CodeGraphStatus', () => {
  it("lists this connector's repos with their graph status", async () => {
    getCodeGraphRepos.mockResolvedValue([
      { repo: 'acme/shop', source: 'GitHub', connector_id: 'conn-1', graph_status: 'ready' },
      { repo: 'acme/api', source: 'GitHub', connector_id: 'conn-1', graph_status: 'failed',
        last_error: 'RuntimeError: boom' },
      { repo: 'other/repo', source: 'GitLab', connector_id: 'conn-2', graph_status: 'ready' },
    ]);
    renderInTheme(<CodeGraphStatus connectorId="conn-1" />);

    expect(await screen.findByText('acme/shop')).toBeTruthy();
    expect(screen.getByText('Ready')).toBeTruthy();
    expect(screen.getByText('Failed').getAttribute('title')).toBe('RuntimeError: boom');
    expect(screen.queryByText('other/repo')).toBeNull();
  });

  it('names each graph link after its repository, and offers none without a graph', async () => {
    getCodeGraphRepos.mockResolvedValue([
      { repo: 'acme/shop', source: 'GitHub', connector_id: 'conn-1', graph_status: 'ready' },
      { repo: 'acme/api', source: 'GitHub', connector_id: 'conn-1', graph_status: 'stale' },
      { repo: 'acme/new', source: 'GitHub', connector_id: 'conn-1', graph_status: 'never_built' },
    ]);
    renderInTheme(<CodeGraphStatus connectorId="conn-1" />);

    const shop = await screen.findByRole('link', { name: 'View graph of acme/shop' });
    expect(shop.getAttribute('href')).toBe('/graph/?repo=acme%2Fshop');
    expect(shop.textContent).toBe('View graph');
    expect(screen.getByRole('link', { name: 'View graph of acme/api' })).toBeTruthy();
    expect(screen.getAllByRole('link')).toHaveLength(2);
  });

  it.each(Object.entries(locales))(
    '%s: the link name contains its visible label, so speech input can click it',
    (_lang, strings) => {
      const ui = (strings as { graph: { ui: Record<string, string> } }).graph.ui;
      expect(ui.viewGraphOf).toContain(ui.viewGraph);
    },
  );

  it('renders nothing when the code graph cannot be reached', async () => {
    getCodeGraphRepos.mockRejectedValue(new Error('503'));
    const { container } = renderInTheme(<CodeGraphStatus connectorId="conn-1" />);
    await vi.waitFor(() => expect(getCodeGraphRepos).toHaveBeenCalled());
    expect(container.textContent).not.toContain('Code graph');
  });
});
