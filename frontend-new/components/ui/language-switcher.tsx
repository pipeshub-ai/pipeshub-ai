'use client';

import React from 'react';
import { Flex, Text, DropdownMenu, Button } from '@radix-ui/themes';
import { useLanguageStore, Language } from '@/lib/store/language-store';
import { useTranslation } from 'react-i18next';

export function LanguageSwitcher() {
  const { t } = useTranslation();
  const { language, setLanguage } = useLanguageStore();

  const languages: { value: Language; label: string; nativeLabel: string }[] = [
    { value: 'en-US', label: 'English', nativeLabel: 'English' },
    { value: 'de-DE', label: 'German', nativeLabel: 'Deutsch' },
    { value: 'es-ES', label: 'Spanish', nativeLabel: 'Español' },
    { value: 'en-IN', label: 'English (India)', nativeLabel: 'English' },
    { value: 'hi-IN', label: 'Hindi', nativeLabel: 'हिन्दी' },
  ];

  const currentLanguage = languages.find((lang) => lang.value === language);

  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger>
        <Button variant="ghost" size="2">
          <Flex align="center" gap="2">
            <span className="material-icons-outlined" style={{ fontSize: '18px' }}>
              language
            </span>
            <Text size="2">{currentLanguage?.nativeLabel || 'EN'}</Text>
          </Flex>
        </Button>
      </DropdownMenu.Trigger>

      <DropdownMenu.Content align="end">
        <DropdownMenu.Label>
          {t('common.selectLanguage')}
        </DropdownMenu.Label>
        <DropdownMenu.Separator />
        {languages.map((lang) => (
          <DropdownMenu.Item
            key={lang.value}
            onClick={() => setLanguage(lang.value)}
            style={{
              backgroundColor:
                language === lang.value ? 'var(--accent-3)' : 'transparent',
            }}
          >
            <Flex align="center" justify="between" style={{ width: '100%' }}>
              <Text size="2">{lang.nativeLabel}</Text>
              {language === lang.value && (
                <span
                  className="material-icons-outlined"
                  style={{ fontSize: '16px', color: 'var(--accent-11)' }}
                >
                  check
                </span>
              )}
            </Flex>
          </DropdownMenu.Item>
        ))}
      </DropdownMenu.Content>
    </DropdownMenu.Root>
  );
}
