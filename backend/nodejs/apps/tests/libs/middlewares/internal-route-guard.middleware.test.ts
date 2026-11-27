import { expect } from 'chai';
import sinon from 'sinon';
import {
  createInternalRouteGuard,
  parseTrustedCidrs,
  resolveInternalRouteMode,
} from '../../../src/libs/middlewares/internal-route-guard.middleware';

function run(
  mode: 'log' | 'deny',
  path: string,
  opts: { ip?: string; headers?: Record<string, string>; cidrs?: string } = {},
) {
  const logger = { warn: sinon.stub(), info: sinon.stub() };
  const req: any = {
    path,
    headers: opts.headers ?? {},
    socket: { remoteAddress: opts.ip ?? '8.8.8.8' },
  };
  const res: any = {
    statusCode: 0,
    body: undefined,
    status(c: number) {
      this.statusCode = c;
      return this;
    },
    json(b: unknown) {
      this.body = b;
      return this;
    },
  };
  const next = sinon.stub();
  void createInternalRouteGuard(
    logger as any,
    mode,
    parseTrustedCidrs(opts.cidrs, logger as any),
  )(req, res, next);
  return { logger, res, next };
}

describe('internal-route-guard middleware', () => {
  it('deny: refuses a request with x-forwarded-for', () => {
    const r = run('deny', '/api/v1/x/internal/y', {
      ip: '127.0.0.1',
      headers: { 'x-forwarded-for': '1.2.3.4' },
    });
    expect(r.res.statusCode).to.equal(404);
    expect(r.next.called).to.equal(false);
  });
  it('deny: refuses a request with a forwarded header', () => {
    const r = run('deny', '/internal/y', {
      ip: '10.0.0.1',
      headers: { forwarded: 'for=1.2.3.4' },
    });
    expect(r.res.statusCode).to.equal(404);
  });
  it('deny: refuses a request with x-real-ip', () => {
    const r = run('deny', '/api/v1/x/internal/y', {
      ip: '127.0.0.1',
      headers: { 'x-real-ip': '1.2.3.4' },
    });
    expect(r.res.statusCode).to.equal(404);
    expect(r.next.called).to.equal(false);
  });
  it('deny: refuses a public socket address', () => {
    const r = run('deny', '/api/v1/x/internal/y', { ip: '8.8.8.8' });
    expect(r.res.statusCode).to.equal(404);
    expect(r.next.called).to.equal(false);
    expect(r.logger.warn.firstCall.args[1]).to.include({
      securityEvent: 'internal_route_public_refused',
    });
  });
  for (const ip of [
    '127.0.0.1',
    '127.1.2.3',
    '::1',
    '::ffff:127.0.0.1',
    '::ffff:7f00:1',
  ]) {
    it(`deny: allows loopback socket ${ip}`, () => {
      const r = run('deny', '/api/v1/x/internal/y', { ip });
      expect(r.next.calledOnce).to.equal(true);
      expect(r.res.statusCode).to.equal(0);
    });
  }
  for (const ip of [
    '172.17.0.1',
    '10.0.0.5',
    '192.168.1.1',
    '169.254.1.1',
    'fd00::1',
    '::ffff:10.0.0.5',
    '::ffff:ac11:1',
  ]) {
    it(`deny: refuses private non-loopback socket ${ip} without a trusted list`, () => {
      const r = run('deny', '/api/v1/x/internal/y', { ip });
      expect(r.res.statusCode).to.equal(404);
      expect(r.next.called).to.equal(false);
    });
  }
  it('log: logs and allows a docker gateway address', () => {
    const r = run('log', '/api/v1/x/internal/y', { ip: '172.17.0.1' });
    expect(r.next.calledOnce).to.equal(true);
    expect(r.res.statusCode).to.equal(0);
    expect(r.logger.warn.firstCall.args[1]).to.include({
      securityEvent: 'internal_route_public_observed',
    });
  });
  it('log: loopback is not logged', () => {
    const r = run('log', '/internal/y', { ip: '127.0.0.1' });
    expect(r.next.calledOnce).to.equal(true);
    expect(r.logger.warn.called).to.equal(false);
  });
  describe('trusted CIDR list', () => {
    const cidrs = '10.20.0.0/16, fd00::/8';
    it('allows a peer inside the list', () => {
      for (const ip of ['10.20.3.4', '::ffff:10.20.3.4', 'fd00::5']) {
        expect(
          run('deny', '/internal/y', { ip, cidrs }).next.calledOnce,
          ip,
        ).to.equal(true);
      }
    });
    it('refuses a peer outside the list', () => {
      expect(
        run('deny', '/internal/y', { ip: '10.21.0.1', cidrs }).res.statusCode,
      ).to.equal(404);
      expect(
        run('deny', '/internal/y', { ip: '172.17.0.1', cidrs }).res.statusCode,
      ).to.equal(404);
    });
    it('still refuses a forwarded header from a trusted peer', () => {
      const r = run('deny', '/internal/y', {
        ip: '10.20.3.4',
        cidrs,
        headers: { 'x-forwarded-for': '1.2.3.4' },
      });
      expect(r.res.statusCode).to.equal(404);
      expect(r.next.called).to.equal(false);
    });
    it('ignores malformed entries with a warning and never widens access', () => {
      const bad =
        'garbage, 10.0.0.0/33, 10.0.0.0/-1, 10.0.0.0/, /8, 10.0.0.0/8/8, ::/129, 10.0.0.1';
      const r = run('deny', '/internal/y', { ip: '10.0.0.1', cidrs: bad });
      expect(r.res.statusCode).to.equal(404);
      expect(r.logger.warn.callCount).to.be.greaterThan(7);
      expect(JSON.stringify(r.logger.warn.firstCall.args)).to.not.include(
        'securityEvent',
      );
    });
    it('ignores entries that would match every address', () => {
      for (const cidrs of [
        '::ffff:10.20.3.4/32',
        '0.0.0.0/0',
        '::/0',
        '::ffff:0:0/96',
        '::ffff:10.20.3.4/0',
      ]) {
        for (const ip of ['172.18.0.1', '8.8.8.8', '2001:db8::1']) {
          const r = run('deny', '/internal/y', { ip, cidrs });
          expect(r.res.statusCode, `${cidrs} ${ip}`).to.equal(404);
          expect(r.next.called).to.equal(false);
        }
      }
    });
    it('ignores entries that match unrelated public ranges whatever their spelling', () => {
      for (const cidrs of [
        '0::ffff:10.20.3.4/32',
        '0:0:0:0:0:ffff:10.20.3.4/96',
        '::10.20.3.4/32',
        '::1/32',
        '::/80',
        '::/1',
      ]) {
        for (const ip of ['172.18.0.1', '8.8.8.8']) {
          const r = run('deny', '/internal/y', { ip, cidrs });
          expect(r.res.statusCode, `${cidrs} ${ip}`).to.equal(404);
        }
      }
    });
    it('still accepts ordinary ranges', () => {
      expect(
        run('deny', '/internal/y', { ip: 'fd00::9', cidrs: 'fd00::/8' }).next
          .calledOnce,
      ).to.equal(true);
      expect(
        run('deny', '/internal/y', { ip: '10.9.9.9', cidrs: '10.0.0.0/8' }).next
          .calledOnce,
      ).to.equal(true);
    });
    it('reads a mapped entry as its IPv4 prefix', () => {
      const cidrs = '::ffff:10.20.3.4/128';
      expect(
        run('deny', '/internal/y', { ip: '10.20.3.4', cidrs }).next.calledOnce,
      ).to.equal(true);
      expect(
        run('deny', '/internal/y', { ip: '10.20.3.5', cidrs }).res.statusCode,
      ).to.equal(404);
      expect(
        run('deny', '/internal/y', {
          ip: '10.20.9.9',
          cidrs: '::ffff:10.20.0.0/112',
        }).next.calledOnce,
      ).to.equal(true);
    });
    it('keeps valid entries next to malformed ones', () => {
      expect(
        run('deny', '/internal/y', {
          ip: '10.20.0.9',
          cidrs: 'junk,10.20.0.0/16',
        }).next.calledOnce,
      ).to.equal(true);
    });
  });
  it('deny: refuses ::ffff:8.8.8.8', () => {
    expect(
      run('deny', '/internal/a', { ip: '::ffff:8.8.8.8' }).res.statusCode,
    ).to.equal(404);
  });
  it('treats INTERNAL case-insensitively and a trailing /internal', () => {
    expect(
      run('deny', '/api/v1/configurationManager/INTERNAL/aiModelsConfig').res
        .statusCode,
    ).to.equal(404);
    expect(run('deny', '/api/v1/foo/internal').res.statusCode).to.equal(404);
  });
  it('does not match lookalike segments', () => {
    for (const p of [
      '/api/v1/knowledgeBase/internals-guide',
      '/not-internal/x',
      '/api/v1/internal-x',
    ]) {
      const r = run('deny', p);
      expect(r.next.calledOnce, p).to.equal(true);
    }
  });
  it('log: calls next and logs path without query', () => {
    const r = run('log', '/api/v1/x/internal/y', { ip: '8.8.8.8' });
    expect(r.next.calledOnce).to.equal(true);
    expect(r.res.statusCode).to.equal(0);
    const [, meta] = r.logger.warn.firstCall.args;
    expect(meta).to.include({
      securityEvent: 'internal_route_public_observed',
      path: '/api/v1/x/internal/y',
    });
    expect(meta.reason).to.be.a('string');
  });
  it('log: rate-limits logging per path', () => {
    const logger = { warn: sinon.stub() };
    const g = createInternalRouteGuard(logger as any, 'log');
    const req: any = {
      path: '/internal/a',
      headers: {},
      socket: { remoteAddress: '8.8.8.8' },
    };
    void g(req, {} as any, () => {});
    void g(req, {} as any, () => {});
    expect(logger.warn.callCount).to.equal(1);
  });
  it('ordinary routes from public IPs are untouched and not logged', () => {
    const r = run('deny', '/api/v1/users', {
      ip: '8.8.8.8',
      headers: { 'x-forwarded-for': '1.1.1.1' },
    });
    expect(r.next.calledOnce).to.equal(true);
    expect(r.logger.warn.called).to.equal(false);
  });
  it('mode defaults to log; deny is read case-insensitively and trimmed', () => {
    expect(resolveInternalRouteMode(undefined)).to.equal('log');
    expect(resolveInternalRouteMode('log')).to.equal('log');
    expect(resolveInternalRouteMode('deny')).to.equal('deny');
    expect(resolveInternalRouteMode('DENY')).to.equal('deny');
    expect(resolveInternalRouteMode(' Deny \n')).to.equal('deny');
  });
  it('warns on an unknown mode and falls back to log', () => {
    const logger = { warn: sinon.stub() };
    expect(resolveInternalRouteMode('enforce', logger as any)).to.equal('log');
    expect(logger.warn.calledOnce).to.equal(true);
    expect(resolveInternalRouteMode('', logger as any)).to.equal('log');
    expect(resolveInternalRouteMode(' LOG ', logger as any)).to.equal('log');
    expect(logger.warn.calledOnce).to.equal(true);
  });
});
