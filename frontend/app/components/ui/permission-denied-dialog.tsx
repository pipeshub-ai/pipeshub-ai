'use client';

export function PermissionDeniedDialog() {
  return null;
}

export function usePermissionDeniedDialog() {
  return {
    openDenied: () => {},
    guard: <A extends unknown[], R>(_allowed: boolean, fn: (...args: A) => R) => fn,
    dialog: null,
  };
}
