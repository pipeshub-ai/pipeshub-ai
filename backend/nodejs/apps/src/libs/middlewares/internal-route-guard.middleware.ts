import { RequestHandler } from 'express';
import { BlockList, isIP } from 'node:net';
import { Logger } from '../services/logger.service';

export type InternalRouteMode = 'log' | 'deny';

/** `deny` (any case, surrounding spaces ignored) enforces; anything else observes, and an unknown value is warned about. */
export const resolveInternalRouteMode = (
  raw: string | undefined,
  logger?: Logger,
): InternalRouteMode => {
  const mode = (raw ?? '').trim().toLowerCase();
  if (mode === 'deny') return 'deny';
  if (mode && mode !== 'log') {
    logger?.warn('Unknown INTERNAL_ROUTES_PUBLIC_ACCESS value; using log', {
      value: raw,
    });
  }
  return 'log';
};

const INTERNAL_SEGMENT = /\/internal(\/|$)/i;

const loopback = new BlockList();
loopback.addSubnet('127.0.0.0', 8, 'ipv4');
loopback.addAddress('::1', 'ipv6');

/** IPv4-mapped IPv6 (::ffff:a.b.c.d or ::ffff:7f00:1) is checked as its IPv4 address. */
const normalize = (addr: string): string => {
  const dotted = /^::ffff:(\d+\.\d+\.\d+\.\d+)$/i.exec(addr);
  if (dotted?.[1]) return dotted[1];
  const hex = /^::ffff:([0-9a-f]{1,4}):([0-9a-f]{1,4})$/i.exec(addr);
  if (!hex?.[1] || !hex[2]) return addr;
  const [hi, lo] = [parseInt(hex[1], 16), parseInt(hex[2], 16)];
  return `${hi >> 8}.${hi & 255}.${lo >> 8}.${lo & 255}`;
};

// Unrelated public addresses: a trusted entry must not match more than one range of them.
const SENTINELS_V4 = ['8.8.8.8', '198.51.100.1'];
const SENTINEL_V6 = '2001:4860:4860::8888';

const matchesTooMuch = (one: BlockList): boolean => {
  const direct = SENTINELS_V4.filter((ip) => one.check(ip, 'ipv4')).length;
  const mapped = SENTINELS_V4.some((ip) => one.check(`::ffff:${ip}`, 'ipv6'));
  const v6 = one.check(SENTINEL_V6, 'ipv6');
  return direct >= 2 || mapped || (v6 && direct > 0);
};

/** Parses a comma-separated CIDR list. Malformed entries are skipped with a warning and never widen access. */
export const parseTrustedCidrs = (
  raw: string | undefined,
  logger?: Logger,
): BlockList => {
  const list = new BlockList();
  for (const entry of (raw ?? '')
    .split(',')
    .map((e) => e.trim())
    .filter(Boolean)) {
    const [net, bits, ...extra] = entry.split('/');
    let addr = net ?? '';
    let prefix =
      bits !== undefined && /^\d{1,3}$/.test(bits) ? Number(bits) : -1;
    let family = addr ? isIP(addr) : 0;
    // An IPv4-mapped entry is stored as the IPv4 address with its prefix minus 96.
    const mapped = family === 6 ? normalize(addr) : addr;
    if (mapped !== addr) {
      addr = mapped;
      family = 4;
      prefix -= 96;
    }
    // A zero prefix (or a mapped one of 96 or less) would match every address.
    if (
      !family ||
      extra.length ||
      prefix < 1 ||
      prefix > (family === 4 ? 32 : 128)
    ) {
      logger?.warn('Ignored a malformed INTERNAL_ROUTES_TRUSTED_CIDRS entry', {
        entry,
      });
      continue;
    }
    const one = new BlockList();
    one.addSubnet(addr, prefix, family === 4 ? 'ipv4' : 'ipv6');
    if (matchesTooMuch(one)) {
      logger?.warn(
        'Ignored an INTERNAL_ROUTES_TRUSTED_CIDRS entry that matches public addresses',
        { entry },
      );
      continue;
    }
    list.addSubnet(addr, prefix, family === 4 ? 'ipv4' : 'ipv6');
  }
  return list;
};

const isInList = (list: BlockList, addr: string | undefined): boolean => {
  if (!addr) return false;
  const ip = normalize(addr);
  const family = isIP(ip);
  return family !== 0 && list.check(ip, family === 4 ? 'ipv4' : 'ipv6');
};

const LOG_INTERVAL_MS = 60_000;
const MAX_TRACKED_PATHS = 500;

/**
 * Keeps `/internal/` routes (service-to-service) off public traffic. A request is internal only
 * when its peer is loopback or inside `trusted` and it carries no forwarded header. Anything else
 * is public: `log` records it and continues, `deny` answers 404.
 *
 * A reverse proxy on loopback must set X-Forwarded-For (or Forwarded / X-Real-IP): one that strips
 * them makes every public request look like a loopback service call. A "no proxy in front" setting
 * would not help, because the services' own calls are loopback without those headers too.
 *
 * shortcut: a private peer outside loopback and the trusted list counts as public, because a client
 * through a Docker published port appears as the gateway address. All-in-one and Compose
 * deployments call over loopback and need nothing; deployments where services call across a
 * network must list their ranges in INTERNAL_ROUTES_TRUSTED_CIDRS before setting `deny`.
 */
export const createInternalRouteGuard = (
  logger: Logger,
  mode: InternalRouteMode,
  trusted: BlockList = new BlockList(),
): RequestHandler => {
  const lastLogged = new Map<string, number>();

  const shouldLog = (path: string): boolean => {
    const now = Date.now();
    const last = lastLogged.get(path);
    if (last !== undefined && now - last < LOG_INTERVAL_MS) return false;
    if (lastLogged.size >= MAX_TRACKED_PATHS) {
      // Drop the oldest entry; a flood of distinct paths cannot grow the map.
      lastLogged.delete(lastLogged.keys().next().value as string);
    }
    lastLogged.set(path, now);
    return true;
  };

  return (req, res, next) => {
    if (!INTERNAL_SEGMENT.test(req.path)) {
      next();
      return;
    }

    const forwarded = Boolean(
      req.headers['forwarded'] ||
      req.headers['x-forwarded-for'] ||
      req.headers['x-real-ip'],
    );
    // eslint-disable-next-line @typescript-eslint/no-unnecessary-condition -- a mocked or torn-down request may lack a socket
    const peer = req.socket?.remoteAddress;
    if (!forwarded && (isInList(loopback, peer) || isInList(trusted, peer))) {
      next();
      return;
    }

    const reason = forwarded ? 'forwarded_header' : 'untrusted_socket_address';
    if (shouldLog(req.path)) {
      logger.warn(
        mode === 'deny'
          ? 'Refused public request to internal route'
          : 'Public request to internal route observed',
        {
          securityEvent:
            mode === 'deny'
              ? 'internal_route_public_refused'
              : 'internal_route_public_observed',
          path: req.path,
          reason,
        },
      );
    }
    if (mode === 'deny') {
      res
        .status(404)
        .json({ error: { code: 'NOT_FOUND', message: 'Not found' } });
      return;
    }
    next();
  };
};
