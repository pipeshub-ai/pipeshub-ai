import React, { useRef } from 'react';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { scrollEdgeFade, scrollEdgesOf, useScrollEdges } from '../use-scroll-edges';

afterEach(() => cleanup());

function Row() {
  const ref = useRef<HTMLDivElement>(null);
  const edges = useScrollEdges(ref);
  return <div ref={ref} data-testid="row" data-start={String(edges.start)} data-end={String(edges.end)} />;
}

function box(el: HTMLElement, scrollWidth: number, clientWidth: number) {
  Object.defineProperty(el, 'scrollWidth', { configurable: true, value: scrollWidth });
  Object.defineProperty(el, 'clientWidth', { configurable: true, value: clientWidth });
}

describe('useScrollEdges', () => {
  it('SCROLLEDGE-1: a row wider than its box hides content at the end, then at the start once scrolled to the end', () => {
    expect(scrollEdgesOf({ scrollLeft: 0, clientWidth: 374, scrollWidth: 699 })).toEqual({ start: false, end: true });
    expect(scrollEdgesOf({ scrollLeft: 325, clientWidth: 374, scrollWidth: 699 })).toEqual({ start: true, end: false });
    expect(scrollEdgesOf({ scrollLeft: 0, clientWidth: 374, scrollWidth: 374 })).toEqual({ start: false, end: false });
  });

  it('SCROLLEDGE-2: the hook follows the scroller as it scrolls', () => {
    render(<Row />);
    const row = screen.getByTestId('row');
    box(row, 699, 374);
    act(() => {
      fireEvent.scroll(row);
    });
    expect(row.dataset.end).toBe('true');
    act(() => {
      row.scrollLeft = 325;
      fireEvent.scroll(row);
    });
    expect([row.dataset.start, row.dataset.end]).toEqual(['true', 'false']);
  });

  it('SCROLLEDGE-3: only a side that hides content fades', () => {
    expect(scrollEdgeFade({ start: false, end: false })).toEqual({});
    expect(scrollEdgeFade({ start: false, end: true }).maskImage).toBe('linear-gradient(to right, #000, #000 calc(100% - 32px), transparent)');
    expect(scrollEdgeFade({ start: true, end: false }).maskImage).toBe('linear-gradient(to right, transparent, #000 32px, #000)');
  });

  it('SCROLLEDGE-4: no component keeps its own copy of the scroll-edge state (R31AXEU-FU-1)', () => {
    const frontend = path.resolve(__dirname, '../../..');
    const files = (rel: string): string[] =>
      statSync(path.join(frontend, rel)).isFile()
        ? /\.tsx?$/.test(rel) && !/\.(test|spec)\./.test(rel) ? [rel] : []
        : readdirSync(path.join(frontend, rel)).flatMap((n) => (n === '__tests__' || n === 'node_modules' ? [] : files(path.join(rel, n))));
    const copies = ['app', 'lib', 'components']
      .flatMap(files)
      .filter((file) => file !== path.join('lib', 'hooks', 'use-scroll-edges.ts'))
      .filter((file) => /scrollLeft \+ [\w.]*clientWidth <|setCanScroll(?:Left|Right)\b/.test(readFileSync(path.join(frontend, file), 'utf8')));
    expect(copies).toEqual([]);
  });

  it('SCROLLEDGE-5: a scroller that mounts later, when `remount` changes, is still watched', () => {
    function Later({ show }: { show: boolean }) {
      const ref = useRef<HTMLDivElement>(null);
      const edges = useScrollEdges(ref, show);
      return show ? <div ref={ref} data-testid="later" data-end={String(edges.end)} /> : <span data-testid="none" data-end={String(edges.end)} />;
    }
    const view = render(<Later show={false} />);
    const proto = HTMLElement.prototype;
    const sw = Object.getOwnPropertyDescriptor(proto, 'scrollWidth');
    const cw = Object.getOwnPropertyDescriptor(proto, 'clientWidth');
    Object.defineProperty(proto, 'scrollWidth', { configurable: true, get: () => 400 });
    Object.defineProperty(proto, 'clientWidth', { configurable: true, get: () => 100 });
    try {
      view.rerender(<Later show />);
      expect(screen.getByTestId('later').dataset.end).toBe('true');
    } finally {
      if (sw) Object.defineProperty(proto, 'scrollWidth', sw);
      if (cw) Object.defineProperty(proto, 'clientWidth', cw);
    }
  });
});
