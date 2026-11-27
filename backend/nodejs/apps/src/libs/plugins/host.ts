import type { RequestHandler } from 'express';
import { onPlatformEvent } from '../platform-events';
import type { PlatformCapabilities } from './capabilities';
import type {
  PlatformPlugin,
  PluginContext,
  PluginFailure,
  PluginHook,
  PluginLoadReport,
  PluginRouter,
} from './types';

interface NamedHook {
  plugin: string;
  hook: PluginHook;
}

type Report = (failure: PluginFailure) => void;

/**
 * Holds what the loaded plug-ins registered. A plug-in must never be able to
 * stop the application: a failure in `register`, a start hook or a shutdown
 * hook is reported and the next plug-in or hook still runs.
 */
export class PluginHost {
  private readonly names: string[] = [];
  private readonly middlewares: RequestHandler[] = [];
  private readonly routers: PluginRouter[] = [];
  private readonly startHooks: NamedHook[] = [];
  private readonly shutdownHooks: NamedHook[] = [];
  private readonly capabilities = new Map<string, unknown>();
  private readonly unsubscribers: (() => void)[] = [];

  constructor(private readonly report: Report = () => undefined) {}

  /**
   * Registers each plug-in in list order. Whatever a plug-in asked for takes
   * effect only once its `register` has returned, so one that throws half-way
   * leaves nothing behind.
   */
  async load(plugins: readonly PlatformPlugin[]): Promise<PluginLoadReport> {
    const result: PluginLoadReport = { loaded: [], failed: [] };
    for (const plugin of plugins) {
      const subscriptions: (() => () => void)[] = [];
      const middlewares: RequestHandler[] = [];
      const routers: PluginRouter[] = [];
      const startHooks: PluginHook[] = [];
      const shutdownHooks: PluginHook[] = [];
      const capabilities: [string, unknown][] = [];
      let open = true;
      const ctx: PluginContext = {
        onEvent: ((name: never, handler: never): (() => void) => {
          let stop: (() => void) | undefined;
          let cancelled = false;
          const subscribe = (): (() => void) => {
            stop = cancelled ? undefined : onPlatformEvent(name, handler);
            return () => stop?.();
          };
          if (open) subscriptions.push(subscribe);
          else this.unsubscribers.push(subscribe());
          return () => {
            cancelled = true;
            stop?.();
          };
        }) as PluginContext['onEvent'],
        useHttpMiddleware: (middleware) => {
          middlewares.push(middleware);
        },
        useRouter: (path, router) => {
          routers.push({ path, router });
        },
        onStart: (hook) => {
          startHooks.push(hook);
        },
        onShutdown: (hook) => {
          shutdownHooks.push(hook);
        },
        provideCapability: (name, capability) => {
          capabilities.push([name, capability]);
        },
      };
      try {
        await plugin.register(ctx);
      } catch (error) {
        const failure: PluginFailure = {
          plugin: plugin.name,
          phase: 'register',
          error,
        };
        result.failed.push(failure);
        this.safeReport(failure);
        continue;
      } finally {
        open = false;
      }
      for (const subscribe of subscriptions) {
        this.unsubscribers.push(subscribe());
      }
      this.middlewares.push(...middlewares);
      this.routers.push(...routers);
      for (const hook of startHooks) {
        this.startHooks.push({ plugin: plugin.name, hook });
      }
      for (const hook of shutdownHooks) {
        this.shutdownHooks.push({ plugin: plugin.name, hook });
      }
      for (const [name, capability] of capabilities) {
        this.capabilities.set(name, capability);
      }
      this.names.push(plugin.name);
      result.loaded.push(plugin.name);
    }
    return result;
  }

  loadedPlugins(): readonly string[] {
    return this.names;
  }

  httpMiddlewares(): readonly RequestHandler[] {
    return this.middlewares;
  }

  /** Routers the loaded plug-ins asked for, in registration order. */
  httpRouters(): readonly PluginRouter[] {
    return this.routers;
  }

  getCapability<K extends keyof PlatformCapabilities>(
    name: K,
  ): PlatformCapabilities[K] | undefined {
    return this.capabilities.get(name) as PlatformCapabilities[K] | undefined;
  }

  /** Start hooks, in the order they were registered. */
  async start(): Promise<PluginFailure[]> {
    return this.runHooks(this.startHooks, 'start');
  }

  /** Shutdown hooks, last registered first. */
  async shutdown(): Promise<PluginFailure[]> {
    return this.runHooks([...this.shutdownHooks].reverse(), 'shutdown');
  }

  /** Forget every plug-in and remove its event listeners. For tests. */
  reset(): void {
    for (const stop of this.unsubscribers.splice(0)) stop();
    this.names.length = 0;
    this.middlewares.length = 0;
    this.routers.length = 0;
    this.startHooks.length = 0;
    this.shutdownHooks.length = 0;
    this.capabilities.clear();
  }

  private async runHooks(
    hooks: readonly NamedHook[],
    phase: 'start' | 'shutdown',
  ): Promise<PluginFailure[]> {
    const failed: PluginFailure[] = [];
    for (const { plugin, hook } of hooks) {
      try {
        await hook();
      } catch (error) {
        const failure: PluginFailure = { plugin, phase, error };
        failed.push(failure);
        this.safeReport(failure);
      }
    }
    return failed;
  }

  private safeReport(failure: PluginFailure): void {
    try {
      this.report(failure);
    } catch {
      // Reporting is best effort; the failure is still in the returned list.
    }
  }
}
