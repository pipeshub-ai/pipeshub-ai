import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { screen, cleanup } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';

const getCodeGraphRepos = vi.fn();
vi.mock('../../../api', () => ({
  ConnectorsApi: { getCodeGraphRepos: () => getCodeGraphRepos() },
}));

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

  it('renders nothing when the code graph cannot be reached', async () => {
    getCodeGraphRepos.mockRejectedValue(new Error('503'));
    const { container } = renderInTheme(<CodeGraphStatus connectorId="conn-1" />);
    await vi.waitFor(() => expect(getCodeGraphRepos).toHaveBeenCalled());
    expect(container.textContent).not.toContain('Code graph');
  });
});
