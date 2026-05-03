import { apiClient } from './axios-instance';
import axios from 'axios';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL;

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
  const { data } = await axios.get<T>(`${API_BASE_URL}${url}`);
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
