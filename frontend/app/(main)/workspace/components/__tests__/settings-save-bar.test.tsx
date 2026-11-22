import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, screen } from '@testing-library/react';
import { renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import { SettingsSaveBar } from '../settings-save-bar';

afterEach(cleanup);

describe('SettingsSaveBar', () => {
  it('takes its buttons out of the tab order while hidden', () => {
    renderInTheme(<SettingsSaveBar visible={false} onDiscard={vi.fn()} onSave={vi.fn()} />);

    expect(screen.getByRole('button', { name: 'Save' }).closest('[inert]')).not.toBeNull();
  });

  it('is reachable when shown', () => {
    renderInTheme(<SettingsSaveBar visible onDiscard={vi.fn()} onSave={vi.fn()} />);

    expect(screen.getByRole('button', { name: 'Save' }).closest('[inert]')).toBeNull();
  });
});
