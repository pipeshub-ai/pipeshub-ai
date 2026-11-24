/**
 * Plug-ins: the optional parts of the platform, and the only way they attach.
 *
 * Application code announces platform events (`libs/platform-events`) and, for
 * the rare question it must ask, calls `getCapability`. It never imports a
 * plug-in. Which plug-ins a build contains is the list in
 * `src/plugins.generated.ts`: the checked-in default, rewritten by
 * `scripts/generate-plugins.mjs` when an image is built with a different set.
 */
import type { RequestHandler } from 'express';
import { setPlatformEventErrorReporter } from '../platform-events';
import { Logger } from '../services/logger.service';
import { plugins as generatedPlugins } from '../../plugins.generated';
import { getCapability } from './capabilities';
import { host, setPluginFailureReporter } from './registry';
import type { PlatformPlugin, PluginFailure, PluginLoadReport } from './types';

export { PluginHost } from './host';
export { getCapability };
export type {
  DataCollectionCapability,
  PlatformCapabilities,
} from './capabilities';
export type {
  PlatformPlugin,
  PluginContext,
  PluginFailure,
  PluginHook,
  PluginLoadReport,
} from './types';

const logger = Logger.getInstance({ service: 'Plugins' });

const describe = (error: unknown): string =>
  error instanceof Error ? error.message : String(error);

function reportFailure(failure: PluginFailure): void {
  logger.error(`Plug-in ${failure.phase} failed; continuing without it`, {
    plugin: failure.plugin,
    phase: failure.phase,
    error: describe(failure.error),
  });
}

// One line per event name: a listener that fails on every request must not flood the log.
const reportedEvents = new Set<string>();
let loading: Promise<PluginLoadReport> | undefined;

/**
 * Registers the build's plug-ins (or `list`, in tests). Safe to call from
 * every entry point: the list is loaded once and later calls get the same
 * report. A plug-in that fails to register is logged and skipped.
 */
export function loadPlugins(
  list: readonly PlatformPlugin[] = generatedPlugins,
): Promise<PluginLoadReport> {
  if (loading === undefined) {
    setPluginFailureReporter(reportFailure);
    setPlatformEventErrorReporter((name, error) => {
      if (reportedEvents.has(name)) return;
      reportedEvents.add(name);
      logger.warn('A platform event listener failed', {
        event: name,
        error: describe(error),
      });
    });
    loading = host.load(list);
  }
  return loading;
}

/** Middlewares the loaded plug-ins asked for, in registration order. */
export function pluginHttpMiddlewares(): readonly RequestHandler[] {
  return host.httpMiddlewares();
}

export function startPlugins(): Promise<PluginFailure[]> {
  return host.start();
}

export function shutdownPlugins(): Promise<PluginFailure[]> {
  return host.shutdown();
}

/** Unload everything so the next `loadPlugins` starts clean. For tests. */
export function resetPlugins(): void {
  host.reset();
  reportedEvents.clear();
  setPlatformEventErrorReporter(undefined);
  loading = undefined;
}
