import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/chat/project-api', () => ({ ProjectApi: { create: vi.fn() } }));

import { CreateProjectDialog } from '../create-project-dialog';

afterEach(cleanup);

describe('CreateProjectDialog', () => {
  it('labels every field and counts characters against the limits', () => {
    render(
      <Theme>
        <CreateProjectDialog open onOpenChange={vi.fn()} onCreated={vi.fn()} />
      </Theme>,
    );
    expect(screen.getByLabelText('Name')).toBeTruthy();
    const description = screen.getByLabelText('Description');
    expect(screen.getByLabelText('Instructions')).toBeTruthy();
    expect(screen.getByText('0 / 1000')).toBeTruthy();
    expect(screen.getByText('0 / 8000')).toBeTruthy();

    fireEvent.change(description, { target: { value: 'hello' } });
    expect(screen.getByText('5 / 1000')).toBeTruthy();
  });

  it('keeps room for the name error so the dialog does not jump', () => {
    render(
      <Theme>
        <CreateProjectDialog open onOpenChange={vi.fn()} onCreated={vi.fn()} />
      </Theme>,
    );
    const name = screen.getByLabelText('Name');
    const [errorId, countId] = (name.getAttribute('aria-describedby') ?? '').split(' ');
    const errorSlot = document.getElementById(errorId);
    expect(errorSlot?.style.visibility).toBe('hidden');
    expect(document.getElementById(countId)?.textContent).toContain('100');

    fireEvent.click(screen.getByRole('button', { name: 'Create project' }));
    expect(screen.getByRole('alert').textContent).toBe('Name is required');
    expect(name.getAttribute('aria-invalid')).toBe('true');
  });
});
