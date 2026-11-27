/**
 * The bar over selected words (SelectionActions), in a chat of your own and in a group chat: a divider between each
 * two buttons it shows, none at an edge and never two in a row, all drawn the same.
 */
import React from 'react';
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { SelectionActions } from '../selection-actions';

afterEach(cleanup);

const noop = () => {};

describe('SelectionActions', () => {
  it.each([
    ['Reply | Ask PipesHub | Copy', { onReply: noop, onAsk: noop }],
    ['Reply | Copy', { onReply: noop }],
    ['Ask PipesHub | Copy', { onAsk: noop }],
    ['Copy', {}],
  ])('%s', (expected, props) => {
    render(<Theme><SelectionActions text="the runbook is stale" {...props} /></Theme>);
    const bar = screen.getByRole('group', { name: 'Selected text' });
    // Left to right: each button by its label, each divider as "|".
    const layout = Array.from(bar.children, (el) =>
      el.classList.contains('rt-Separator') ? '|' : el.querySelector('.rt-Text')?.textContent,
    );
    expect(layout.join(' ')).toBe(expected);
    const dividers = Array.from(bar.querySelectorAll('.rt-Separator'), (el) => el.outerHTML);
    expect(new Set(dividers).size).toBeLessThanOrEqual(1);
  });
});
