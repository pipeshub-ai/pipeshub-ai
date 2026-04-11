'use client';

import { useEffect, useState } from 'react';

/**
 * Custom hook for debouncing search queries
 *
 * Delays the update of the returned value until the input has stopped changing
 * for the specified delay period. Useful for reducing API calls during user typing.
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
    // Set up timer to update debounced value after delay
    const timer = setTimeout(() => {
      setDebouncedValue(value);
    }, delay);

    // Clean up timer if value changes before delay expires
    return () => {
      clearTimeout(timer);
    };
  }, [value, delay]);

  return debouncedValue;
}
