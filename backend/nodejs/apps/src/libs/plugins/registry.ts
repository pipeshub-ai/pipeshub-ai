import { PluginHost } from './host';
import type { PluginFailure } from './types';

let reporter: ((failure: PluginFailure) => void) | undefined;

/**
 * The process's plug-in host. It lives apart from `index.ts` so that code
 * which only asks `getCapability` does not import the build's plug-in list.
 */
export const host = new PluginHost((failure) => reporter?.(failure));

export function setPluginFailureReporter(
  next: ((failure: PluginFailure) => void) | undefined,
): void {
  reporter = next;
}
