import 'reflect-metadata';
import { expect } from 'chai';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import sinon from 'sinon';

const HEALTH_ROUTE_FILES = ['src/integrations/slack-bot/src/index.ts'];

function healthHandler(source: string): (req: unknown, res: unknown) => void {
  const match = source.match(
    /router\.get\("\/", \(_req: Request, res: Response\) => \{([\s\S]*?)\}\);/,
  );
  expect(match, 'health route handler').to.not.equal(null);
  return new Function('_req', 'res', match![1]) as (req: unknown, res: unknown) => void;
}

describe('slack bot health route', () => {
  for (const relativePath of HEALTH_ROUTE_FILES) {
    describe(relativePath, () => {
      const source = readFileSync(resolve(__dirname, '../../../', relativePath), 'utf8');

      it('does not log the request', () => {
        expect(source).to.not.include('console.log(req)');
      });

      it('replies Running and does not print a request that carries a token', () => {
        const handler = healthHandler(source);
        const req = { headers: { authorization: 'Bearer SUPER-SECRET-TOKEN' } };
        let body: unknown;
        const res = {
          send(value: unknown) {
            body = value;
            return this;
          },
        };
        const log = sinon.spy(console, 'log');
        try {
          handler(req, res);
        } finally {
          log.restore();
        }

        expect(body).to.equal('Running');
        expect(log.called).to.equal(false);
        expect(log.args.flat().join(' ')).to.not.include('SUPER-SECRET-TOKEN');
      });
    });
  }
});
