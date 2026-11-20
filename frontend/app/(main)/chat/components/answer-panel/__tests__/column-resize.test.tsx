import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { AnswerPanelFrame } from '../answer-panel';
import { clampColumnWidth } from '../column-width';

const originalWidth = window.innerWidth;
const setWindow = (width: number) => Object.defineProperty(window, 'innerWidth', { configurable: true, value: width });
const frame = () =>
  render(
    <Theme>
      <AnswerPanelFrame slotId="s1" picker={null} sources="S" citations="C" context="X" outputs="O" />
    </Theme>,
  );
const handle = () => screen.getByRole('separator', { name: 'Resize panel' });
const now = () => Number(handle().getAttribute('aria-valuenow'));

beforeEach(() => {
  localStorage.clear();
  setWindow(1440);
});
afterEach(() => {
  cleanup();
  setWindow(originalWidth);
});

describe('clampColumnWidth', () => {
  it('holds 352 to the smaller of 720 and half the window', () => {
    expect(clampColumnWidth(100, 1440)).toBe(352);
    expect(clampColumnWidth(900, 1920)).toBe(720);
    expect(clampColumnWidth(900, 1024)).toBe(512);
    expect(clampColumnWidth(400, 1440)).toBe(400);
  });
});

describe('the right column resize handle', () => {
  it('starts at 360; arrows move it 16px, Home and End jump, a double-click resets; each is kept for the next chat', () => {
    frame();
    expect(now()).toBe(360);
    fireEvent.keyDown(handle(), { key: 'ArrowLeft' });
    expect(now()).toBe(376);
    expect(localStorage.getItem('chat-right-column-width-px')).toBe('376');
    fireEvent.keyDown(handle(), { key: 'Home' });
    expect(now()).toBe(720);
    fireEvent.keyDown(handle(), { key: 'End' });
    expect(now()).toBe(360);
    fireEvent.keyDown(handle(), { key: 'ArrowRight' });
    expect(now()).toBe(352);
    fireEvent.keyDown(handle(), { key: 'ArrowRight' });
    expect(now()).toBe(352);
    fireEvent.keyDown(handle(), { key: 'ArrowLeft' });
    fireEvent.keyDown(handle(), { key: 'ArrowLeft' });
    cleanup();
    frame();
    expect(now()).toBe(384);
    fireEvent.doubleClick(handle());
    expect(now()).toBe(360);
    expect(localStorage.getItem('chat-right-column-width-px')).toBe('360');
  });

  it('on a narrow window Home stops at half of it', () => {
    setWindow(1024);
    frame();
    fireEvent.keyDown(handle(), { key: 'Home' });
    expect(now()).toBe(512);
  });

  it('the gutter never takes the chat below its minimum', () => {
    frame();
    const gutter = handle().parentElement!;
    expect(gutter.style.maxWidth).toBe('calc(100% - 280px)');
    expect(gutter.style.width).toContain('360px');
  });

  it('where the chat minimum caps the column, it says and keeps the width it really has', () => {
    setWindow(1024);
    const observed: ResizeObserverCallback[] = [];
    vi.stubGlobal(
      'ResizeObserver',
      class {
        constructor(cb: ResizeObserverCallback) {
          observed.push(cb);
        }
        observe() {}
        disconnect() {}
      },
    );
    frame();
    // With the sidebar open the split is 793px: the chat keeps 280, the gutter's insets and hairline take 18.
    const gutter = handle().parentElement!;
    const card = handle().nextElementSibling as HTMLElement;
    Object.defineProperty(gutter.parentElement!, 'clientWidth', { configurable: true, value: 793 });
    gutter.getBoundingClientRect = () => ({ width: 513 }) as DOMRect;
    card.getBoundingClientRect = () => ({ width: 495 }) as DOMRect;
    act(() => observed.forEach((cb) => cb([], {} as ResizeObserver)));
    fireEvent.keyDown(handle(), { key: 'Home' });
    expect(now()).toBe(495);
    expect(handle().getAttribute('aria-valuemax')).toBe('495');
    expect(handle().getAttribute('aria-valuemin')).toBe('352');
    expect(localStorage.getItem('chat-right-column-width-px')).toBe('495');
    // A step from what is shown, not from a width the layout never gave it.
    fireEvent.keyDown(handle(), { key: 'ArrowRight' });
    expect(now()).toBe(479);
    vi.unstubAllGlobals();
  });
});
