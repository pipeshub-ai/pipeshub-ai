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

  // Force default theme permanently
  useEffect(() => {
    if (values.state.primaryColor !== 'default') {
      values.setField('primaryColor', 'default');
    }
  }, [values]);

  // Override setField to prevent changing primaryColor
  const setFieldWithDefaultTheme = useCallback((
    name: keyof SettingsState,
    updateValue: SettingsState[keyof SettingsState]
  ) => {
    // Prevent changing primary color from default
    if (name === 'primaryColor') {
      return;
    }
    values.setField(name, updateValue);
  }, [values]);

  // Override setState to prevent changing primaryColor
  const setStateWithDefaultTheme = useCallback((updateValue: Partial<SettingsState>) => {
    const { primaryColor, ...rest } = updateValue;
    // Always keep primaryColor as default
    values.setState({ ...rest, primaryColor: 'default' });
  }, [values]);

  const memoizedValue = useMemo(
    () => ({
      ...values.state,
      primaryColor: 'default' as const, // Force default theme
      canReset: false, // Disable reset since we want to keep default theme
      onReset: () => {}, // Disable reset functionality
      onUpdate: setStateWithDefaultTheme,
      onUpdateField: setFieldWithDefaultTheme,
      // Remove drawer functionality since we're not using the settings drawer
      openDrawer: false,
      onCloseDrawer: () => {},
      onToggleDrawer: () => {},
    }),
    [
      values.state,
      setFieldWithDefaultTheme,
      setStateWithDefaultTheme,
    ]
  );

  return <SettingsContext.Provider value={memoizedValue}>{children}</SettingsContext.Provider>;
}
