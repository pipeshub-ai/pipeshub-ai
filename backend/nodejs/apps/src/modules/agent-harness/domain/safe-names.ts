/**
 * The one check of a name or path a person or an engine supplies, before it becomes a file anywhere (SEC-FS-02).
 * Python has the same rules (`harness_core/workspace/safe_names.py`); `contracts/harness/vectors/safe-names.json`
 * pins both.
 *
 * - `checkName`: one file name a person or an engine chose. Refuses separators, `.`/`..`, NUL and control
 *   characters, separators or dots hidden by percent-encoding or look-alike characters, a drive letter or `:`, a
 *   Windows device name, a trailing dot or space.
 * - `checkPortablePath`: a relative workspace path entering the store or leaving it in an archive: canonical, with
 *   no drive letter or encoded separator in any segment (Linux names such as `aux.c` stay allowed).
 * - `linkTargetEscapes`: a link target that is no plain relative POSIX path; `linkEscapes` adds where it lands.
 */
import { trimRun } from './trim-run';
import { normalizeWorkspacePath, WorkspaceError } from './workspace';

export const MAX_NAME_BYTES = 255;

export type UnsafeNameReason =
  | 'empty'
  | 'too_long'
  | 'separator'
  | 'traversal'
  | 'nul'
  | 'control'
  | 'encoded_separator'
  | 'drive'
  | 'reserved'
  | 'trailing_dot_or_space'
  | 'not_a_path';

export class UnsafeNameError extends Error {
  constructor(
    readonly reason: UnsafeNameReason,
    message: string,
  ) {
    super(message);
    this.name = 'UnsafeNameError';
  }
}

const DRIVE = /^[A-Za-z]:/;
const ENCODED = /%[0-9A-Fa-f]{2}/;
const DECODE_ROUNDS = 4;
const CONTROL = /\p{Cc}/u;
const RESERVED_STEMS = new Set([
  'con',
  'prn',
  'aux',
  'nul',
  'conin$',
  'conout$',
  ...['com', 'lpt'].flatMap((device) =>
    Array.from('0123456789¹²³').map((digit) => `${device}${digit}`),
  ),
]);

/** Python's `unquote(errors="surrogateescape")` for what matters here: a malformed escape stays as it is. */
function percentDecode(value: string): string {
  return value.replace(/(?:%[0-9A-Fa-f]{2})+/g, (run) => {
    try {
      return decodeURIComponent(run);
    } catch {
      return run.replace(/%([0-9A-Fa-f]{2})/g, (escape, hex: string) => {
        const byte = parseInt(hex, 16);
        return byte < 0x80 ? String.fromCharCode(byte) : escape;
      });
    }
  });
}

function hidesSeparator(segment: string): boolean {
  const forms = new Set([segment, segment.normalize('NFKC')]);
  let current = segment;
  for (
    let round = 0;
    round < DECODE_ROUNDS && ENCODED.test(current);
    round += 1
  ) {
    current = percentDecode(current);
    forms.add(current);
    forms.add(current.normalize('NFKC'));
  }
  forms.delete(segment);
  return [...forms].some(
    (form) =>
      form.includes('/') ||
      form.includes('\\') ||
      form.includes('\u0000') ||
      form === '.' ||
      form === '..',
  );
}

/** `name` in NFC when it is one portable file name; `UnsafeNameError` with the reason otherwise. */
export function checkName(name: unknown): string {
  if (typeof name !== 'string' || name.length === 0) {
    throw new UnsafeNameError('empty', 'The name is empty');
  }
  if (name.includes('\u0000')) {
    throw new UnsafeNameError('nul', 'The name contains a NUL character');
  }
  const normalized = name.normalize('NFC');
  if (normalized.includes('/') || normalized.includes('\\')) {
    throw new UnsafeNameError(
      /^[/\\]/.test(normalized) ? 'traversal' : 'separator',
      'The name contains a folder separator',
    );
  }
  if (normalized === '.' || normalized === '..') {
    throw new UnsafeNameError('traversal', 'The name points at a folder');
  }
  if (CONTROL.test(normalized)) {
    throw new UnsafeNameError(
      'control',
      'The name contains a control character',
    );
  }
  if (hidesSeparator(normalized)) {
    throw new UnsafeNameError(
      'encoded_separator',
      'The name hides a folder separator or a dot folder',
    );
  }
  if (normalized.includes(':')) {
    throw new UnsafeNameError(
      DRIVE.test(normalized) ? 'drive' : 'separator',
      'The name contains a colon, which names a drive or a hidden stream on Windows',
    );
  }
  if (/[. ]$/.test(normalized)) {
    throw new UnsafeNameError(
      'trailing_dot_or_space',
      'The name ends with a dot or a space',
    );
  }
  const stem = trimRun(normalized.split('.', 1)[0] ?? '', ' ', { end: true });
  if (RESERVED_STEMS.has(stem.toLowerCase())) {
    throw new UnsafeNameError(
      'reserved',
      'The name is a device name on Windows',
    );
  }
  if (Buffer.byteLength(normalized, 'utf8') > MAX_NAME_BYTES) {
    throw new UnsafeNameError('too_long', 'The name is too long');
  }
  return normalized;
}

export const isSafeName = (name: unknown): boolean => {
  try {
    checkName(name);
    return true;
  } catch {
    return false;
  }
};

function pathReason(path: unknown): UnsafeNameReason {
  if (typeof path !== 'string' || path.length === 0) return 'empty';
  if (path.includes('\u0000')) return 'nul';
  if (path.includes('\\')) return 'separator';
  if (path.split('/').some((s) => s === '.' || s === '..')) return 'traversal';
  return 'not_a_path';
}

/** `path` canonical with no drive letter or encoded separator in any segment; `UnsafeNameError` otherwise. */
export function checkPortablePath(path: unknown): string {
  if (typeof path === 'string' && /^[/\\]/.test(path)) {
    throw new UnsafeNameError('traversal', 'The path is absolute');
  }
  if (typeof path === 'string' && DRIVE.test(path)) {
    throw new UnsafeNameError('drive', 'The path starts with a drive');
  }
  let canonical: string;
  try {
    canonical = normalizeWorkspacePath(path);
  } catch (error) {
    if (!(error instanceof WorkspaceError)) throw error;
    throw new UnsafeNameError(pathReason(path), error.message);
  }
  for (const segment of canonical.split('/')) {
    if (DRIVE.test(segment)) {
      throw new UnsafeNameError(
        'drive',
        'A folder or file name in the path names a drive',
      );
    }
    if (hidesSeparator(segment)) {
      throw new UnsafeNameError(
        'encoded_separator',
        'The path hides a folder separator or a dot folder',
      );
    }
  }
  return canonical;
}

/** A relative path whose every segment passes `checkName` (a person's upload into a folder). */
export function checkNamePath(path: unknown): string {
  const canonical = checkPortablePath(path);
  for (const segment of canonical.split('/')) checkName(segment);
  return canonical;
}

/** Whether a link's target is no plain relative POSIX path: absolute, a drive, a backslash or an encoded separator. */
export function linkTargetEscapes(target: string): boolean {
  if (
    target.length === 0 ||
    target.includes('\u0000') ||
    target.includes('\\') ||
    target.startsWith('/') ||
    DRIVE.test(target)
  ) {
    return true;
  }
  return target
    .split('/')
    .some(
      (segment) =>
        segment !== '' && (DRIVE.test(segment) || hidesSeparator(segment)),
    );
}

/**
 * Python's `sync.rules.link_escapes`: whether a link at `path` may point outside the root, resolved lexically from
 * its folder; passing through another link (`links`) counts as escaping.
 */
export function linkEscapes(
  path: string,
  target: string,
  links: ReadonlySet<string> = new Set(),
): boolean {
  if (linkTargetEscapes(target)) return true;
  const resolved = path.split('/').slice(0, -1);
  for (const segment of target.split('/')) {
    if (segment === '' || segment === '.') continue;
    if (
      resolved.length > 0 &&
      resolved.join('/') !== path &&
      links.has(resolved.join('/'))
    ) {
      return true;
    }
    if (segment === '..') {
      if (resolved.length === 0) return true;
      resolved.pop();
    } else {
      resolved.push(segment);
    }
  }
  return false;
}
