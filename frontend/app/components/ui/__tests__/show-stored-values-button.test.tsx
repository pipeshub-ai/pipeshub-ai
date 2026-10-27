import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, cleanup, fireEvent, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

const device = { isMobile: false };
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => device.isMobile }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { ShowStoredValuesButton } from '../show-stored-values-button';

function renderButton(props: Partial<React.ComponentProps<typeof ShowStoredValuesButton>> = {}) {
  const onClick = props.onClick ?? vi.fn();
  render(
    <Theme>
      <ShowStoredValuesButton {...props} onClick={onClick} />
    </Theme>,
  );
  return { onClick, button: screen.getByRole('button', { name: 'form.showStoredValues' }) };
}

describe('ShowStoredValuesButton', () => {
  afterEach(() => {
    cleanup();
    device.isMobile = false;
  });

  it('asks for the stored values when clicked', () => {
    const { onClick, button } = renderButton();

    fireEvent.click(button);

    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it('does not submit the surrounding form', () => {
    const { button } = renderButton();

    expect(button.getAttribute('type')).toBe('button');
  });

  it('cannot be pressed while disabled', () => {
    const { onClick, button } = renderButton({ disabled: true });

    fireEvent.click(button);

    expect(onClick).not.toHaveBeenCalled();
  });

  it('has a 44px touch target on mobile', () => {
    device.isMobile = true;
    const { button } = renderButton();

    expect(button.style.minWidth).toBe('44px');
    expect(button.style.minHeight).toBe('44px');
  });

  it('keeps the compact size on desktop', () => {
    const { button } = renderButton();

    expect(button.style.minWidth).toBe('');
    expect(button.style.minHeight).toBe('');
  });
});
