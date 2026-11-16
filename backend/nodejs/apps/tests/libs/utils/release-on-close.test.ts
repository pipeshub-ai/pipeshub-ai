import 'reflect-metadata';
import { expect } from 'chai';
import express from 'express';
import http from 'http';
import { PassThrough } from 'stream';
import { releaseOnClientClose } from '../../../src/libs/utils/release-on-close';

describe('releaseOnClientClose', function () {
  this.timeout(10000);

  const serve = (
    handler: express.RequestHandler,
  ): Promise<{ server: http.Server; port: number }> =>
    new Promise((resolve) => {
      const app = express().get('/', handler);
      const server = http.createServer((req, res) => void app(req, res));
      server.listen(0, '127.0.0.1', () =>
        resolve({ server, port: (server.address() as { port: number }).port }),
      );
    });

  it('destroys the source when the client leaves before the body ends', async () => {
    const source = new PassThrough();
    const { server, port } = await serve((_req, res) => {
      res.writeHead(200);
      source.pipe(res);
      releaseOnClientClose(res, source);
      source.write(Buffer.alloc(1024));
    });
    try {
      await new Promise<void>((resolve) => {
        const req = http.get({ host: '127.0.0.1', port }, () => {
          req.destroy();
          resolve();
        });
        req.on('error', () => undefined);
      });
      const start = Date.now();
      while (!source.destroyed && Date.now() - start < 3000) {
        await new Promise((r) => setTimeout(r, 25));
      }
      expect(source.destroyed).to.equal(true);
    } finally {
      server.closeAllConnections();
      server.close();
    }
  });

  it('leaves the source alone once the body was fully sent', async () => {
    const source = new PassThrough();
    const { server, port } = await serve((_req, res) => {
      source.pipe(res);
      releaseOnClientClose(res, source);
      source.end('done');
    });
    try {
      await new Promise<void>((resolve, reject) => {
        http
          .get({ host: '127.0.0.1', port }, (res) => {
            res.resume();
            res.on('end', () => resolve());
          })
          .on('error', reject);
      });
      await new Promise((r) => setTimeout(r, 50));
      expect(source.writableFinished).to.equal(true);
      expect(source.listenerCount('error')).to.equal(0);
    } finally {
      server.closeAllConnections();
      server.close();
    }
  });
});
