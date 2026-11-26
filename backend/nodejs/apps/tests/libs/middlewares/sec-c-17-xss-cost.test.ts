/**
 * SEC-C-17 (found in R38-INPUT's sweep): the global XSS check runs before authentication on every route. Its tag
 * patterns were quadratic on a run of `<` (about 160 ms a string, so seconds for one 10 MB body), and its
 * sanitizer is linear but heavy on text with `<` in it. Both are now bounded.
 */
import { expect } from 'chai';
import express from 'express';
import http from 'node:http';
import { AddressInfo } from 'node:net';

import {
  XSS_MARKUP_BUDGET_CHARS,
  xssSanitizationMiddleware,
} from '../../../src/libs/middlewares/xss-sanitization.middleware';
import { containsXSSPattern, sanitizeString } from '../../../src/utils/xss-sanitization';

describe('SEC-C-17: the XSS check still answers as before on the shapes that were quadratic', () => {
  // Linear by structure: Python's test_sec_c_pattern_budget.py lints these patterns from this file's source.
  it('a run of < with no > is not a tag, and is left to the sanitizer', () => {
    for (const text of ['<'.repeat(10_000), '<a'.repeat(5_000), '<script'.repeat(1_400), '</script'.repeat(1_200), '<iframe'.repeat(1_400)]) {
      expect(containsXSSPattern(text), text.slice(0, 10)).to.equal(false);
      expect(sanitizeString(text)).to.not.include('<');
    }
  });

  it('still finds a tag, including one after an unclosed opener', () => {
    for (const text of ['<b>', 'x <a<b> y', '< script>', '<img src=x onerror=alert(1)>', `${'<'.repeat(5000)}<i>`]) {
      expect(containsXSSPattern(text), text).to.equal(true);
    }
    for (const text of ['a < b and c > d? no: 3 < 4', 'plain text']) {
      expect(containsXSSPattern(text), text).to.equal(/<[^<>]*>/.test(text));
    }
  });
});

describe('SEC-C-17: one request hands the sanitizer at most 2 MiB of markup-bearing text', () => {
  const send = async (body: unknown, path = '/api/v1/anything'): Promise<{ status: number; seen: unknown }> => {
    let seen: unknown;
    const app = express();
    app.use(express.json({ limit: '10mb' }));
    app.use(xssSanitizationMiddleware);
    app.post(/.*/, (req, res) => {
      seen = req.body;
      res.status(200).end();
    });
    // eslint-disable-next-line @typescript-eslint/no-unused-vars -- Express treats only a four-parameter handler as an error handler
    app.use((err: { statusCode?: number }, _req: express.Request, res: express.Response, _next: express.NextFunction) => {
      res.status(err.statusCode ?? 500).end();
    });
    const server = http.createServer((req, res) => {
      app(req, res);
    });
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
    try {
      const response = await fetch(`http://127.0.0.1:${(server.address() as AddressInfo).port}${path}`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      });
      return { status: response.status, seen };
    } finally {
      await new Promise((resolve) => server.close(() => resolve(undefined)));
    }
  };

  it('an anonymous 9 MB body of < runs is refused at the budget, not sanitized whole', async () => {
    const body = Object.fromEntries(Array.from({ length: 90 }, (_, i) => [`f${i}`, '<'.repeat(99_999)]));
    const got = await send(body);
    // Refused once 2 MiB of it is handed over: the budget is a count, so the cost is bounded without a clock.
    expect(got.status).to.equal(400);
  });

  it('text under the budget is sanitized as before, and text with no angle bracket is only trimmed', async () => {
    const got = await send({ a: ' a > b ', b: '  plain & "quoted"  ', n: 3 });
    expect(got.status).to.equal(200);
    expect(got.seen).to.deep.equal({ a: 'a &gt; b', b: 'plain & "quoted"', n: 3 });
    expect(XSS_MARKUP_BUDGET_CHARS).to.equal(2 * 1024 * 1024);
  });
});
