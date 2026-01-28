import { useMemo, useState, useCallback, createContext } from 'react';

import { useLocalStorage } from 'src/hooks/use-local-storage';

import { STORAGE_KEY } from '../config-settings';

import type { SettingsState, SettingsContextValue, SettingsProviderProps } from '../types';

// ----------------------------------------------------------------------

export const SettingsContext = createContext<SettingsContextValue | undefined>(undefined);

export const SettingsConsumer = SettingsContext.Consumer;

// ----------------------------------------------------------------------

export function SettingsProvider({ children, settings }: SettingsProviderProps) {
  const values = useLocalStorage<SettingsState>(STORAGE_KEY, settings);

  const [openDrawer, setOpenDrawer] = useState(false);

  const onOpenDrawer = useCallback(() => setOpenDrawer(true), []);
  const onCloseDrawer = useCallback(() => setOpenDrawer(false), []);
  const onToggleDrawer = useCallback(() => {
    setOpenDrawer((prev) => !prev);
  }, []);

  const memoizedValue = useMemo(
    () => ({
      ...values.state,
      canReset: values.canReset,
      onReset: values.resetState,
      onUpdate: values.setState,
      onUpdateField: values.setField,
      openDrawer,
      onCloseDrawer,
      onToggleDrawer,
      onOpenDrawer,
    }),
    [
      values,
      openDrawer,
      onCloseDrawer,
      onToggleDrawer,
      onOpenDrawer,
    ]
  );

  return <SettingsContext.Provider value={memoizedValue}>{children}</SettingsContext.Provider>;
}