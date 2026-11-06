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
            color={isAppearanceActive ? 'var(--text-primary)' : 'var(--text-secondary)'}
          />
        }
        label={
          <Flex as="span" align="center" gap="1">
            {t('workspaceMenu.appearance')}
            <Text style={{ color: 'var(--border-strong)' }}>•</Text>
            <Text size="2" weight="medium" style={{ color: 'var(--text-secondary)' }}>{appearanceLabel}</Text>
          </Flex>
        }
        isActive={isAppearanceActive}
        expanded={isAppearanceActive}
        opensPanel={t('workspaceMenu.appearance')}
        rightSlot={
          <MaterialIcon
            name="chevron_right"
            size={ICON_SIZE_DEFAULT}
            color={isAppearanceActive ? 'var(--text-primary)' : 'var(--text-secondary)'}
          />
        }
        onClick={onAppearanceToggle}
      />

      <MenuItem
        icon={
          <MaterialIcon
            name="language"
            size={ICON_SIZE_DEFAULT}
            color={isLanguageActive ? 'var(--text-primary)' : 'var(--text-secondary)'}
          />
        }
        label={
          <Flex as="span" align="center" gap="1">
            {t('workspaceMenu.language')}
            <Text style={{ color: 'var(--border-strong)' }}>•</Text>
            <Text size="2" weight="medium" style={{ color: 'var(--text-secondary)' }}>{languageLabel}</Text>
          </Flex>
        }
        isActive={isLanguageActive}
        expanded={isLanguageActive}
        opensPanel={t('workspaceMenu.language')}
        rightSlot={
          <MaterialIcon
            name="chevron_right"
            size={ICON_SIZE_DEFAULT}
            color={isLanguageActive ? 'var(--text-primary)' : 'var(--text-secondary)'}
          />
        }
        onClick={onLanguageToggle}
      />

      <MenuItem
        icon={
          <MaterialIcon
            name="settings"
            size={ICON_SIZE_DEFAULT}
            color="var(--text-secondary)"
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
        destructive
        onClick={onLogout}
      />
    </Flex>
  );
}
