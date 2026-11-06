'use client';

import { useCallback, useMemo, useState } from 'react';

/**
 * The one pattern for create and edit forms: a field is marked required, its
 * rule is checked as the person types, the error appears under the field once
 * they leave it (or after a send that the server refused), and the primary
 * button stays disabled until every rule passes.
 *
 * `rules` maps each field to its current problem, or undefined when it is fine.
 * Compute it from the form values on every render; it is the source of truth
 * for {@link isValid}. A server answer that names a field is kept in
 * `serverErrors` and wins over the local rule until that field changes.
 */
export function useFormValidation<F extends string>(rules: Partial<Record<F, string | undefined>>) {
  const [touched, setTouched] = useState<ReadonlySet<string>>(() => new Set());
  const [serverErrors, setServerErrors] = useState<Partial<Record<F, string>>>({});

  const touch = useCallback((field: F) => {
    setTouched((prev) => (prev.has(field) ? prev : new Set(prev).add(field)));
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
