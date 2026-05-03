'use client';

import { useEffect, useState } from 'react';

/**
 * Custom hook for debouncing search queries
 *
 * Delays the update of the returned value until the input has stopped changing
 * for the specified delay period. Useful for reducing API calls during user typing.
 * Empty string updates apply immediately so clears/resets do not wait for the delay.
 *
 * @param value The search query to debounce
 * @param delay Delay in milliseconds (default: 300ms)
 * @returns The debounced value
 *
 * @example
 * const debouncedSearchQuery = useDebouncedSearch(searchQuery, 300);
 *
 * useEffect(() => {
 *   // This will only run 300ms after user stops typing
 *   fetchData(debouncedSearchQuery);
 * }, [debouncedSearchQuery]);
 */
export function useDebouncedSearch(value: string, delay: number = 300): string {
  const [debouncedValue, setDebouncedValue] = useState(value);

  useEffect(() => {
    if (value === '') {
      setDebouncedValue('');
      return;
    }
    const timer = setTimeout(() => {
      setDebouncedValue(value);
    }, delay);
    return () => {
      clearTimeout(timer);
    };
  }, [value, delay]);

  return debouncedValue;
}
