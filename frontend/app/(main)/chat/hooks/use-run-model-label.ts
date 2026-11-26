'use client';

import { useMemo } from 'react';
import { useChatStore } from '../store';
import type { RunModelRef } from '../types';
import { runModelLabel } from '../utils/run-model';

/** The name to show for the model a run called; null when the run names none. */
export function useRunModelLabel(model: RunModelRef | undefined): string | null {
  const availableModels = useChatStore((s) => s.settings.availableModels);
  return useMemo(
    () =>
      model
        ? runModelLabel(
            model,
            Object.values(availableModels).flatMap((entry) => entry.models),
          )
        : null,
    [model, availableModels],
  );
}
