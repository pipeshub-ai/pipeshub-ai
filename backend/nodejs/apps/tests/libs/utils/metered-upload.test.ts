/**
 * SEC-C-08: every multipart reader counts a request's bytes as they arrive, declared or chunked, and refuses it
 * past its cap; files stream to a private folder that is removed with the response.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import express from 'express';
import { existsSync } from 'node:fs';
import http from 'node:http';
import { AddressInfo } from 'node:net';

import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import {
  createMulter,
  FIELDS_ALLOWANCE_BYTES,
  readUploadedFile,
  requestByteCap,
  requestDiskStorage,
} from '../../../src/libs/utils/multer.utils';

const CAP = 64 * 1024;
const BOUNDARY = 'meter';

const serveUpload = async (): Promise<{
  port: number;
  dirs: string[];
  stored: string[];
  close: () => Promise<void>;
}> => {
  const dirs: string[] = [];
  const stored: string[] = [];
  const upload = createMulter({
    storage: requestDiskStorage(),
    limits: { fileSize: 1024 * 1024, files: 10 },
    maxRequestBytes: CAP,
  }).array('files', 10);
  const app = express();
  app.post('/up', upload, async (req, res) => {
    const files = (req.files ?? []) as Express.Multer.File[];
    for (const file of files) {
      dirs.push(file.destination);
      stored.push((await readUploadedFile(file)).toString());
    }
    res.json({ files: files.length });
  });
  app.use(ErrorMiddleware.handleError());
  const server = http.createServer((req, res) => {
    app(req, res);
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  return {
    port: (server.address() as AddressInfo).port,
    dirs,
    stored,
    close: () => new Promise((resolve) => server.close(() => resolve())),
  };
};

const head = `--${BOUNDARY}\r\nContent-Disposition: form-data; name="files"; filename="a.txt"\r\nContent-Type: text/plain\r\n\r\n`;
const tail = `\r\n--${BOUNDARY}--\r\n`;

/** Sends `chunks` chunked (no Content-Length); `end` false leaves the body open, as an endless sender does. */
const sendChunked = (port: number, chunks: string[], end = true): Promise<{ status: number; body: string }> =>
  new Promise((resolve, reject) => {
    const req = http.request(
      {
        hostname: '127.0.0.1',
        port,
        path: '/up',
        method: 'POST',
        headers: { 'content-type': `multipart/form-data; boundary=${BOUNDARY}`, 'transfer-encoding': 'chunked' },
      },
      (res) => {
        let body = '';
        res.on('data', (d: Buffer) => (body += d.toString()));
        res.on('end', () => resolve({ status: res.statusCode ?? 0, body }));
      },
    );
    req.on('error', reject);
    for (const chunk of chunks) req.write(chunk);
    if (end) req.end();
  });

describe('SEC-C-08: metered multipart uploads', () => {
  it('a chunked upload within the cap is stored from disk, and its folder is removed after the response', async () => {
    const s = await serveUpload();
    try {
      const got = await sendChunked(s.port, [head, 'hello', ' world', tail]);
      expect(got.status).to.equal(200);
      expect(s.stored).to.deep.equal(['hello world']);
      await new Promise((resolve) => setTimeout(resolve, 50));
      expect(s.dirs.every((dir) => !existsSync(dir))).to.equal(true);
    } finally {
      await s.close();
    }
  });

  it('a chunked upload past the cap is refused as it streams, and nothing is stored', async () => {
    const s = await serveUpload();
    try {
      const filler = 'x'.repeat(16 * 1024);
      const got = await sendChunked(s.port, [head, filler, filler, filler, filler, filler, tail]);
      expect(got.status).to.equal(413);
      expect(got.body).to.include('larger than');
      expect(s.stored).to.deep.equal([]);
    } finally {
      await s.close();
    }
  });

  it('an endless chunked body is refused once past the cap, without waiting for its end', async () => {
    const s = await serveUpload();
    try {
      const got = await sendChunked(s.port, [head, 'y'.repeat(CAP + 1)], false);
      expect(got.status).to.equal(413);
      expect(s.stored).to.deep.equal([]);
    } finally {
      await s.close();
    }
  });

  it('a declared length past the cap is refused before a byte is read', async () => {
    const s = await serveUpload();
    try {
      const status = await new Promise<number>((resolve, reject) => {
        const req = http.request(
          {
            hostname: '127.0.0.1',
            port: s.port,
            path: '/up',
            method: 'POST',
            headers: { 'content-type': `multipart/form-data; boundary=${BOUNDARY}`, 'content-length': String(CAP + 1) },
          },
          (res) => {
            res.resume();
            resolve(res.statusCode ?? 0);
          },
        );
        req.on('error', reject);
        req.write(head);
      });
      expect(status).to.equal(413);
    } finally {
      await s.close();
    }
  });

  it('derives a cap from the file limits, or none when a limit is open', () => {
    expect(requestByteCap({ limits: { files: 10, fileSize: 5 } })).to.equal(50 + FIELDS_ALLOWANCE_BYTES);
    expect(requestByteCap({ limits: { files: 10, fileSize: 5 }, maxRequestBytes: 7 })).to.equal(7);
    expect(requestByteCap({ limits: { files: 10 } })).to.equal(undefined);
  });
});

describe('SEC-C-08: one person streams at most two workspace uploads at once', () => {
  it('a third upload while two are still arriving is refused with a plain 429; the first two finish', async () => {
    const { Container } = await import('inversify');
    const composition = await import('../../../src/modules/agent-harness/composition');
    const { createChatHarnessSessionRouter, UPLOADS_IN_FLIGHT_PER_PERSON } = await import(
      '../../../src/modules/agent-harness/transport/session-routes'
    );
    const container = new Container();
    container.bind('AuthMiddleware').toConstantValue({
      authenticate: (req: { user?: unknown }, _res: unknown, next: () => void) => {
        req.user = { orgId: 'org-1', userId: 'u-1' };
        next();
      },
    } as never);
    container.bind(composition.AGENT_HARNESS_SESSION_RUN_SERVICE).toConstantValue({ assertOwner: async () => undefined });
    container.bind(composition.AGENT_HARNESS_RUN_ADMISSION_SERVICE).toConstantValue({});
    container.bind(composition.AGENT_HARNESS_SESSION_WORKSPACES).toConstantValue({
      upload: async () => ({ workspaceId: 'ws:s1', revision: 3, replayed: false, paths: [], replaced: [] }),
    });
    const app = express();
    app.use('/api/v1/conversations', createChatHarnessSessionRouter(container));
    app.use(ErrorMiddleware.handleError());
    const server = http.createServer((req, res) => {
      app(req, res);
    });
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
    const port = (server.address() as AddressInfo).port;
    const part = (name: string, value: string) =>
      `--${BOUNDARY}\r\nContent-Disposition: form-data; name="${name}"\r\n\r\n${value}\r\n`;
    const fields = part('baseRevision', '2') + part('idempotencyKey', 'k-1') + part('paths', '["a.txt"]');
    const open = () => {
      let status = 0;
      const req = http.request(
        {
          hostname: '127.0.0.1',
          port,
          path: '/api/v1/conversations/s1/workspace/files',
          method: 'POST',
          headers: { 'content-type': `multipart/form-data; boundary=${BOUNDARY}`, 'transfer-encoding': 'chunked' },
        },
        (res) => {
          status = res.statusCode ?? 0;
          res.resume();
        },
      );
      const done = new Promise<number>((resolve, reject) => {
        req.on('close', () => resolve(status));
        req.on('error', reject);
      });
      req.write(fields + head);
      return { finish: () => req.end(`hi${tail}`), done };
    };
    try {
      expect(UPLOADS_IN_FLIGHT_PER_PERSON).to.equal(2);
      const first = open();
      const second = open();
      await new Promise((resolve) => setTimeout(resolve, 100));
      const third = open();
      third.finish();
      expect(await third.done).to.equal(429);
      first.finish();
      second.finish();
      expect([await first.done, await second.done]).to.deep.equal([200, 200]);
    } finally {
      await new Promise((resolve) => server.close(() => resolve(undefined)));
    }
  });
});
