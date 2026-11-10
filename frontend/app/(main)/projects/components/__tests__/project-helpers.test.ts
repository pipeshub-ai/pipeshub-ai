import { describe, expect, it, vi } from 'vitest';
import testI18n from '@/lib/__tests__/test-i18n';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { projectCardActions } from '../project-list';
import { projectVisibilityLabel } from '../about-card';
import { connectorRowDisplay } from '../connectors-card';
import type { ProjectMember } from '@/chat/project-types';

const t = testI18n.t.bind(testI18n);
const member = (id: string): ProjectMember => ({
  principalType: 'user',
  principalId: id,
  role: 'viewer',
  addedBy: 'owner',
  addedAt: '2026-01-01',
});

describe('projectCardActions', () => {
  it('lets the owner pin, archive and delete an active project', () => {
    expect(projectCardActions({ role: 'owner', isPinned: false, isArchived: false })).toEqual(['pin', 'archive', 'delete']);
  });

  it('lets a viewer pin and leave, but not archive or delete', () => {
    expect(projectCardActions({ role: 'viewer', isPinned: true, isArchived: false })).toEqual(['unpin', 'leave']);
  });

  it('offers unarchive on an archived project only to its owner', () => {
    expect(projectCardActions({ role: 'owner', isPinned: false, isArchived: true })).toEqual(['unarchive', 'delete']);
    expect(projectCardActions({ role: 'editor', isPinned: false, isArchived: true })).toEqual(['leave']);
  });
});

describe('projectVisibilityLabel', () => {
  it('says the project is org-wide when it is', () => {
    expect(projectVisibilityLabel({ visibility: 'org', members: [] }, true, t)).toBe('Visible to your organization');
  });

  it('counts the members of a private project that has some', () => {
    expect(projectVisibilityLabel({ visibility: 'private', members: [member('a'), member('b')] }, true, t)).toBe(
      'Shared with 2 members',
    );
  });

  it('is private to the owner only when nobody else is a member', () => {
    expect(projectVisibilityLabel({ visibility: 'private', members: [] }, true, t)).toBe('Private to you');
  });
});

describe('connectorRowDisplay', () => {
  it('names a URL-named connector by its host and path', () => {
    expect(connectorRowDisplay({ name: 'https://www.example.com/blog/feed.xml', connector: 'RSS' })).toEqual({
      name: 'example.com/blog/feed.xml',
      connector: 'RSS',
    });
  });

  it('falls back to the sub-type, then the name, for the icon', () => {
    expect(connectorRowDisplay({ name: 'Support', connector: '', subType: 'ZENDESK' }).connector).toBe('ZENDESK');
    expect(connectorRowDisplay({ name: 'Zendesk', connector: '' }).connector).toBe('Zendesk');
    expect(connectorRowDisplay({ name: 'Acme docs', connector: '' }).connector).toBe('');
  });
});
