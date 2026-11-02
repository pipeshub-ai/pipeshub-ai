'use client';

import { SUPPORTED_LANGUAGES } from '@/lib/i18n/supported-languages';
import { useLanguageStore, type Language } from '@/lib/store/language-store';
import { useTranslation } from 'react-i18next';
import { SubPanel, SubPanelItem } from './sub-panel';

/** Derived from lib/i18n/supported-languages.ts, sorted by native name so variants sit together. */
const LANGUAGES = (Object.values(SUPPORTED_LANGUAGES) as {
  value: Language;
  menuName: string;
}[]).sort((a, b) => a.menuName.localeCompare(b.menuName, 'en'));

interface LanguagePanelProps {
  isOpen: boolean;
  onBack: () => void;
}

/**
 * Floating sub-panel that appears to the right of the workspace menu.
 * Lists supported languages with their country, checkmark on active.
 */
export function LanguagePanel({ isOpen, onBack }: LanguagePanelProps) {
  const { language, setLanguage } = useLanguageStore();
  const { t } = useTranslation();

  return (
    <SubPanel isOpen={isOpen} title={t('workspaceMenu.language')} onBack={onBack}>
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
