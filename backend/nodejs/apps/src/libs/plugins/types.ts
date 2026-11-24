import type { RequestHandler } from 'express';
import type { onPlatformEvent } from '../platform-events';
import type { PlatformCapabilities } from './capabilities';

export type PluginHook = () => void | Promise<void>;

/** What a plug-in may do while it registers. Nothing here outlives `register`. */
export interface PluginContext {
  /** Same as `onPlatformEvent`; the listener is removed if `register` throws. */
  onEvent: typeof onPlatformEvent;
  /** Adds an Express middleware to every request, after the rate limiter. */
  useHttpMiddleware(middleware: RequestHandler): void;
  /** Runs once the application has configured its routes; in registration order. */
  onStart(hook: PluginHook): void;
  /** Runs when the application stops; in reverse registration order. */
  onShutdown(hook: PluginHook): void;
  /** Answers application code that asks `getCapability(name)`. */
  provideCapability<K extends keyof PlatformCapabilities>(
    name: K,
    capability: PlatformCapabilities[K],
  ): void;
}

/**
 * An optional part of the platform. The application never imports one: the
 * build lists the plug-ins it ships in `src/plugins.generated.ts`, and
 * `loadPlugins()` registers that list.
 */
export interface PlatformPlugin {
  name: string;
  register(ctx: PluginContext): void | Promise<void>;
}

export interface PluginFailure {
  plugin: string;
  /** Which step failed. */
  phase: 'register' | 'start' | 'shutdown';
  error: unknown;
}

export interface PluginLoadReport {
  loaded: string[];
  failed: PluginFailure[];
}
