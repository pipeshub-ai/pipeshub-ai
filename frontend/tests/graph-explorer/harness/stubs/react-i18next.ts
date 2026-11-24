// The product's own English strings, without i18next's startup.
import en from '@/lib/i18n/locales/en-US.json';

function lookup(key: string): string {
  let cur: unknown = en;
  for (const part of key.split('.')) {
    if (typeof cur !== 'object' || cur === null || !(part in cur)) return key;
    cur = (cur as Record<string, unknown>)[part];
  }
  return typeof cur === 'string' ? cur : key;
}

export function useTranslation() {
  return { t: (key: string, vars?: Record<string, unknown>) => lookup(key).replace(/{{(\w+)}}/g, (_, k) => String(vars?.[k] ?? '')) };
}
