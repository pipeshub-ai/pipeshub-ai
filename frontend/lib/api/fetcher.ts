import { apiClient } from './axios-instance';
import axios from 'axios';
import { getApiBaseUrl } from '@/lib/utils/api-base-url';

/**
 * SWR fetcher for authenticated requests
 * Uses the apiClient which includes auth token and error handling
 */
export async function axiosFetcher<T>(url: string): Promise<T> {
  const { data } = await apiClient.get<T>(url);
  return data;
}

/**
 * SWR fetcher for unauthenticated/public requests
 * Does not include auth token
 */
export async function publicFetcher<T>(url: string): Promise<T> {
  const { data } = await axios.get<T>(`${getApiBaseUrl()}${url}`);
  return data;
}

/**
 * SWR fetcher with custom config
 * Allows passing additional axios config options
 */
export async function configuredFetcher<T>(
  url: string,
  config?: { params?: Record<string, unknown> }
): Promise<T> {
  const { data } = await apiClient.get<T>(url, config);
  return data;
}
