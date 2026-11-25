import type { WorkerFeature } from '../contracts/generated/ext/pipeshub/enums';
import type { RunInputSnapshot } from './execution';

/**
 * G-52: the person's locale sets the answer's language unless they write in another one or ask otherwise. Admission
 * takes it from the request's `Accept-Language` (the web app sends the language it shows; any other client its
 * own), and the run input carries it as `locale` while a live worker reads it (`output_locale`); the loop says it at
 * the context tail, so it never changes the cached prompt prefix (GA-27).
 */
export const OUTPUT_LOCALE_FEATURE: WorkerFeature = 'output_locale';

/** As the worker's wire model checks it (`app/utils/output_language.py`): a language, then up to three subtags. */
const LOCALE = /^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8}){0,3}$/;
export const MAX_LOCALE_CHARS = 35;
/** Past this a header is not a browser's: it is not read. */
const MAX_HEADER_CHARS = 512;

/** A tag in its canonical case (`de-DE`, `zh-Hant-TW`), or undefined when it is not a well-formed one. */
export function canonicalLocale(value: string | undefined): string | undefined {
  const tag = (value ?? '').trim();
  if (tag.length > MAX_LOCALE_CHARS || !LOCALE.test(tag)) return undefined;
  const [language = '', ...subtags] = tag.split('-');
  return [
    language.toLowerCase(),
    ...subtags.map((part) =>
      part.length === 4 && /^[A-Za-z]+$/.test(part)
        ? part.charAt(0).toUpperCase() + part.slice(1).toLowerCase()
        : part.length === 2 && /^[A-Za-z]+$/.test(part)
          ? part.toUpperCase()
          : part.toLowerCase(),
    ),
  ].join('-');
}

/** The locale an `Accept-Language` header prefers most: its highest-weighted well-formed tag; `*` names none. */
export function requestLocale(
  header: string | string[] | undefined,
): string | undefined {
  const value = Array.isArray(header) ? header.join(',') : header;
  if (value === undefined || value.length > MAX_HEADER_CHARS) return undefined;
  let best: { tag: string; q: number } | undefined;
  for (const entry of value.split(',')) {
    const [range, ...params] = entry.split(';');
    const tag = canonicalLocale(range);
    if (tag === undefined) continue;
    const weight = params
      .map((param) => param.trim())
      .find((param) => param.startsWith('q='));
    const q = weight === undefined ? 1 : Number(weight.slice(2));
    if (!Number.isFinite(q) || q <= 0 || q > 1) continue;
    if (best === undefined || q > best.q) best = { tag, q };
  }
  return best?.tag;
}

/** The admitted input with the decided locale: set while a live worker reads it, else none. */
export function withOutputLocale(
  input: RunInputSnapshot,
  locale: string | undefined,
): RunInputSnapshot {
  const { locale: _previous, ...rest } = input;
  return locale === undefined ? rest : { ...rest, locale };
}
