import { expect } from 'chai';
import express, { Router } from 'express';
import {
  emitPlatformEvent,
  resetPlatformEvents,
} from '../../../src/libs/platform-events';
import {
  PluginHost,
  getCapability,
  loadPlugins,
  pluginHttpMiddlewares,
  pluginRouters,
  resetPlugins,
  shutdownPlugins,
  startPlugins,
} from '../../../src/libs/plugins';
import type {
  PlatformPlugin,
  PluginFailure,
} from '../../../src/libs/plugins';
import { plugins as generatedPlugins } from '../../../src/plugins.generated';

const sweep = (): void =>
  emitPlatformEvent('docstore.ttl.swept', { target: 't', removed: 1 });

describe('libs/plugins', () => {
  beforeEach(() => {
    resetPlugins();
    resetPlatformEvents();
  });
  afterEach(() => {
    resetPlugins();
    resetPlatformEvents();
  });

  describe('PluginHost', () => {
    it('registers plug-ins in list order and reports them loaded', async () => {
      const order: string[] = [];
      const host = new PluginHost();
      const report = await host.load([
        { name: 'a', register: () => void order.push('a') },
        {
          name: 'b',
          register: async () => {
            await Promise.resolve();
            order.push('b');
          },
        },
        { name: 'c', register: () => void order.push('c') },
      ]);
      expect(order).to.deep.equal(['a', 'b', 'c']);
      expect(report).to.deep.equal({ loaded: ['a', 'b', 'c'], failed: [] });
      expect(host.loadedPlugins()).to.deep.equal(['a', 'b', 'c']);
    });

    it('reports a plug-in that throws in register and still loads the others', async () => {
      const reported: PluginFailure[] = [];
      const boom = new Error('boom');
      const host = new PluginHost((f) => reported.push(f));
      let started = 0;
      const report = await host.load([
        {
          name: 'broken',
          register: () => {
            throw boom;
          },
        },
        {
          name: 'rejects',
          register: () => Promise.reject(new Error('async')),
        },
        { name: 'fine', register: (ctx) => ctx.onStart(() => void started++) },
      ]);
      expect(report.loaded).to.deep.equal(['fine']);
      expect(report.failed.map((f) => [f.plugin, f.phase])).to.deep.equal([
        ['broken', 'register'],
        ['rejects', 'register'],
      ]);
      expect(report.failed[0]!.error).to.equal(boom);
      expect(reported).to.deep.equal(report.failed);
      await host.start();
      expect(started).to.equal(1);
    });

    it('leaves nothing behind from a plug-in that throws half-way through register', async () => {
      const host = new PluginHost();
      let events = 0;
      let hooks = 0;
      await host.load([
        {
          name: 'half',
          register: (ctx) => {
            ctx.onEvent('docstore.ttl.swept', () => void events++);
            ctx.useHttpMiddleware((_req, _res, next) => next());
            ctx.useRouter('/half', Router());
            ctx.onStart(() => void hooks++);
            ctx.onShutdown(() => void hooks++);
            ctx.provideCapability('data-collection', { isActive: () => true });
            throw new Error('late');
          },
        },
      ]);
      sweep();
      await host.start();
      await host.shutdown();
      expect(events).to.equal(0);
      expect(hooks).to.equal(0);
      expect(host.httpMiddlewares()).to.have.length(0);
      expect(host.httpRouters()).to.have.length(0);
      expect(host.getCapability('data-collection')).to.equal(undefined);
    });

    it('survives a failure reporter that throws', async () => {
      const host = new PluginHost(() => {
        throw new Error('reporter');
      });
      const report = await host.load([
        {
          name: 'broken',
          register: () => {
            throw new Error('boom');
          },
        },
      ]);
      expect(report.failed).to.have.length(1);
    });

    it('delivers platform events to ctx.onEvent listeners until they unsubscribe', async () => {
      const host = new PluginHost();
      let calls = 0;
      let off: () => void = () => undefined;
      await host.load([
        {
          name: 'listener',
          register: (ctx) => {
            off = ctx.onEvent('docstore.ttl.swept', () => void calls++);
          },
        },
      ]);
      sweep();
      off();
      sweep();
      expect(calls).to.equal(1);
    });

    it('never subscribes a listener that was cancelled during register', async () => {
      const host = new PluginHost();
      let calls = 0;
      await host.load([
        {
          name: 'changed-its-mind',
          register: (ctx) => {
            ctx.onEvent('docstore.ttl.swept', () => void calls++)();
          },
        },
      ]);
      sweep();
      expect(calls).to.equal(0);
    });

    it('subscribes at once when a plug-in keeps the context and listens later', async () => {
      const host = new PluginHost();
      let calls = 0;
      let later: (() => void) | undefined;
      await host.load([
        {
          name: 'late',
          register: (ctx) => {
            later = () => ctx.onEvent('docstore.ttl.swept', () => void calls++);
          },
        },
      ]);
      later!();
      sweep();
      expect(calls).to.equal(1);
      host.reset();
      sweep();
      expect(calls).to.equal(1);
    });

    it('collects HTTP middlewares in registration order', async () => {
      const host = new PluginHost();
      const first = (_q: unknown, _s: unknown, next: () => void): void => next();
      const second = (_q: unknown, _s: unknown, next: () => void): void => next();
      await host.load([
        { name: 'a', register: (ctx) => ctx.useHttpMiddleware(first) },
        { name: 'b', register: (ctx) => ctx.useHttpMiddleware(second) },
      ]);
      expect(host.httpMiddlewares()).to.deep.equal([first, second]);
    });

    it('collects routers with their paths in registration order', async () => {
      const host = new PluginHost();
      const first = Router();
      const second = Router();
      await host.load([
        { name: 'a', register: (ctx) => ctx.useRouter('/api/v1/a', first) },
        { name: 'b', register: (ctx) => ctx.useRouter('/api/v1/b', second) },
      ]);
      expect(host.httpRouters()).to.deep.equal([
        { path: '/api/v1/a', router: first },
        { path: '/api/v1/b', router: second },
      ]);
    });

    it('serves routes a plug-in adds to its router from a start hook', async () => {
      const host = new PluginHost();
      await host.load([
        {
          name: 'late',
          register: (ctx) => {
            const router = Router();
            ctx.useRouter('/late', router);
            ctx.onStart(() => {
              router.get('/ping', (_req, res) => {
                res.json({ ok: true });
              });
            });
          },
        },
      ]);
      const app = express();
      for (const { path, router } of host.httpRouters()) app.use(path, router);
      const server = app.listen(0);
      try {
        const { port } = server.address() as { port: number };
        const url = `http://127.0.0.1:${port}/late/ping`;
        expect((await fetch(url)).status).to.equal(404);
        await host.start();
        const answer = await fetch(url);
        expect(answer.status).to.equal(200);
        expect(await answer.json()).to.deep.equal({ ok: true });
      } finally {
        await new Promise((resolve) => server.close(resolve));
      }
    });

    it('runs start hooks in order and shutdown hooks in reverse', async () => {
      const host = new PluginHost();
      const order: string[] = [];
      const plugin = (name: string): PlatformPlugin => ({
        name,
        register: (ctx) => {
          ctx.onStart(() => void order.push(`start ${name}-1`));
          ctx.onStart(async () => {
            await Promise.resolve();
            order.push(`start ${name}-2`);
          });
          ctx.onShutdown(() => void order.push(`stop ${name}-1`));
          ctx.onShutdown(async () => {
            await Promise.resolve();
            order.push(`stop ${name}-2`);
          });
        },
      });
      await host.load([plugin('a'), plugin('b')]);
      expect(await host.start()).to.deep.equal([]);
      expect(await host.shutdown()).to.deep.equal([]);
      expect(order).to.deep.equal([
        'start a-1',
        'start a-2',
        'start b-1',
        'start b-2',
        'stop b-2',
        'stop b-1',
        'stop a-2',
        'stop a-1',
      ]);
    });

    it('reports a failing hook and still runs the ones after it', async () => {
      const reported: PluginFailure[] = [];
      const host = new PluginHost((f) => reported.push(f));
      const order: string[] = [];
      await host.load([
        {
          name: 'a',
          register: (ctx) => {
            ctx.onStart(() => {
              throw new Error('start failed');
            });
            ctx.onShutdown(() => void order.push('stop a'));
          },
        },
        {
          name: 'b',
          register: (ctx) => {
            ctx.onStart(() => void order.push('start b'));
            ctx.onShutdown(() => Promise.reject(new Error('stop failed')));
          },
        },
      ]);
      const startFailures = await host.start();
      const stopFailures = await host.shutdown();
      expect(order).to.deep.equal(['start b', 'stop a']);
      expect(startFailures.map((f) => [f.plugin, f.phase])).to.deep.equal([
        ['a', 'start'],
      ]);
      expect(stopFailures.map((f) => [f.plugin, f.phase])).to.deep.equal([
        ['b', 'shutdown'],
      ]);
      expect(reported).to.have.length(2);
    });

    it('answers getCapability with what a plug-in provided, and undefined otherwise', async () => {
      const host = new PluginHost();
      expect(host.getCapability('data-collection')).to.equal(undefined);
      const capability = { isActive: (): boolean => true };
      await host.load([
        {
          name: 'a',
          register: (ctx) => ctx.provideCapability('data-collection', capability),
        },
      ]);
      expect(host.getCapability('data-collection')).to.equal(capability);
    });

    it('reset removes listeners, hooks, middlewares, routers and capabilities', async () => {
      const host = new PluginHost();
      let calls = 0;
      await host.load([
        {
          name: 'a',
          register: (ctx) => {
            ctx.onEvent('docstore.ttl.swept', () => void calls++);
            ctx.useHttpMiddleware((_q, _s, next) => next());
            ctx.useRouter('/a', Router());
            ctx.onStart(() => void calls++);
            ctx.provideCapability('data-collection', { isActive: () => true });
          },
        },
      ]);
      host.reset();
      sweep();
      await host.start();
      expect(calls).to.equal(0);
      expect(host.httpMiddlewares()).to.have.length(0);
      expect(host.httpRouters()).to.have.length(0);
      expect(host.loadedPlugins()).to.have.length(0);
      expect(host.getCapability('data-collection')).to.equal(undefined);
    });
  });

  describe('loadPlugins', () => {
    it('loads the generated list by default', async () => {
      const report = await loadPlugins();
      expect(report.failed).to.deep.equal([]);
      expect(report.loaded).to.deep.equal(generatedPlugins.map((p) => p.name));
    });

    it('hands the application the routers the loaded plug-ins asked for', async () => {
      const router = Router();
      await loadPlugins([
        { name: 'routes', register: (ctx) => ctx.useRouter('/api/v1/x', router) },
      ]);
      expect(pluginRouters()).to.deep.equal([{ path: '/api/v1/x', router }]);
    });

    it('accepts an empty list: nothing is registered and nothing breaks', async () => {
      const report = await loadPlugins([]);
      expect(report).to.deep.equal({ loaded: [], failed: [] });
      expect(pluginHttpMiddlewares()).to.have.length(0);
      expect(pluginRouters()).to.have.length(0);
      expect(getCapability('data-collection')).to.equal(undefined);
      expect(await startPlugins()).to.deep.equal([]);
      expect(await shutdownPlugins()).to.deep.equal([]);
      expect(sweep).not.to.throw();
    });

    it('loads once: a second call returns the first report', async () => {
      let registered = 0;
      const list = [{ name: 'once', register: () => void registered++ }];
      const first = await loadPlugins(list);
      const second = await loadPlugins(list);
      expect(registered).to.equal(1);
      expect(second).to.equal(first);
    });

    it('does not stop start-up when a plug-in throws in register', async () => {
      let started = 0;
      const report = await loadPlugins([
        {
          name: 'broken',
          register: () => {
            throw new Error('boom');
          },
        },
        {
          name: 'fine',
          register: (ctx) => {
            ctx.onStart(() => void started++);
            ctx.provideCapability('data-collection', { isActive: () => false });
          },
        },
      ]);
      expect(report.loaded).to.deep.equal(['fine']);
      expect(report.failed.map((f) => f.plugin)).to.deep.equal(['broken']);
      await startPlugins();
      expect(started).to.equal(1);
      expect(getCapability('data-collection')?.isActive()).to.equal(false);
    });

    it('logs a failing event listener without letting it reach the emitter', async () => {
      await loadPlugins([
        {
          name: 'noisy',
          register: (ctx) => {
            ctx.onEvent('docstore.ttl.swept', () => {
              throw new Error('listener');
            });
          },
        },
      ]);
      expect(sweep).not.to.throw();
      expect(sweep).not.to.throw();
    });
  });
});
