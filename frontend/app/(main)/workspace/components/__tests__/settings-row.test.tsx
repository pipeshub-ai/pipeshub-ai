import { cleanup, render, screen } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { Theme } from '@radix-ui/themes';
import { SettingsRow } from '../settings-row';

const mobile = vi.hoisted(() => ({ value: false }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => mobile.value }));

function renderRow() {
  render(
    <Theme>
      <SettingsRow label="Name" description="Shown to others">
        <input aria-label="field" />
      </SettingsRow>
    </Theme>
  );
  return screen.getByLabelText('field').parentElement as HTMLElement;
}

describe('SettingsRow', () => {
  afterEach(cleanup);
  beforeEach(() => {
    mobile.value = false;
  });

  it('keeps the control in a 38% column on desktop', () => {
    const box = renderRow();
    expect(box.style.flex).toContain('38%');
    expect(box.style.minWidth).toBe('200px');
  });

  it('stacks the label above a full-width control on mobile', () => {
    mobile.value = true;
    const box = renderRow();
    expect(box.style.flex).toBe('');
    expect(box.style.minWidth).toBe('0');
    expect((box.parentElement as HTMLElement).className).toContain('rt-r-fd-column');
  });
});
