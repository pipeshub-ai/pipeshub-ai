'use client';

import { useState, useRef, useCallback, useEffect } from 'react';
import { useToastStore } from '@/lib/store/toast-store';
import { useTranslation } from 'react-i18next';
import { ConnectorsApi } from '../api';
import { useConnectorsStore } from '../store';

export const INLINE_RENAME_MAX_LENGTH = 128;

const INPUT_PADDING_BUFFER = 24;

interface UseInlineRenameOptions {
  connectorId: string;
  currentName: string;
}

export function useInlineRename({ connectorId, currentName }: UseInlineRenameOptions) {
  const { t } = useTranslation();
  const addToast = useToastStore((s) => s.addToast);
  const renameConnectorInstance = useConnectorsStore((s) => s.renameConnectorInstance);

  const [isEditing, setIsEditing] = useState(false);
  const [editValue, setEditValue] = useState('');
  const [isBusy, setIsBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const measureRef = useRef<HTMLSpanElement>(null);
  const [inputWidth, setInputWidth] = useState<number | undefined>(undefined);
  const cancelledRef = useRef(false);

  const measureTextWidth = useCallback((text: string) => {
    if (measureRef.current) {
      measureRef.current.textContent = text || ' ';
      return measureRef.current.offsetWidth;
    }
    return undefined;
  }, []);

  useEffect(() => {
    if (isEditing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [isEditing]);

  const startEditing = useCallback(() => {
    cancelledRef.current = false;
    const name = currentName;
    setEditValue(name);
    setIsEditing(true);
    const width = measureTextWidth(name);
    if (width !== undefined) {
      setInputWidth(width + INPUT_PADDING_BUFFER);
    }
  }, [currentName, measureTextWidth]);

  const cancelEditing = useCallback(() => {
    cancelledRef.current = true;
    setEditValue(currentName);
    setIsEditing(false);
  }, [currentName]);

  const saveRename = useCallback(async () => {
    if (cancelledRef.current) return;
    if (!connectorId || isBusy) return;

    const trimmed = editValue.trim();
    if (!trimmed || trimmed === currentName) {
      setIsEditing(false);
      return;
    }

    setIsBusy(true);
    try {
      await ConnectorsApi.updateConnectorInstanceName(connectorId, trimmed);
      renameConnectorInstance(connectorId, trimmed);
      addToast({
        variant: 'success',
        title: t('workspace.connectors.settingsTab.renameSuccess'),
        duration: 3000,
      });
    } catch (error: unknown) {
      console.error('ConnectorsApi.updateConnectorInstanceName', error);
    } finally {
      setIsBusy(false);
      setIsEditing(false);
    }
  }, [connectorId, currentName, editValue, isBusy, renameConnectorInstance, addToast, t]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        e.stopPropagation();
        void saveRename();
      } else if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        cancelEditing();
      }
    },
    [saveRename, cancelEditing]
  );

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      setEditValue(e.target.value);
      const width = measureTextWidth(e.target.value);
      if (width !== undefined) {
        setInputWidth(width + INPUT_PADDING_BUFFER);
      }
    },
    [measureTextWidth]
  );

  return {
    isEditing,
    editValue,
    isBusy,
    inputRef,
    measureRef,
    inputWidth,
    startEditing,
    cancelEditing,
    saveRename,
    handleKeyDown,
    handleChange,
  };
}
