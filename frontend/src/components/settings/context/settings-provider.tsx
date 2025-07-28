import { useMemo, useEffect, useCallback, createContext } from 'react';

import { useLocalStorage } from 'src/hooks/use-local-storage';

import { STORAGE_KEY } from '../config-settings';

import type { SettingsState, SettingsContextValue, SettingsProviderProps } from '../types';

// ----------------------------------------------------------------------

export const SettingsContext = createContext<SettingsContextValue | undefined>(undefined);

export const SettingsConsumer = SettingsContext.Consumer;

// ----------------------------------------------------------------------

export function SettingsProvider({ children, settings }: SettingsProviderProps) {
  const values = useLocalStorage<SettingsState>(STORAGE_KEY, settings);

  // Force blue theme permanently
  useEffect(() => {
    if (values.state.primaryColor !== 'blue') {
      values.setField('primaryColor', 'blue');
    }
  }, [values]);

  // Override setField to prevent changing primaryColor
  const setFieldWithBlueTheme = useCallback((
    name: keyof SettingsState,
    updateValue: SettingsState[keyof SettingsState]
  ) => {
    // Prevent changing primary color from blue
    if (name === 'primaryColor') {
      return;
    }
    values.setField(name, updateValue);
  }, [values]);

  // Override setState to prevent changing primaryColor
  const setStateWithBlueTheme = useCallback((updateValue: Partial<SettingsState>) => {
    const { primaryColor, ...rest } = updateValue;
    // Always keep primaryColor as blue
    values.setState({ ...rest, primaryColor: 'blue' });
  }, [values]);

  const memoizedValue = useMemo(
    () => ({
      ...values.state,
      primaryColor: 'blue' as const, // Force blue theme
      canReset: false, // Disable reset since we want to keep blue theme
      onReset: () => {}, // Disable reset functionality
      onUpdate: setStateWithBlueTheme,
      onUpdateField: setFieldWithBlueTheme,
      // Remove drawer functionality since we're not using the settings drawer
      openDrawer: false,
      onCloseDrawer: () => {},
      onToggleDrawer: () => {},
    }),
    [
      values.state,
      setFieldWithBlueTheme,
      setStateWithBlueTheme,
    ]
  );

  return <SettingsContext.Provider value={memoizedValue}>{children}</SettingsContext.Provider>;
}