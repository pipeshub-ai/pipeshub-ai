import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, cleanup, screen, fireEvent } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { SendImagesRow, parseMaxImages } from '../send-images-row';

function row(props: Partial<React.ComponentProps<typeof SendImagesRow>> = {}) {
  const onToggle = vi.fn();
  const onMaxImagesCommit = vi.fn();
  render(
    <Theme>
      <SendImagesRow enabled maxImages={3} onToggle={onToggle} onMaxImagesCommit={onMaxImagesCommit} {...props} />
    </Theme>,
  );
  return { onToggle, onMaxImagesCommit, input: screen.queryByRole('spinbutton') as HTMLInputElement | null };
}

afterEach(cleanup);

describe('SendImagesRow — "Send images to LLM"', () => {
  it('the switch is named by its label and reports a change', () => {
    const { onToggle } = row({ enabled: false });
    fireEvent.click(screen.getByRole('switch', { name: 'workspace.webSearch.sendImages' }));
    expect(onToggle).toHaveBeenCalledWith(true);
  });

  it('the count is asked for only while images are sent', () => {
    expect(row({ enabled: false }).input).toBeNull();
    cleanup();
    expect(row({ enabled: true }).input?.value).toBe('3');
    expect(screen.getByText('workspace.webSearch.imagesCostWarning')).toBeTruthy();
  });

  it('a new whole number is saved when the field is left', () => {
    const { input, onMaxImagesCommit } = row();
    fireEvent.change(input!, { target: { value: '12' } });
    fireEvent.blur(input!);
    expect(onMaxImagesCommit).toHaveBeenCalledWith(12);
  });

  it('the same number is not saved again', () => {
    const { input, onMaxImagesCommit } = row();
    fireEvent.blur(input!);
    expect(onMaxImagesCommit).not.toHaveBeenCalled();
  });

  it.each(['0', '51', '2.5', '', 'abc'])('%j is refused, said so, and the saved count is put back', (value) => {
    const { input, onMaxImagesCommit } = row();
    fireEvent.change(input!, { target: { value } });
    fireEvent.blur(input!);
    expect(onMaxImagesCommit).not.toHaveBeenCalled();
    expect(input!.value).toBe('3');
    expect(screen.getByRole('alert').textContent).toBe('workspace.webSearch.maxImagesInvalid');
    expect(input!.getAttribute('aria-invalid')).toBe('true');
  });

  it('nothing can be changed while disabled (loading, saving or inherited)', () => {
    const { input } = row({ disabled: true });
    expect((screen.getByRole('switch') as HTMLButtonElement).disabled).toBe(true);
    expect(input!.disabled).toBe(true);
  });
});

describe('parseMaxImages', () => {
  it.each([
    ['1', 1], ['50', 50], [' 7 ', 7], ['0', null], ['51', null], ['500', null], ['2.5', null], ['', null], ['x', null],
  ])('%j → %j', (text, expected) => {
    expect(parseMaxImages(text)).toBe(expected);
  });
});
