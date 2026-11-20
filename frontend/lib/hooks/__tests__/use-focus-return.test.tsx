import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useFocusReturn } from '../use-focus-return';

// jsdom lays nothing out, so every element reports no boxes. Treat an element
// as rendered when it is in the page and not marked hidden.
beforeEach(() => {
  vi.spyOn(Element.prototype, 'getClientRects').mockImplementation(function (this: Element) {
    const rendered = this.isConnected && !this.closest('[hidden]');
    return (rendered ? [{}] : []) as unknown as DOMRectList;
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  document.body.innerHTML = '';
});

const cancelable = () => new Event('focus-scope', { cancelable: true });

function button(label: string, parent: HTMLElement = document.body): HTMLButtonElement {
  const el = document.createElement('button');
  el.type = 'button';
  el.textContent = label;
  parent.appendChild(el);
  return el;
}

/** Simulates the dialog taking focus while it is open. */
function focusInsideDialog() {
  button('Cancel').focus();
}

describe('useFocusReturn', () => {
  it('returns focus to the opener', () => {
    const opener = button('Remove');
    opener.focus();
    const { result } = renderHook(() => useFocusReturn());

    result.current.onOpenAutoFocus(cancelable());
    focusInsideDialog();
    const close = cancelable();
    result.current.onCloseAutoFocus(close);

    expect(document.activeElement).toBe(opener);
    expect(close.defaultPrevented).toBe(true);
  });

  it('returns focus to the menu trigger when opened from a menu item', () => {
    const trigger = button('Actions');
    trigger.id = 'row-actions-trigger';
    const menu = document.createElement('div');
    menu.setAttribute('role', 'menu');
    menu.setAttribute('aria-labelledby', 'row-actions-trigger');
    document.body.appendChild(menu);
    const item = document.createElement('div');
    item.setAttribute('role', 'menuitem');
    item.tabIndex = -1;
    menu.appendChild(item);
    item.focus();
    const { result } = renderHook(() => useFocusReturn());

    result.current.onOpenAutoFocus(cancelable());
    menu.remove();
    focusInsideDialog();
    result.current.onCloseAutoFocus(cancelable());

    expect(document.activeElement).toBe(trigger);
  });

  it('focuses the fallback when the opener is gone', () => {
    const opener = button('Delete row');
    const fallback = button('Add record');
    opener.focus();
    const { result } = renderHook(() => useFocusReturn({ fallback: () => fallback }));

    result.current.onOpenAutoFocus(cancelable());
    opener.remove();
    focusInsideDialog();
    const close = cancelable();
    result.current.onCloseAutoFocus(close);

    expect(document.activeElement).toBe(fallback);
    expect(close.defaultPrevented).toBe(true);
  });

  it.each([
    ['disabled', (el: HTMLButtonElement) => (el.disabled = true)],
    ['inside an inert region', (el: HTMLButtonElement) => {
      const wrap = document.createElement('div');
      wrap.setAttribute('inert', '');
      el.replaceWith(wrap);
      wrap.appendChild(el);
    }],
    ['not rendered', (el: HTMLButtonElement) => (el.hidden = true)],
  ])('skips an opener that is %s', (_label, spoil) => {
    const opener = button('Remove');
    const fallback = button('Back to list');
    opener.focus();
    const { result } = renderHook(() => useFocusReturn({ fallback: () => fallback }));

    result.current.onOpenAutoFocus(cancelable());
    spoil(opener);
    focusInsideDialog();
    result.current.onCloseAutoFocus(cancelable());

    expect(document.activeElement).toBe(fallback);
  });

  it('leaves Radix its default when there is no usable target and no fallback', () => {
    const opener = button('Remove');
    opener.focus();
    const { result } = renderHook(() => useFocusReturn());

    result.current.onOpenAutoFocus(cancelable());
    opener.remove();
    focusInsideDialog();
    const inside = document.activeElement;
    const close = cancelable();
    result.current.onCloseAutoFocus(close);

    expect(close.defaultPrevented).toBe(false);
    expect(document.activeElement).toBe(inside);
  });

  it('leaves Radix its default when the fallback is also unusable', () => {
    const opener = button('Remove');
    opener.focus();
    const { result } = renderHook(() => useFocusReturn({ fallback: () => null }));

    result.current.onOpenAutoFocus(cancelable());
    opener.remove();
    const close = cancelable();
    result.current.onCloseAutoFocus(close);

    expect(close.defaultPrevented).toBe(false);
  });

  it('never prevents the dialog from taking focus on open', () => {
    button('Remove').focus();
    const { result } = renderHook(() => useFocusReturn());
    const open = cancelable();
    result.current.onOpenAutoFocus(open);
    expect(open.defaultPrevented).toBe(false);
  });

  it('keeps both handlers stable across renders, and reads the latest fallback', () => {
    const first = button('First');
    const second = button('Second');
    const opener = button('Remove');
    const { result, rerender } = renderHook(({ target }) => useFocusReturn({ fallback: () => target }), {
      initialProps: { target: first },
    });
    const { onOpenAutoFocus, onCloseAutoFocus } = result.current;
    rerender({ target: second });
    expect(result.current.onOpenAutoFocus).toBe(onOpenAutoFocus);
    expect(result.current.onCloseAutoFocus).toBe(onCloseAutoFocus);

    opener.focus();
    onOpenAutoFocus(cancelable());
    opener.remove();
    onCloseAutoFocus(cancelable());
    expect(document.activeElement).toBe(second);
  });
});
