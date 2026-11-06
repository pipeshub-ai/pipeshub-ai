import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import '@/lib/__tests__/test-i18n';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('../api', () => ({
  PatApi: {
    getScopes: vi.fn().mockResolvedValue({
      scopes: [{ name: 'kb:read', description: 'Read', category: 'Knowledge Base' }],
    }),
    createToken: vi.fn(),
  },
}));

import { CreatePatPanel } from '../components/create-pat-panel';

afterEach(() => cleanup());

function renderPanel() {
  return render(
    <Theme>
      <CreatePatPanel open onOpenChange={() => {}} />
    </Theme>,
  );
}

const createButton = () => screen.getByRole('button', { name: 'Create token' }) as HTMLButtonElement;

describe('CreatePatPanel validation (one pattern for create forms)', () => {
  it('marks the name required and keeps Create disabled until it has text', async () => {
    renderPanel();
    const name = (await screen.findByLabelText(/^Token name/)) as HTMLInputElement;
    await waitFor(() => expect(screen.getByText('kb:read')).toBeTruthy());
    expect(name.getAttribute('aria-required')).toBe('true');
    expect(createButton().disabled).toBe(true);
    fireEvent.change(name, { target: { value: 'CI token' } });
    expect(createButton().disabled).toBe(false);
  });

  it('says why under the field once it is left empty, and marks it invalid', async () => {
    renderPanel();
    const name = await screen.findByLabelText(/^Token name/);
    fireEvent.focus(name);
    fireEvent.blur(name);
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toBeTruthy();
    expect(name.getAttribute('aria-invalid')).toBe('true');
    expect(name.getAttribute('aria-describedby')).toContain(alert.id);
  });
});
