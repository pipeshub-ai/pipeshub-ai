import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, cleanup, fireEvent, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

import { CreateFolderDialog, type CreateFolderDialogProps } from '../create-folder-dialog';

afterEach(cleanup);

const TAKEN = 'A collection with this name already exists';

function setup(taken: Set<string>) {
  const validateName = vi.fn((name: string) => (taken.has(name) ? TAKEN : null));
  const props: CreateFolderDialogProps = {
    open: true,
    onOpenChange: () => {},
    onSubmit: vi.fn(),
    isCollection: true,
    validateName,
  };
  const view = render(
    <Theme>
      <CreateFolderDialog {...props} />
    </Theme>,
  );
  const rerender = (over: Partial<CreateFolderDialogProps>) =>
    view.rerender(
      <Theme>
        <CreateFolderDialog {...props} {...over} />
      </Theme>,
    );
  fireEvent.change(screen.getAllByRole('textbox')[0], { target: { value: 'Team' } });
  return { validateName, rerender };
}

describe('CreateFolderDialog — name check', () => {
  it('flags a name that is already taken while it is typed', () => {
    setup(new Set(['Team']));
    expect(screen.getByRole('alert').textContent).toBe(TAKEN);
  });

  it('does not call the new collection a duplicate of itself while it is created', () => {
    const taken = new Set<string>();
    const { rerender } = setup(taken);
    rerender({ isCreating: true });
    // The page refreshes the collection list before it closes the dialog.
    taken.add('Team');
    rerender({ isCreating: true });
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('does not check the name while the dialog closes', () => {
    const taken = new Set<string>();
    const { validateName, rerender } = setup(taken);
    taken.add('Team');
    validateName.mockClear();
    rerender({ open: false });
    expect(validateName).not.toHaveBeenCalled();
  });

  it('checks the name again when creating fails and the dialog stays open', () => {
    const taken = new Set<string>();
    const { rerender } = setup(taken);
    rerender({ isCreating: true });
    taken.add('Team');
    rerender({ isCreating: false });
    expect(screen.getByRole('alert').textContent).toBe(TAKEN);
  });
});
