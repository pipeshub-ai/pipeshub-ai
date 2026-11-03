import React from 'react';
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import en from '@/lib/i18n/locales/en-US.json';
import { ErrorBoundary, ErrorFallback } from '../error-boundary';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => {
      let cur: unknown = en;
      for (const part of key.split('.')) {
        if (typeof cur !== 'object' || cur === null || !(part in cur)) return key;
        cur = (cur as Record<string, unknown>)[part];
      }
      return typeof cur === 'string' ? cur : key;
    },
  }),
}));

let shouldThrow = true;
function Bomb() {
  if (shouldThrow) throw new Error('boom');
  return <div>child ok</div>;
}

const TITLE = en.common.errorBoundary.title;
const RETRY = en.common.errorBoundary.retry;

function wrap(ui: React.ReactNode) {
  return <Theme>{ui}</Theme>;
}

beforeEach(() => {
  shouldThrow = true;
  vi.spyOn(console, 'error').mockImplementation(() => {});
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('ErrorBoundary', () => {
  it('renders the fallback title when a child throws and reports the error', () => {
    const onError = vi.fn();
    render(wrap(<ErrorBoundary onError={onError}><Bomb /></ErrorBoundary>));
    expect(screen.getByText(TITLE)).toBeTruthy();
    expect(onError).toHaveBeenCalledTimes(1);
  });

  it('re-renders the child after "Try again"', () => {
    render(wrap(<ErrorBoundary><Bomb /></ErrorBoundary>));
    shouldThrow = false;
    fireEvent.click(screen.getByText(RETRY));
    expect(screen.getByText('child ok')).toBeTruthy();
  });

  it('clears the error when a resetKeys entry changes', () => {
    const { rerender } = render(wrap(<ErrorBoundary resetKeys={['a']}><Bomb /></ErrorBoundary>));
    expect(screen.getByText(TITLE)).toBeTruthy();
    shouldThrow = false;
    rerender(wrap(<ErrorBoundary resetKeys={['b']}><Bomb /></ErrorBoundary>));
    expect(screen.getByText('child ok')).toBeTruthy();
  });

  it('compact fallback omits the title and the chat link', () => {
    render(wrap(<ErrorFallback error={new Error('x')} reset={() => {}} compact />));
    expect(screen.queryByText(TITLE)).toBeNull();
    expect(screen.queryByText(en.common.errorBoundary.goToChat)).toBeNull();
    expect(screen.getByText(RETRY)).toBeTruthy();
  });

  it('full fallback fills the viewport, nested fallback fills its container', () => {
    const { container, rerender } = render(wrap(<ErrorFallback error={new Error('x')} reset={() => {}} />));
    expect(container.querySelector('[role="alert"]')?.classList.contains('viewport-height')).toBe(true);
    rerender(wrap(<ErrorFallback error={new Error('x')} reset={() => {}} nested />));
    const alert = container.querySelector('[role="alert"]') as HTMLElement;
    expect(alert.classList.contains('viewport-height')).toBe(false);
    expect(alert.style.height).toBe('100%');
  });
});
