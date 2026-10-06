import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

import { DraftShareLine } from '../draft-share-line';
import { draftShareBody, useDraftShareStore } from '../../../draft-share-store';

beforeEach(() => useDraftShareStore.getState().clear());
afterEach(cleanup);

const mount = (onEdit = vi.fn()) => {
  render(
    <Theme>
      <DraftShareLine onEdit={onEdit} />
    </Theme>,
  );
  return onEdit;
};

describe('the "will be shared with" line', () => {
  it('renders nothing until someone is picked', () => {
    mount();
    expect(screen.queryByTestId('draft-share-line')).toBeNull();
  });

  it('names each person and team with their level, and each can be removed', () => {
    useDraftShareStore.getState().add([
      { type: 'user', id: 'u1', name: 'Alex', level: 'write' },
      { type: 'team', id: 't1', name: 'Design team', level: 'read' },
    ]);
    mount();
    expect(screen.getByTestId('draft-share-line').textContent).toContain('Will be shared with');
    expect(screen.getAllByTestId('draft-share-entry').map((e) => e.textContent)).toEqual([
      expect.stringContaining('Alex (Can continue)'),
      expect.stringContaining('Design team (Can view)'),
    ]);

    fireEvent.click(within(screen.getAllByTestId('draft-share-entry')[0]).getByRole('button', { name: 'Remove Alex' }));
    expect(useDraftShareStore.getState().principals.map((p) => p.id)).toEqual(['t1']);
    fireEvent.click(screen.getByRole('button', { name: 'Remove Design team' }));
    expect(screen.queryByTestId('draft-share-line')).toBeNull();
    expect(useDraftShareStore.getState().message).toBe('');
  });

  it('clicking the line reopens the drawer', () => {
    useDraftShareStore.getState().add([{ type: 'user', id: 'u1', name: 'Alex', level: 'read' }]);
    const onEdit = mount();
    fireEvent.click(screen.getByTestId('draft-share-edit'));
    expect(onEdit).toHaveBeenCalledTimes(1);
  });
});

describe('the draft store', () => {
  it('adding the same person again replaces them; the message survives until the draft empties', () => {
    const s = useDraftShareStore.getState();
    s.add([{ type: 'user', id: 'u1', name: 'Alex', level: 'read' }], { message: ' hello ' });
    s.add([{ type: 'user', id: 'u1', name: 'Alex', level: 'write' }]);
    expect(useDraftShareStore.getState().principals).toEqual([{ type: 'user', id: 'u1', name: 'Alex', level: 'write' }]);
    expect(useDraftShareStore.getState().message).toBe('hello');
    expect(draftShareBody()).toEqual({ collaborators: [{ principalType: 'user', principalId: 'u1', accessLevel: 'write' }], note: 'hello' });
  });

  it('a user and a team with the same id are different principals', () => {
    const s = useDraftShareStore.getState();
    s.add([{ type: 'user', id: 'x', name: 'U', level: 'read' }, { type: 'team', id: 'x', name: 'T', level: 'read' }]);
    s.remove('user', 'x');
    expect(useDraftShareStore.getState().principals.map((p) => p.type)).toEqual(['team']);
  });

  it('org-wide confirmation is carried to the body once confirmed', () => {
    useDraftShareStore.getState().add([{ type: 'team', id: 'all_org', name: 'Everyone', level: 'read' }], { confirmOrgWide: true });
    expect(draftShareBody()).toMatchObject({ confirmOrgWide: true });
  });

  it('is undefined with nobody picked', () => {
    expect(draftShareBody()).toBeUndefined();
  });
});
