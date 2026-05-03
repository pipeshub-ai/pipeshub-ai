'use client';

import { SUPPORTED_LANGUAGES } from '@/lib/i18n/supported-languages';
import { useLanguageStore, type Language } from '@/lib/store/language-store';
import { SubPanel, SubPanelItem } from './sub-panel';

/** Derived from the single source of truth in lib/i18n/supported-languages.ts */
const LANGUAGES = Object.values(SUPPORTED_LANGUAGES) as {
  value: Language;
  menuName: string;
}[];

interface LanguagePanelProps {
  isOpen: boolean;
}

/**
 * Floating sub-panel that appears to the right of the workspace menu.
 * Lists supported languages with their country, checkmark on active.
 */
export function LanguagePanel({ isOpen }: LanguagePanelProps) {
  const { language, setLanguage } = useLanguageStore();

  return (
    <SubPanel isOpen={isOpen}>
      {LANGUAGES.map((lang) => (
        <SubPanelItem
          key={lang.value}
          label={lang.menuName}
          isActive={language === lang.value}
          onClick={() => setLanguage(lang.value)}
        />
      ))}
    </SubPanel>
  );
}
