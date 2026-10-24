import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, cleanup } from '@testing-library/react';

const panel = vi.hoisted(() => ({ icon: undefined as React.ReactNode }));

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));
vi.mock('../forms/provider-config-form', () => ({ default: () => null }));
vi.mock('../../../components/workspace-right-panel', () => ({
  WorkspaceRightPanel: ({ icon }: { icon?: React.ReactNode }) => {
    panel.icon = icon;
    return <div data-testid="icon">{typeof icon === 'string' ? null : icon}</div>;
  },
}));

import { ConfigurePanel } from '../configure-panel';
import type { ConfigurableMethod } from '../../types';

function open(method: ConfigurableMethod) {
  return render(<ConfigurePanel open method={method} onClose={vi.fn()} onSaveSuccess={vi.fn()} />);
}

afterEach(() => {
  cleanup();
  panel.icon = undefined;
});

describe('ConfigurePanel header icon', () => {
  it('shows the Google logo for Google, not an icon-font name the font has no glyph for', () => {
    const { getByTestId } = open('google');

    expect(typeof panel.icon).not.toBe('string');
    expect(getByTestId('icon').querySelector('img')?.getAttribute('src')).toBe('/icons/auth-config/google.svg');
  });

  it('keeps the icon-font name for Microsoft', () => {
    open('microsoft');

    expect(panel.icon).toBe('window');
  });
});
