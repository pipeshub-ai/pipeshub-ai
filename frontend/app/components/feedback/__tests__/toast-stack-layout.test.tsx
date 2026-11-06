import React from 'react';
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup, act, fireEvent } from '@testing-library/react';

import { ToastContainer } from '../toast-container';
import { useToastStore, toast } from '@/lib/store/toast-store';
import { useToastCornerCard } from '../toast-corner';

vi.mock('@/app/components/theme-provider', () => ({ useThemeAppearance: () => ({ appearance: 'light' }) }));

beforeEach(() => {
  window.matchMedia = vi.fn().mockReturnValue({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() });
});
afterEach(() => {
  cleanup();
  act(() => useToastStore.getState().clearAll());
  document.documentElement.removeAttribute('style');
});

const region = (role: 'status' | 'alert') => screen.getByRole(role) as HTMLElement;

describe('toast host', () => {
  it('keeps both live regions mounted before any toast arrives', () => {
    render(<ToastContainer />);
    expect(region('status').getAttribute('aria-live')).toBe('polite');
    expect(region('status').getAttribute('aria-atomic')).toBe('true');
    expect(region('alert')).toBeTruthy();
    expect(region('status').textContent).toBe('');
  });

  it('puts an error in the alert region and anything else in the status region', async () => {
    render(<ToastContainer />);
    act(() => {
      toast.success('Saved');
    });
    await screen.findByText('Saved');
    expect(region('status').textContent).toContain('Saved');
    act(() => {
      toast.error('Could not save');
    });
    await screen.findByText('Could not save');
    expect(region('alert').textContent).toContain('Could not save');
    expect(region('status').textContent).not.toContain('Saved');
  });

  it('shows one toast at a time: a new one replaces the one showing', async () => {
    render(<ToastContainer />);
    act(() => {
      toast.info('First');
      toast.info('Second');
    });
    await screen.findByText('Second');
    expect(screen.queryByText('First')).toBeNull();
    expect(screen.queryByText(/more/)).toBeNull();
  });

  it('is the toast\'s own width, flush right, and lets clicks through where there is no toast', async () => {
    render(<ToastContainer />);
    act(() => {
      toast.info('Stopping…');
    });
    const title = await screen.findByText('Stopping…');
    const host = region('status');
    expect(host.style.width).toBe('calc(var(--space-9) * 5 + var(--space-7))');
    expect(host.style.justifyContent).toBe('flex-end');
    expect(host.style.pointerEvents).toBe('none');
    const card = host.firstElementChild as HTMLElement;
    expect(card.contains(title)).toBe(true);
    expect(card.style.pointerEvents).toBe('auto');
    expect(card.style.width).toBe('calc(var(--space-9) * 5 + var(--space-7))');
  });

  const CORNER_BOTTOM = 'max(var(--space-4), env(safe-area-inset-bottom, 0px))';
  const CORNER_RIGHT = 'max(var(--space-4), env(safe-area-inset-right, 0px))';

  it('sits in the bottom-right corner, 16 px from the edges, on a laptop and on a phone alike', async () => {
    render(<ToastContainer />);
    act(() => {
      toast.info('Saved');
    });
    await screen.findByText('Saved');
    expect(region('status').dataset.phToastPlacement).toBe('bottom');
    expect([region('status').style.bottom, region('status').style.right, region('status').style.top]).toEqual([
      CORNER_BOTTOM,
      CORNER_RIGHT,
      '',
    ]);
    cleanup();
    act(() => useToastStore.getState().clearAll());

    // A phone's width matches every max-width query: the toast stays where it is.
    window.matchMedia = vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() });
    render(<ToastContainer />);
    act(() => {
      toast.error('Could not save');
    });
    await screen.findByText('Could not save');
    expect(region('alert').dataset.phToastPlacement).toBe('bottom');
    expect([region('alert').style.bottom, region('alert').style.right, region('alert').style.top]).toEqual([
      CORNER_BOTTOM,
      CORNER_RIGHT,
      '',
    ]);
  });

  it('puts a toast whose caller asks for the top edge 16 px from the top and right edges', async () => {
    render(<ToastContainer />);
    act(() => {
      toast.error('Could not invite', { placement: 'top' });
    });
    await screen.findByText('Could not invite');
    expect(region('alert').dataset.phToastPlacement).toBe('top');
    expect([region('alert').style.top, region('alert').style.right, region('alert').style.bottom]).toEqual([
      'max(var(--space-4), env(safe-area-inset-top, 0px))',
      CORNER_RIGHT,
      '',
    ]);
  });

  it('marks only the region holding the toast with its placement', async () => {
    render(<ToastContainer />);
    act(() => {
      toast.info('Below');
    });
    await screen.findByText('Below');
    expect(document.querySelectorAll('[data-ph-toast-placement]')).toHaveLength(1);
  });

  describe('stays in the corner whatever the page shows', () => {
    beforeEach(() => {
      Object.defineProperty(window, 'innerWidth', { value: 1440, configurable: true });
      Object.defineProperty(window, 'innerHeight', { value: 900, configurable: true });
      vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(360);
      vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(88);
    });
    afterEach(() => {
      document.querySelectorAll('[data-test-page-part]').forEach((el) => el.remove());
      vi.restoreAllMocks();
    });

    /** A part of the page, laid out where `rect` says. */
    const pagePart = (attribute: string, rect: Partial<DOMRect>) => {
      const el = document.createElement('div');
      el.setAttribute(attribute, '');
      el.setAttribute('data-test-page-part', '');
      el.getBoundingClientRect = () => ({ width: (rect.right ?? 0) - (rect.left ?? 0), ...rect }) as DOMRect;
      const button = document.createElement('button');
      el.appendChild(button);
      document.body.appendChild(el);
      return el;
    };

    const expectInCorner = () => {
      expect([region('status').style.bottom, region('status').style.right]).toEqual([CORNER_BOTTOM, CORNER_RIGHT]);
      expect(document.documentElement.getAttribute('style') ?? '').toBe('');
    };

    it('with a settings panel open along the right edge', async () => {
      // The workspace drawer at 1440 x 900: 600 px wide, 10 px from the right edge.
      pagePart('data-ph-workspace-drawer-panel', { left: 830, right: 1430, top: 0, bottom: 900 });
      render(<ToastContainer />);
      act(() => {
        toast.success('Invite sent');
      });
      await screen.findByText('Invite sent');
      act(() => {
        window.dispatchEvent(new Event('resize'));
      });
      expectInCorner();
    });

    it('with the chat composer reaching under the corner ("Answer ready" over the model picker and Send)', async () => {
      // The live chat at 1440 x 900: composer x 437-1237, y 768-862; the toast card is 360 x 88.
      pagePart('data-composer-shell', { left: 437, right: 1237, top: 768, bottom: 862 });
      render(<ToastContainer />);
      act(() => {
        toast.info('Answer ready', { description: 'DF-test chat' });
      });
      await screen.findByText('Answer ready');
      act(() => {
        window.dispatchEvent(new Event('resize'));
      });
      expectInCorner();
    });
  });

  it("sits above a toast card another host put in the corner, and comes back down when it goes", async () => {
    vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(88);
    function CornerCard() {
      const [card, setCard] = React.useState<HTMLDivElement | null>(null);
      useToastCornerCard(card);
      return <div ref={setCard}>Uploading 2 files</div>;
    }
    try {
      const page = render(
        <>
          <ToastContainer />
          <CornerCard />
        </>,
      );
      act(() => {
        toast.success('Copied link');
      });
      await screen.findByText('Copied link');
      // The card's 88 px and one --space-3 gap above it.
      expect(region('status').style.bottom).toBe(`calc(${CORNER_BOTTOM} + 88px + 1 * var(--space-3))`);
      expect(region('status').style.right).toBe(CORNER_RIGHT);
      page.rerender(<ToastContainer />);
      expect(region('status').style.bottom).toBe(CORNER_BOTTOM);
    } finally {
      vi.restoreAllMocks();
    }
  });

  it('gives a success toast a close button that closes it', async () => {
    render(<ToastContainer />);
    act(() => {
      toast.success('You left the chat');
    });
    await screen.findByText('You left the chat');
    const close = screen.getByRole('button', { name: 'Close' });
    // Reachable by keyboard, and focus on it holds the toast.
    expect(close.tabIndex).toBe(0);
    // A 44px tap area on touch screens.
    expect(close.classList.contains('touch-hit')).toBe(true);
    act(() => close.focus());
    expect(useToastStore.getState().isHovered).toBe(true);
    fireEvent.click(close);
    expect(useToastStore.getState().toasts.every((t) => t.isExiting)).toBe(true);
  });

  it('gives a loading toast no close button, and the success it settles into one', async () => {
    render(<ToastContainer />);
    let id = '';
    act(() => {
      id = toast.loading('Saving…');
    });
    await screen.findByText('Saving…');
    expect(screen.queryByRole('button', { name: 'Close' })).toBeNull();
    act(() => toast.update(id, { variant: 'success', title: 'Saved' }));
    await screen.findByText('Saved');
    expect(screen.getByRole('button', { name: 'Close' })).toBeTruthy();
  });

  it('gives no close button to a toast whose caller passed false', async () => {
    render(<ToastContainer />);
    act(() => {
      toast.info('Copied link');
    });
    await screen.findByText('Copied link');
    expect(screen.getByRole('button', { name: 'Close' })).toBeTruthy();
    act(() => {
      toast.info('Link copied again', { showCloseButton: false });
    });
    await screen.findByText('Link copied again');
    expect(screen.queryByRole('button', { name: 'Close' })).toBeNull();
  });

  it('names an error toast\'s close button, hides its glyph, and pauses while it has focus', async () => {
    render(<ToastContainer />);
    act(() => {
      toast.error('Could not save');
    });
    await screen.findByText('Could not save');
    const close = screen.getByRole('button', { name: 'Close' });
    expect(close.querySelector('[aria-hidden="true"]')).toBeTruthy();
    act(() => close.focus());
    expect(useToastStore.getState().isHovered).toBe(true);
  });

  it('pauses its clock while pointed at and resumes with the time it had left', async () => {
    vi.useFakeTimers();
    try {
      render(<ToastContainer />);
      act(() => {
        toast.success('Saved');
      });
      act(() => {
        vi.advanceTimersByTime(2000);
      });
      fireEvent.mouseEnter(region('status'));
      act(() => {
        vi.advanceTimersByTime(5000);
      });
      expect(useToastStore.getState().toasts).toHaveLength(1);
      fireEvent.mouseLeave(region('status'));
      act(() => {
        vi.advanceTimersByTime(1900);
      });
      expect(useToastStore.getState().toasts[0]?.isExiting).toBeFalsy();
      act(() => {
        vi.advanceTimersByTime(200 + 300);
      });
      expect(useToastStore.getState().toasts).toHaveLength(0);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('pressing a toast action', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  const entries = (title: string) => useToastStore.getState().toasts.filter((t) => t.title === title);
  const shown = () => useToastStore.getState().toasts.find((t) => !t.isExiting && !t.isWaiting)?.title;

  it('closes a toast whose action does not close it, and the waiting toast comes up', () => {
    const onClick = vi.fn();
    render(<ToastContainer />);
    act(() => {
      toast.success('Chat archived', { action: { label: 'Undo', onClick } });
      toast.info('Copied link');
    });
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    expect(onClick).toHaveBeenCalledTimes(1);
    expect(entries('Chat archived')[0]?.isExiting).toBe(true);
    expect(shown()).toBe('Copied link');
    act(() => {
      vi.advanceTimersByTime(240);
    });
    expect(entries('Chat archived')).toHaveLength(0);
    expect(screen.getByText('Copied link')).toBeTruthy();
  });

  it('removes a toast whose action closes it itself only once', () => {
    let undoId = '';
    const onClick = vi.fn(() => toast.dismiss(undoId));
    render(<ToastContainer />);
    act(() => {
      undoId = toast.success('Chat archived', { action: { label: 'Undo', onClick } });
      toast.info('First waiting');
      toast.info('Second waiting');
    });
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    expect(onClick).toHaveBeenCalledTimes(1);
    // One removal: one waiting toast came up, the next still waits its turn.
    expect(shown()).toBe('First waiting');
    expect(entries('First waiting')).toHaveLength(1);
    expect(entries('Second waiting')[0]?.isWaiting).toBe(true);
    act(() => {
      vi.advanceTimersByTime(240);
    });
    expect(entries('Chat archived')).toHaveLength(0);
  });
});

describe('the pause belongs to the card on screen', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  const shown = () => useToastStore.getState().toasts.find((t) => !t.isExiting && !t.isWaiting)?.title;
  const advance = (ms: number) =>
    act(() => {
      vi.advanceTimersByTime(ms);
    });
  // @testing-library/user-event is not a dependency of this repo, so the press is real focus (or a
  // hover) followed by the click that Enter or a mouse press produces on a button.
  const press = (button: HTMLElement, by: 'keyboard' | 'mouse') => {
    if (by === 'keyboard') act(() => button.focus());
    else fireEvent.mouseEnter(button.closest('[data-ph-toast-region]') as HTMLElement);
    fireEvent.click(button);
  };

  for (const by of ['keyboard', 'mouse'] as const) {
    for (const selfDismiss of [false, true]) {
      it(`Undo pressed by ${by}${selfDismiss ? ', its onClick closing it,' : ''}: the queue runs on its own clock`, () => {
        let undoId = '';
        const onClick = vi.fn(() => {
          if (selfDismiss) toast.dismiss(undoId);
        });
        render(<ToastContainer />);
        act(() => {
          undoId = toast.success('Chat archived', { action: { label: 'Undo', onClick } });
          toast.info('First waiting');
          toast.info('Second waiting');
        });
        press(screen.getByRole('button', { name: 'Undo' }), by);
        expect(onClick).toHaveBeenCalledTimes(1);
        expect(shown()).toBe('First waiting');
        expect(useToastStore.getState().isHovered).toBe(false);
        advance(3999);
        expect(shown()).toBe('First waiting');
        advance(1);
        expect(shown()).toBe('Second waiting');
        advance(240);
        expect(useToastStore.getState().toasts.some((t) => t.title === 'First waiting')).toBe(false);
      });
    }
  }

  it('closing a focused error toast gives the waiting toast its full lifetime', () => {
    render(<ToastContainer />);
    act(() => {
      toast.success('Chat archived', { action: { label: 'Undo', onClick: () => {} } });
      toast.error('Could not load chats');
      toast.info('Copied link');
    });
    press(screen.getByRole('button', { name: 'Undo' }), 'mouse');
    expect(shown()).toBe('Could not load chats');
    press(screen.getByRole('button', { name: 'Close' }), 'keyboard');
    expect(shown()).toBe('Copied link');
    expect(useToastStore.getState().isHovered).toBe(false);
    advance(3999);
    expect(shown()).toBe('Copied link');
    advance(1);
    expect(shown()).toBeUndefined();
  });

  it('a toast that replaces a hovered one closes on its own lifetime, and pauses again on the next pointer move', () => {
    render(<ToastContainer />);
    act(() => {
      toast.info('First');
    });
    fireEvent.mouseEnter(screen.getByRole('status'));
    expect(useToastStore.getState().isHovered).toBe(true);
    act(() => {
      toast.info('Second');
    });
    expect(useToastStore.getState().isHovered).toBe(false);
    advance(3999);
    expect(shown()).toBe('Second');
    fireEvent.mouseMove(screen.getByText('Second'));
    expect(useToastStore.getState().isHovered).toBe(true);
    advance(10_000);
    expect(shown()).toBe('Second');
    fireEvent.mouseLeave(screen.getByRole('status'));
    advance(1);
    expect(shown()).toBeUndefined();
  });
});
