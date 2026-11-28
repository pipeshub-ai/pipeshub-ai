'use client';

import { useCallback, useMemo, useRef, useState } from 'react';

/*
 * A field is usually left by pressing something else, often a button below it. An error line that shows on blur
 * pushes that button down between mouse-down and mouse-up, and the press is lost. So an error asked for during a
 * press waits until the press is over: right after its click, or a moment after the release when no click comes
 * (a touch's mouse events can come a little after it). The keyboard is never held.
 */
const PRESS_SETTLE_MS = 400;
let pressing = false;
let settleTimer: ReturnType<typeof setTimeout> | null = null;
const afterPress: Array<() => void> = [];

function endPress(): void {
  pressing = false;
  if (settleTimer) clearTimeout(settleTimer);
  settleTimer = null;
  afterPress.splice(0).forEach((run) => run());
}

if (typeof window !== 'undefined') {
  window.addEventListener(
    'pointerdown',
    () => {
      pressing = true;
      if (settleTimer) clearTimeout(settleTimer);
      settleTimer = null;
    },
    true,
  );
  const release = () => {
    if (!pressing) return;
    if (settleTimer) clearTimeout(settleTimer);
    settleTimer = setTimeout(endPress, PRESS_SETTLE_MS);
  };
  window.addEventListener('pointerup', release, true);
  window.addEventListener('pointercancel', release, true);
  // Captured, so this runs before the click's own handlers; the timeout runs after them.
  window.addEventListener(
    'click',
    () => {
      if (pressing) setTimeout(endPress, 0);
    },
    true,
  );
}

/** Runs `show` now, or once the press in progress is over, its click handled first. */
export function whenPressEnds(show: () => void): void {
  if (pressing) afterPress.push(show);
  else show();
}

/**
 * The one pattern for create and edit forms: a field is marked required, its
 * rule is checked as the person types, the error appears under the field once
 * they leave it (after the press that left it, see {@link whenPressEnds}, or
 * after a send that the server refused), and the primary button stays
 * disabled until every rule passes.
 *
 * `rules` maps each field to its current problem, or undefined when it is fine.
 * Compute it from the form values on every render; it is the source of truth
 * for {@link isValid}. A server answer that names a field is kept in
 * `serverErrors` and wins over the local rule until that field changes.
 */
export function useFormValidation<F extends string>(rules: Partial<Record<F, string | undefined>>) {
  const [touched, setTouched] = useState<ReadonlySet<string>>(() => new Set());
  const [serverErrors, setServerErrors] = useState<Partial<Record<F, string>>>({});
  // A reset (Cancel, a closed form) while an error waits for a press to end drops that error.
  const generation = useRef(0);

  const touch = useCallback((field: F) => {
    const asked = generation.current;
    whenPressEnds(() => {
      if (generation.current !== asked) return;
      setTouched((prev) => (prev.has(field) ? prev : new Set(prev).add(field)));
    });
  }, []);

  /** After a refused send: show every listed field's problem at once. */
  const touchAll = useCallback((fields: readonly F[]) => {
    setTouched((prev) => new Set([...prev, ...fields]));
  }, []);

  /** Call from the field's onChange: the server's message is about the old value. */
  const clearServerError = useCallback((field: F) => {
    setServerErrors((prev) => {
      if (!(field in prev)) return prev;
      const next = { ...prev };
      delete next[field];
      return next;
    });
  }, []);

  /**
   * Keeps the field errors a refused save came with. Returns true when at least
   * one belongs to a field in `fields`, so the caller knows not to toast it again.
   */
  const applyServerErrors = useCallback(
    (errors: Record<string, string>, fields: readonly F[]) => {
      const mine: Partial<Record<F, string>> = {};
      for (const field of fields) {
        if (errors[field]) mine[field] = errors[field];
      }
      const any = Object.keys(mine).length > 0;
      if (any) setServerErrors((prev) => ({ ...prev, ...mine }));
      return any;
    },
    [],
  );

  const reset = useCallback(() => {
    generation.current += 1;
    setTouched(new Set());
    setServerErrors({});
  }, []);

  const isValid = useMemo(
    () => Object.values(rules).every((problem) => !problem),
    [rules],
  );

  /** What to show under the field now. */
  const errorFor = (field: F): string | undefined =>
    serverErrors[field] ?? (touched.has(field) ? (rules[field] as string | undefined) : undefined);

  return { errorFor, touch, touchAll, isValid, applyServerErrors, clearServerError, reset };
}
