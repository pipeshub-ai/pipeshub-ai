import { i18n } from '@/lib/i18n';

/**
 * G-52 (C20.53): the person's PipesHub language as a request header. A run answers in it unless they write in
 * another; a chat turn sends it, and so does the request that makes a schedule, whose runs fire with no browser.
 */
export function uiLanguageHeader(): Record<string, string> {
  const language = i18n.language;
  return typeof language === 'string' && language.length > 0 ? { 'Accept-Language': language } : {};
}
