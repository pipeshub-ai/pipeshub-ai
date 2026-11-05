import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { BuilderSidebarToolset } from '@/app/(main)/toolsets/api';
import type { User } from '@/app/(main)/workspace/users/types';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui', () => ({ ConnectorIcon: () => null }));
vi.mock('@/config', () => ({ ToolsetToolRulesDialog: () => null }));
vi.mock('../components/toolset-tool-rules-dialog', () => ({ ToolsetToolRulesDialog: () => null }));
vi.mock('@radix-ui/themes', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@radix-ui/themes')>()),
  Avatar: ({ src }: { src?: string }) => <span data-testid="avatar" data-src={src ?? ''} />,
}));
const directory = vi.hoisted(() => ({ user: null as User | null, asked: [] as unknown[] }));
vi.mock('@/lib/hooks/use-user-directory-entry', () => ({
  useUserDirectoryEntry: (id: unknown) => {
    directory.asked.push(id);
    return directory.user;
  },
}));

import { ToolsetInstanceRowCard } from '../components/toolset-instance-row-card';

const CREATOR = '507f1f77bcf86cd799439011';
const instance = {
  name: 'jira',
  displayName: 'Jira',
  instanceId: 'inst-j',
  instanceName: 'Jira (Engineering)',
  toolsetType: 'jira',
  isAuthenticated: true,
  createdBy: CREATOR,
  tools: [],
} as unknown as BuilderSidebarToolset;

beforeEach(() => {
  installBrowserShims();
  directory.asked = [];
  directory.user = null;
});
afterEach(() => cleanup());

describe('ToolsetInstanceRowCard: enabled by', () => {
  it('shows the creator as "First L" with their picture', () => {
    directory.user = {
      id: 'u1', userId: CREATOR, name: 'Ada Lovelace', hasLoggedIn: true, isActive: true,
      profilePicture: 'data:image/png;base64,AAAA',
    };
    renderInTheme(<ToolsetInstanceRowCard scope="team" instance={instance} onAuthenticate={vi.fn()} onConfigure={vi.fn()} />);

    expect(screen.getByText('Ada L')).toBeTruthy();
    expect(screen.getByTestId('avatar').getAttribute('data-src')).toBe('data:image/png;base64,AAAA');
    expect(directory.asked).toContain(CREATOR);
  });

  it('shows no creator while the user is unknown', () => {
    renderInTheme(<ToolsetInstanceRowCard scope="team" instance={instance} onAuthenticate={vi.fn()} onConfigure={vi.fn()} />);

    expect(screen.queryByTestId('avatar')).toBeNull();
  });
});
