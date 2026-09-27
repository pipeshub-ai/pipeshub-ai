import type { i18n as I18n } from 'i18next';

/**
 * `{{field, lowercase}}` lets a locale put a server-supplied name mid-sentence
 * in lower case ("Select project"), while German keeps its nouns capitalised
 * by leaving the formatter out.
 */
export function addFormatters(instance: I18n): void {
  instance.services.formatter?.add('lowercase', (value: unknown, lng: string) =>
    String(value ?? '').toLocaleLowerCase(lng),
  );
}
