// Query values that grant access on their own: an OAuth callback's code and
// state, and any token or secret a caller put in a URL.
const SENSITIVE_QUERY_PARAMS = new Set([
  'code',
  'state',
  'token',
  'access_token',
  'refresh_token',
  'id_token',
  'client_secret',
]);

// Matched against whole words of a key, never substrings, so `passage` or
// `bypass` stay readable while `X-Amz-Signature` or `apiKey` do not.
const CREDENTIAL_WORDS = new Set([
  'password',
  'passwd',
  'pass',
  'pwd',
  'secret',
  'token',
  'signature',
  'sig',
  'credential',
  'credentials',
  'apikey',
]);

const keyWords = (key: string): string[] =>
  key
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter((word) => word !== '');

const isSensitiveKey = (key: string): boolean => {
  if (SENSITIVE_QUERY_PARAMS.has(key.toLowerCase())) return true;
  const words = keyWords(key);
  return words.some(
    (word, i) =>
      CREDENTIAL_WORDS.has(word) || (word === 'api' && words[i + 1] === 'key'),
  );
};

/** The URL with sensitive query values replaced, for writing to logs. */
export const logSafeUrl = (url: string): string => {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return url.split('?')[0] ?? '';
  }
  for (const key of Array.from(parsed.searchParams.keys())) {
    if (isSensitiveKey(key)) {
      parsed.searchParams.set(key, 'REDACTED');
    }
  }
  return parsed.toString();
};
