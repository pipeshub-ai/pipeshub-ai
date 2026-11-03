// Express `trust proxy`: false (none), a hop count, or addresses/CIDRs.
export type TrustProxySetting = false | number | string[];

export interface ParsedTrustProxy {
  value: TrustProxySetting;
  warning?: string;
}

// `true` is rejected: Express would then use the leftmost X-Forwarded-For
// entry, which the client controls.
export function parseTrustProxy(raw: string | undefined): ParsedTrustProxy {
  const value = raw?.trim();
  if (!value || value === 'false' || value === '0') {
    return { value: false };
  }
  if (value === 'true') {
    return {
      value: false,
      warning:
        'TRUST_PROXY=true is not allowed (it trusts client-supplied X-Forwarded-For). ' +
        'Set a hop count or a list of proxy CIDRs. Falling back to trusting no proxy.',
    };
  }
  if (/^\d+$/.test(value)) {
    return { value: parseInt(value, 10) };
  }
  return {
    value: value
      .split(',')
      .map((entry) => entry.trim())
      .filter(Boolean),
  };
}
