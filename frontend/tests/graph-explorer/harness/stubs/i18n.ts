import { useTranslation } from './react-i18next';

// The product's `i18n.t` for code outside components, over the same English strings.
export const i18n = { t: (key: string, vars?: Record<string, unknown>) => useTranslation().t(key, vars) };
