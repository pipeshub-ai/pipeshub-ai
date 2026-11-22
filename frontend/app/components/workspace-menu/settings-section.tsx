'use client';

import { Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ICON_SIZE_DEFAULT } from '@/app/components/sidebar';
import { useThemeAppearance, type ThemePreference } from '@/app/components/theme-provider';
import { useTranslation } from 'react-i18next';
import { useLanguageStore } from '@/lib/store/language-store';
import { SUPPORTED_LANGUAGES } from '@/lib/i18n/supported-languages';
import { MenuItem } from './menu-item';

interface SettingsSectionProps {
  onWorkspaceSettings: () => void;
  onAppearanceToggle: () => void;
  isAppearanceActive: boolean;
  onLanguageToggle: () => void;
  isLanguageActive: boolean;
  onLogout: () => void;
}

const APPEARANCE_LABEL_KEYS: Record<ThemePreference, string> = {
  system: 'workspaceMenu.system',
  light: 'workspaceMenu.light',
  dark: 'workspaceMenu.dark',
};

/** Icon for appearance: shows current mode icon */
function AppearanceIcon({ color }: { color: string }) {
  const { appearance } = useThemeAppearance();
  return (
    <MaterialIcon
      name={appearance === 'dark' ? 'dark_mode' : 'light_mode'}
      size={ICON_SIZE_DEFAULT}
      color={color}
    />
  );
}

/**
 * Top section of the workspace menu:
 *   Appearance, Workspace Settings, Language, Log Out
 */
export function SettingsSection({
  onWorkspaceSettings,
  onAppearanceToggle,
  isAppearanceActive,
  onLanguageToggle,
  isLanguageActive,
  onLogout,
}: SettingsSectionProps) {
  const { t } = useTranslation();
  const { preference } = useThemeAppearance();
  const { language } = useLanguageStore();

  // Match the option checked in the Appearance panel ("System" stays "System").
  const appearanceLabel = t(APPEARANCE_LABEL_KEYS[preference]);
  const languageLabel = SUPPORTED_LANGUAGES[language].menuName;

  return (
    <Flex direction="column" gap="1">
      <MenuItem
        icon={
          <AppearanceIcon
            color={isAppearanceActive ? 'var(--slate-12)' : 'var(--slate-11)'}
          />
        }
        label={
          <Flex align="center" gap="2">
            {t('workspaceMenu.appearance')}
            <Text size="2" style={{ color: 'var(--slate-8)' }}>•</Text>
            <Text size="2" weight="medium" style={{ color: 'var(--slate-10)' }}>{appearanceLabel}</Text>
          </Flex>
        }
        isActive={isAppearanceActive}
        rightSlot={
          <MaterialIcon
            name="chevron_right"
            size={ICON_SIZE_DEFAULT}
            color={isAppearanceActive ? 'var(--slate-12)' : 'var(--slate-11)'}
          />
        }
        onClick={onAppearanceToggle}
      />

      <MenuItem
        icon={
          <MaterialIcon
            name="language"
            size={ICON_SIZE_DEFAULT}
            color={isLanguageActive ? 'var(--slate-12)' : 'var(--slate-11)'}
          />
        }
        label={
          <Flex align="center" gap="2">
            {t('workspaceMenu.language')}
            <Text size="2" style={{ color: 'var(--slate-8)' }}>•</Text>
            <Text size="2" weight="medium" style={{ color: 'var(--slate-10)' }}>{languageLabel}</Text>
          </Flex>
        }
        isActive={isLanguageActive}
        rightSlot={
          <MaterialIcon
            name="chevron_right"
            size={ICON_SIZE_DEFAULT}
            color={isLanguageActive ? 'var(--slate-12)' : 'var(--slate-11)'}
          />
        }
        onClick={onLanguageToggle}
      />

      <MenuItem
        icon={
          <MaterialIcon
            name="settings"
            size={ICON_SIZE_DEFAULT}
            color="var(--slate-11)"
          />
        }
        label={t('workspaceMenu.workspaceSettings')}
        href="/workspace/"
        onClick={onWorkspaceSettings}
      />

      <MenuItem
        icon={
          <MaterialIcon
            name="logout"
            size={ICON_SIZE_DEFAULT}
            color="var(--red-11)"
          />
        }
        label={t('workspaceMenu.logOut')}
        textColor="var(--red-11)"
        onClick={onLogout}
      />
    </Flex>
  );
}
