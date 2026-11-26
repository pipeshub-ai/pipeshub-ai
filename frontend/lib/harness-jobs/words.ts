/**
 * How an adapter words things: an i18next `t`, or anything with its shape. Adapters stay pure (no React, no global
 * i18n), so a test passes the en-US catalogue and a screen passes `useTranslation().t`.
 */
export type Words = (key: string, vars?: Record<string, unknown>) => string;
