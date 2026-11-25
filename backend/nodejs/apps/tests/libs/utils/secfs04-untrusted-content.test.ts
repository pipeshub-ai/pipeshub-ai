/**
 * SEC-FS-04 (C6.37): an artifact or upload of an active type (HTML, SVG, XML,
 * script) is never served as a page at the app's origin: the record stream
 * the web app and agents' download links use, the local-storage download, and
 * a cloud signed URL all make it a sandboxed attachment, with nosniff on every
 * type.
 */
import 'reflect-metadata';
import { PassThrough } from 'node:stream';
import { AddressInfo } from 'node:net';
import { Server } from 'node:http';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import axios from 'axios';
import express from 'express';
import { expect } from 'chai';
import sinon from 'sinon';
import {
  asAttachment,
  isActiveContentType,
  UNTRUSTED_CONTENT_CSP,
  untrustedContentHeaders,
} from '../../../src/libs/utils/untrusted-content.utils';
import { getRecordBuffer } from '../../../src/modules/knowledge_base/controllers/kb_controllers';
import { serveFileFromLocalStorage } from '../../../src/modules/storage/utils/utils';

const ACTIVE = [
  'text/html',
  'text/html; charset=utf-8',
  'application/xhtml+xml',
  'image/svg+xml',
  'text/xml',
  'application/xml',
  'application/rss+xml',
  'text/javascript',
  'application/javascript',
  'TEXT/HTML',
];
const PASSIVE = ['application/pdf', 'image/png', 'text/plain', 'text/csv', 'application/octet-stream', 'text/markdown', 'application/json'];

describe('SEC-FS-04 risky output types are served with a safe content type and CSP (C6.37)', () => {
  afterEach(() => sinon.restore());

  it('FS4-1: active types are recognised; passive ones are not', () => {
    for (const type of ACTIVE) expect(isActiveContentType(type), type).to.equal(true);
    for (const type of PASSIVE) expect(isActiveContentType(type), type).to.equal(false);
    expect(isActiveContentType(undefined)).to.equal(false);
  });

  it('FS4-2: an active type is a sandboxed attachment, its filename kept; every type is nosniff', () => {
    expect(untrustedContentHeaders('image/svg+xml', 'inline; filename="chart.svg"')).to.deep.equal({
      'X-Content-Type-Options': 'nosniff',
      'Content-Security-Policy': UNTRUSTED_CONTENT_CSP,
      'Content-Disposition': 'attachment; filename="chart.svg"',
    });
    expect(untrustedContentHeaders('text/html')['Content-Disposition']).to.equal('attachment');
    expect(untrustedContentHeaders('image/png', 'inline')).to.deep.equal({
      'X-Content-Type-Options': 'nosniff',
      'Content-Disposition': 'inline',
    });
    expect(asAttachment("inline; filename*=UTF-8''r%C3%A9sum%C3%A9.html")).to.equal("attachment; filename*=UTF-8''r%C3%A9sum%C3%A9.html");
  });

  describe('FS4-3: the record stream (KB route the web app and download links use) relays no page', () => {
    let server: Server;
    let base: string;

    beforeEach(async () => {
      const app = express();
      app.use((req, _res, next) => {
        (req as unknown as { user: unknown }).user = { userId: 'u-1', orgId: 'o-1' };
        next();
      });
      app.get('/stream/record/:recordId', getRecordBuffer('http://connector.invalid') as never);
      server = app.listen(0);
      await new Promise((resolve) => server.once('listening', resolve));
      base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
    });

    afterEach(async () => {
      await new Promise((resolve) => server.close(resolve));
    });

    const upstream = (headers: Record<string, string>, body: string) => {
      const data = new PassThrough();
      data.end(body);
      // Only the connector call is stubbed; the test's own request uses fetch.
      sinon.stub(axios, 'get').resolves({ headers, data });
    };

    it('an agent-written HTML artifact the connector serves inline arrives as a sandboxed attachment', async () => {
      upstream({ 'content-type': 'text/html; charset=utf-8', 'content-disposition': 'inline; filename="report.html"' }, '<script>steal()</script>');
      const reply = await fetch(`${base}/stream/record/r-1`, { headers: { authorization: 'Bearer t' } });
      expect(reply.status).to.equal(200);
      expect(reply.headers.get('content-type')).to.equal('text/html; charset=utf-8');
      expect(reply.headers.get('content-security-policy')).to.equal(UNTRUSTED_CONTENT_CSP);
      expect(reply.headers.get('x-content-type-options')).to.equal('nosniff');
      expect(reply.headers.get('content-disposition')).to.equal('attachment; filename="report.html"');
      expect(await reply.text(), 'the web app still reads the bytes').to.equal('<script>steal()</script>');
    });

    it('an SVG with no disposition is an attachment too', async () => {
      upstream({ 'content-type': 'image/svg+xml' }, '<svg onload="steal()"/>');
      const reply = await fetch(`${base}/stream/record/r-2`, { headers: { authorization: 'Bearer t' } });
      expect(reply.headers.get('content-disposition')).to.equal('attachment');
      expect(reply.headers.get('content-security-policy')).to.equal(UNTRUSTED_CONTENT_CSP);
    });

    it('a PDF or image keeps its inline preview, with nosniff', async () => {
      upstream({ 'content-type': 'application/pdf', 'content-disposition': 'inline; filename="a.pdf"' }, '%PDF-1.7');
      const reply = await fetch(`${base}/stream/record/r-3`, { headers: { authorization: 'Bearer t' } });
      expect(reply.headers.get('content-disposition')).to.equal('inline; filename="a.pdf"');
      expect(reply.headers.get('x-content-type-options')).to.equal('nosniff');
      expect(reply.headers.get('content-security-policy')).to.equal(null);
    });
  });

  it('FS4-4: the local-storage download of an SVG is a sandboxed nosniff attachment', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'secfs04-'));
    const file = join(dir, 'chart.svg');
    writeFileSync(file, '<svg onload="steal()"/>');
    const headers: Record<string, string> = {};
    const sink = new PassThrough();
    const res = Object.assign(sink, {
      setHeader: (name: string, value: string) => {
        headers[name] = value;
      },
      headersSent: false,
    });
    try {
      serveFileFromLocalStorage(
        { documentName: 'chart', extension: '.svg', local: { localPath: `file://${file}` } } as never,
        res as never,
      );
      await new Promise((resolve) => sink.on('finish', resolve).resume());
      expect(headers).to.include({
        'Content-Type': 'image/svg+xml',
        'X-Content-Type-Options': 'nosniff',
        'Content-Security-Policy': UNTRUSTED_CONTENT_CSP,
      });
      expect(headers['Content-Disposition']).to.match(/^attachment; filename\*=UTF-8''chart\.svg$/);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});
