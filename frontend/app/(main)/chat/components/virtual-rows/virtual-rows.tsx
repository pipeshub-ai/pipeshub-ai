'use client';

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
} from 'react';
import { measuredWindow, rowOffsets } from './measured-window';

/**
 * Lists longer than this render only the rows near the viewport (EV-15, PF-17). Shorter ones render every row,
 * exactly as before: find-in-page, focus and the enter animation are untouched for everyday runs.
 */
export const VIRTUALIZE_AFTER = 150;
/** Pixels rendered beyond each edge of the viewport, so a fast scroll does not show blank space. */
const OVERSCAN_PX = 800;

/**
 * Where a row's own state lives (a card the person opened): a virtualized row unmounts when scrolled away, so its
 * state is kept by the outermost list, keyed by the row's path through nested lists.
 */
interface RowStateScope {
  store: Map<string, unknown>;
  path: string;
}
const RowStateContext = createContext<RowStateScope | null>(null);

/**
 * `useState` that survives the row being scrolled out of a virtualized list and back. Outside a list it is
 * plain `useState`.
 */
export function useRowState<T>(name: string, initial: T): [T, React.Dispatch<React.SetStateAction<T>>] {
  const scope = useContext(RowStateContext);
  const key = scope ? `${scope.path}#${name}` : '';
  const [value, setValue] = useState<T>(() => (scope && scope.store.has(key) ? (scope.store.get(key) as T) : initial));
  const set = useCallback<React.Dispatch<React.SetStateAction<T>>>(
    (next) => {
      setValue((previous) => {
        const resolved = typeof next === 'function' ? (next as (prev: T) => T)(previous) : next;
        if (scope) scope.store.set(key, resolved);
        return resolved;
      });
    },
    [scope, key],
  );
  return [value, set];
}

/** A key seen before gets the count of its earlier copies: a row's measured size and state must be its own. */
function uniqueKeys(keys: string[]): string[] {
  const seen = new Map<string, number>();
  return keys.map((key) => {
    const copies = seen.get(key) ?? 0;
    seen.set(key, copies + 1);
    return copies === 0 ? key : `${key}~${copies}`;
  });
}

function scrollParent(element: HTMLElement | null): HTMLElement | null {
  for (let node = element?.parentElement ?? null; node; node = node.parentElement) {
    const { overflowY } = window.getComputedStyle(node);
    if (overflowY === 'auto' || overflowY === 'scroll' || overflowY === 'overlay') return node;
  }
  return null;
}

interface Viewport {
  top: number;
  bottom: number;
}

/** The viewport over `list`, relative to the list's own top. */
function viewportOf(list: HTMLElement, scroller: HTMLElement | null): Viewport {
  const listTop = list.getBoundingClientRect().top;
  if (!scroller) {
    const top = -listTop;
    return { top, bottom: top + window.innerHeight };
  }
  const top = scroller.getBoundingClientRect().top - listTop;
  return { top, bottom: top + scroller.clientHeight };
}

export interface VirtualRowsProps<T> {
  items: readonly T[];
  getKey: (item: T, index: number) => string;
  /** A row's height before it is measured. */
  estimateSize: (item: T, index: number) => number;
  renderRow: (item: T, index: number) => React.ReactNode;
  /** Space between rows, as CSS (`var(--space-2)`). */
  gap?: string;
  threshold?: number;
  'data-testid'?: string;
}

/**
 * A column of rows that varies in height. Past `threshold` rows only those near the scroll container's viewport
 * are in the DOM; each is measured once rendered (ResizeObserver) and an estimate stands in for the rest. A row
 * above the viewport that changes height moves the scroll position by as much, so what the person reads stays put.
 */
export function VirtualRows<T>({
  items,
  getKey,
  estimateSize,
  renderRow,
  gap,
  threshold = VIRTUALIZE_AFTER,
  'data-testid': testId,
}: VirtualRowsProps<T>) {
  const parent = useContext(RowStateContext);
  const [ownStore] = useState(() => new Map<string, unknown>());
  const store = parent?.store ?? ownStore;
  const basePath = parent?.path ?? '';
  const virtual = items.length > threshold;

  const listRef = useRef<HTMLDivElement>(null);
  const scrollerRef = useRef<HTMLElement | null>(null);
  // Measured heights by row key: mutated by the row observer, which then bumps `measured` to re-read them.
  const [sizes] = useState(() => new Map<string, number>());
  const [viewport, setViewport] = useState<Viewport>({ top: 0, bottom: 0 });
  const [measured, bumpMeasured] = useReducer((n: number) => n + 1, 0);
  const frame = useRef<number | null>(null);

  const keys = useMemo(() => uniqueKeys(items.map((item, index) => getKey(item, index))), [items, getKey]);
  const offsets = useMemo(
    () => (virtual ? rowOffsets(items.length, (i) => sizes.get(keys[i]) ?? estimateSize(items[i], i)) : null),
    // `measured` changes when `sizes` (mutated in place) does.
    [virtual, items, keys, estimateSize, sizes, measured],
  );
  const keyIndex = useMemo(() => new Map(keys.map((key, index) => [key, index])), [keys]);
  // What the row observer reads when it fires, after the render that set them.
  const offsetsRef = useRef(offsets);
  const keyIndexRef = useRef(keyIndex);
  useLayoutEffect(() => {
    offsetsRef.current = offsets;
    keyIndexRef.current = keyIndex;
  }, [offsets, keyIndex]);

  const readViewport = useCallback(() => {
    frame.current = null;
    const list = listRef.current;
    if (!list) return;
    const next = viewportOf(list, scrollerRef.current);
    setViewport((previous) => (previous.top === next.top && previous.bottom === next.bottom ? previous : next));
  }, []);
  const scheduleRead = useCallback(() => {
    if (frame.current === null) frame.current = window.requestAnimationFrame(readViewport);
  }, [readViewport]);

  useLayoutEffect(() => {
    if (!virtual) return undefined;
    const scroller = scrollParent(listRef.current);
    scrollerRef.current = scroller;
    readViewport();
    const target: HTMLElement | Window = scroller ?? window;
    target.addEventListener('scroll', scheduleRead, { passive: true });
    window.addEventListener('resize', scheduleRead);
    let observer: ResizeObserver | null = null;
    if (scroller && typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(scheduleRead);
      observer.observe(scroller);
    }
    return () => {
      target.removeEventListener('scroll', scheduleRead);
      window.removeEventListener('resize', scheduleRead);
      observer?.disconnect();
      if (frame.current !== null) window.cancelAnimationFrame(frame.current);
      frame.current = null;
    };
  }, [virtual, readViewport, scheduleRead]);

  const rowObserver = useRef<ResizeObserver | null>(null);
  useEffect(() => {
    if (!virtual || typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver((entries) => {
      let changed = false;
      let shift = 0;
      const view = scrollerRef.current;
      const list = listRef.current;
      const viewTop = list ? viewportOf(list, view).top : 0;
      for (const entry of entries) {
        const element = entry.target as HTMLElement;
        const key = element.dataset.vrow;
        if (key === undefined || !element.isConnected) continue;
        const height = element.getBoundingClientRect().height;
        const index = keyIndexRef.current.get(key);
        const before = sizes.get(key) ?? (index !== undefined && offsetsRef.current
          ? offsetsRef.current[index + 1] - offsetsRef.current[index]
          : height);
        if (Math.abs(before - height) < 0.5) {
          sizes.set(key, height);
          continue;
        }
        sizes.set(key, height);
        changed = true;
        if (index !== undefined && offsetsRef.current && offsetsRef.current[index] < viewTop) shift += height - before;
      }
      if (shift !== 0 && view) view.scrollTop += shift;
      if (changed) bumpMeasured();
    });
    rowObserver.current = observer;
    return () => {
      observer.disconnect();
      rowObserver.current = null;
    };
  }, [virtual, sizes]);

  const measureRow = useCallback((element: HTMLDivElement | null) => {
    if (element) rowObserver.current?.observe(element);
  }, []);

  const row = (index: number, last: boolean) => {
    const key = keys[index];
    return (
      <RowStateContext.Provider key={key} value={{ store, path: `${basePath}/${key}` }}>
        {virtual ? (
          <div ref={measureRow} data-vrow={key} style={{ paddingBottom: last ? 0 : gap }}>
            {renderRow(items[index], index)}
          </div>
        ) : (
          renderRow(items[index], index)
        )}
      </RowStateContext.Provider>
    );
  };

  if (!virtual || !offsets) {
    return (
      <div ref={listRef} data-testid={testId} style={{ display: 'flex', flexDirection: 'column', gap }}>
        {items.map((_, index) => row(index, index === items.length - 1))}
      </div>
    );
  }

  const window_ = measuredWindow(offsets, viewport.top, viewport.bottom, OVERSCAN_PX);
  const rendered: React.ReactNode[] = [];
  for (let index = window_.start; index < window_.end; index += 1) rendered.push(row(index, index === items.length - 1));
  return (
    <div ref={listRef} data-testid={testId} data-virtualized="true" style={{ overflowAnchor: 'none' }}>
      <div aria-hidden="true" style={{ height: window_.padTop }} />
      {rendered}
      <div aria-hidden="true" style={{ height: window_.padBottom }} />
    </div>
  );
}
