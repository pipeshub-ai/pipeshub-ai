import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import type { User } from '@/app/(main)/workspace/users/types';
import type { ProjectDetail } from '@/chat/project-types';

vi.mock('@/app/components/share/shared-avatar-stack', () => ({ SharedAvatarStack: () => null }));
vi.mock('@/app/components/ui/user-avatar', () => ({
  UserAvatar: ({ fullName, src }: { fullName?: string; src?: string }) => (
    <span data-testid="owner-avatar" data-name={fullName ?? ''} data-src={src ?? ''} />
  ),
}));
const directory = vi.hoisted(() => ({ user: null as User | null, asked: [] as unknown[] }));
vi.mock('@/lib/hooks/use-user-directory-entry', () => ({
  useUserDirectoryEntry: (id: unknown) => {
    directory.asked.push(id);
    return directory.user;
  },
}));

import { AboutCard } from '../about-card';

const OWNER = '507f1f77bcf86cd799439011';
const project = { _id: 'p1', userId: OWNER, name: 'Q4 Planning', members: [] } as unknown as ProjectDetail;

function renderCard(isOwner: boolean) {
  return render(
    <Theme>
      <AboutCard project={project} isOwner={isOwner} />
    </Theme>,
  );
}

beforeEach(() => {
  directory.asked = [];
  directory.user = {
    id: 'u1', userId: OWNER, name: 'Ada Lovelace', email: 'ada@acme.test', hasLoggedIn: true, isActive: true,
    profilePicture: 'data:image/png;base64,AAAA',
  };
});
afterEach(() => cleanup());

describe('AboutCard: owner', () => {
  it('looks the owner up for someone the project was shared with', () => {
    renderCard(false);

    expect(directory.asked).toContain(OWNER);
    expect(screen.getByText('Ada Lovelace')).toBeTruthy();
    expect(screen.getByTestId('owner-avatar').getAttribute('data-src')).toBe('data:image/png;base64,AAAA');
  });

  it('falls back to the email when the owner has no name', () => {
    directory.user = { ...directory.user!, name: '  ' };
    renderCard(false);

    expect(screen.getByText('ada@acme.test')).toBeTruthy();
  });

  it('does not look anyone up for the owner themself', () => {
    renderCard(true);

    expect(directory.asked.every((id) => id === null)).toBe(true);
  });
});
