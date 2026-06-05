'use client';

import type { ReactNode } from 'react';
import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import { immer } from 'zustand/middleware/immer';
// ========================================
// Toast Types
// ========================================

export type ToastVariant = 'loading' | 'success' | 'error' | 'info' | 'warning';
export type ToastPlacement = 'top' | 'bottom';

export interface ToastAction {
  label: string;
  icon?: string;
  onClick?: () => void;
  /** Renders as a link when set (use with `openInNewTab` for external/same-app new tab). */
  href?: string;
  openInNewTab?: boolean;
}

export interface Toast {
  id: string;
  variant: ToastVariant;
  title: string;
  description?: string;
  // Rich content rendered instead of `description` when set. A FUNCTION (not a
  // node) so the immer store never holds/auto-freezes React elements — fresh
  // elements are created at render time, avoiding frozen-element crashes.
  renderDescription?: () => ReactNode;
  icon?: string;                    // Custom icon (e.g., connector icon like 'chat')
  showCloseButton?: boolean;        // Default: true for non-loading toasts
  action?: ToastAction;             // Optional action button
  duration?: number | null;         // Auto-dismiss duration in ms (null = persist)
  /** Wider layout for scrollable multi-line content. */
  contentLayout?: 'default' | 'expanded';
  /** Screen edge for the toast stack. Default `bottom`; use `top` when the bottom is occupied (e.g. upload tray). */
  placement?: ToastPlacement;
  createdAt: number;                // Timestamp for ordering
  isExiting?: boolean;              // For exit animation
}

export interface ToastOptions {
  description?: string;
  renderDescription?: () => ReactNode;
  icon?: string;
  showCloseButton?: boolean;
  action?: ToastAction;
  duration?: number | null;         // Override default duration
  contentLayout?: 'default' | 'expanded';
  placement?: ToastPlacement;
}

// Immer cannot safely hold render callbacks; keep them outside the draft state.
const renderDescriptionByToastId = new Map<string, () => ReactNode>();

export function getToastRenderDescription(
  id: string,
): (() => ReactNode) | undefined {
  return renderDescriptionByToastId.get(id);
}

// ========================================
// Store State & Actions
// ========================================

interface ToastState {
  toasts: Toast[];
  isHovered: boolean;               // For stack expansion on hover
  maxVisibleToasts: number;         // Max toasts shown in stack (default: 3)
}

interface ToastActions {
  addToast: (toast: Omit<Toast, 'id' | 'createdAt' | 'isExiting'>) => string;
  removeToast: (id: string) => void;
  updateToast: (id: string, updates: Partial<Omit<Toast, 'id' | 'createdAt'>>) => void;
  clearAll: () => void;
  setHovered: (hovered: boolean) => void;
}

type ToastStore = ToastState & ToastActions;

// ========================================
// Constants
// ========================================

const generateId = () => `toast-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;

const DEFAULT_DURATIONS: Record<ToastVariant, number | null> = {
  loading: null,      // Persist until updated/removed
  success: 3000,
  error: 6000,
  info: 3000,
  warning: 4000,
};

const initialState: ToastState = {
  toasts: [],
  isHovered: false,
  maxVisibleToasts: 3,
};

// ========================================
// Store Implementation
// ========================================

export const useToastStore = create<ToastStore>()(
  devtools(
    immer((set, get) => ({
      ...initialState,

      addToast: (toastData) => {
        const id = generateId();
        const { renderDescription, ...toastFields } = toastData;
        if (renderDescription) {
          renderDescriptionByToastId.set(id, renderDescription);
        }
        // Toasts with action buttons persist until user interaction
        const duration = toastFields.duration !== undefined
          ? toastFields.duration
          : toastFields.action
            ? null
            : DEFAULT_DURATIONS[toastFields.variant];

        const toast: Toast = {
          ...toastFields,
          id,
          createdAt: Date.now(),
          placement: toastFields.placement ?? 'bottom',
          showCloseButton: toastFields.showCloseButton ?? (toastFields.variant !== 'loading'),
          duration,
        };

        set((state) => {
          // Add new toast to the beginning (newest first)
          state.toasts.unshift(toast);
        });

        // Auto-dismiss if duration is set
        if (duration) {
          setTimeout(() => {
            get().removeToast(id);
          }, duration);
        }

        return id;
      },

      removeToast: (id) => {
        // Check if toast exists and isn't already exiting
        const existingToast = get().toasts.find((t) => t.id === id);
        if (!existingToast || existingToast.isExiting) return;

        set((state) => {
          const index = state.toasts.findIndex((t) => t.id === id);
          if (index !== -1) {
            state.toasts[index].isExiting = true;
          }
        });

        // Remove after exit animation (300ms)
        setTimeout(() => {
          // Drop the out-of-state render callback so the module-level map does
          // not retain closures for dismissed toasts (memory leak otherwise).
          renderDescriptionByToastId.delete(id);
          set((state) => {
            state.toasts = state.toasts.filter((t) => t.id !== id);
          });
        }, 300);
      },

      updateToast: (id, updates) => {
        const { renderDescription, ...toastUpdates } = updates;
        if (renderDescription !== undefined) {
          if (renderDescription) {
            renderDescriptionByToastId.set(id, renderDescription);
          } else {
            renderDescriptionByToastId.delete(id);
          }
        }

        set((state) => {
          const toast = state.toasts.find((t) => t.id === id);
          if (toast) {
            Object.assign(toast, toastUpdates);

            // If variant changed and no explicit duration provided, set up auto-dismiss
            if (toastUpdates.variant && toastUpdates.duration === undefined) {
              // Toasts with action buttons persist until user interaction
              const newDuration = toastUpdates.action || toast.action
                ? null
                : DEFAULT_DURATIONS[toastUpdates.variant];
              toast.duration = newDuration;
              toast.showCloseButton = toastUpdates.showCloseButton ?? (toastUpdates.variant !== 'loading');

              if (newDuration) {
                setTimeout(() => {
                  get().removeToast(id);
                }, newDuration);
              }
            }
          }
        });
      },

      clearAll: () => {
        renderDescriptionByToastId.clear();
        set((state) => {
          state.toasts = [];
        });
      },

      setHovered: (hovered) => {
        set((state) => {
          state.isHovered = hovered;
        });
      },
    })),
    { name: 'ToastStore' }
  )
);

// ========================================
// Selectors
// ========================================

export const selectToasts = (state: ToastStore) => state.toasts;
export const selectIsHovered = (state: ToastStore) => state.isHovered;
export const selectMaxVisibleToasts = (state: ToastStore) => state.maxVisibleToasts;

// ========================================
// Toast Helper API
// ========================================

export const toast = {
  /**
   * Show a loading toast (persists until updated/removed)
   * @returns Toast ID for later updates
   */
  loading: (title: string, options?: ToastOptions): string => {
    return useToastStore.getState().addToast({
      variant: 'loading',
      title,
      ...options,
    });
  },

  /**
   * Show a success toast (auto-dismiss after 3s)
   */
  success: (title: string, options?: ToastOptions): string => {
    return useToastStore.getState().addToast({
      variant: 'success',
      title,
      ...options,
    });
  },

  /**
   * Show an error toast (auto-dismiss after 3s)
   */
  error: (title: string, options?: ToastOptions): string => {
    return useToastStore.getState().addToast({
      variant: 'error',
      title,
      ...options,
    });
  },

  /**
   * Show an info toast (auto-dismiss after 3s)
   */
  info: (title: string, options?: ToastOptions): string => {
    return useToastStore.getState().addToast({
      variant: 'info',
      title,
      ...options,
    });
  },

  /**
   * Show a warning toast (auto-dismiss after 4s)
   */
  warning: (title: string, options?: ToastOptions): string => {
    return useToastStore.getState().addToast({
      variant: 'warning',
      title,
      ...options,
    });
  },

  /**
   * Update an existing toast (useful for loading -> success/error transitions)
   */
  update: (id: string, updates: Partial<Omit<Toast, 'id' | 'createdAt'>>): void => {
    useToastStore.getState().updateToast(id, updates);
  },

  /**
   * Dismiss a specific toast
   */
  dismiss: (id: string): void => {
    useToastStore.getState().removeToast(id);
  },

  /**
   * Dismiss all toasts
   */
  dismissAll: (): void => {
    useToastStore.getState().clearAll();
  },

  /**
   * Promise helper - shows loading, then success/error based on promise result
   */
  promise: async <T>(
    promise: Promise<T>,
    options: {
      loading: string;
      success: string | ((data: T) => string);
      error: string | ((err: unknown) => string);
      loadingOptions?: ToastOptions;
      successOptions?: ToastOptions;
      errorOptions?: ToastOptions;
    }
  ): Promise<T> => {
    const id = toast.loading(options.loading, options.loadingOptions);

    try {
      const result = await promise;
      const successMessage = typeof options.success === 'function'
        ? options.success(result)
        : options.success;

      toast.update(id, {
        variant: 'success',
        title: successMessage,
        ...options.successOptions,
      });

      return result;
    } catch (err) {
      const errorMessage = typeof options.error === 'function'
        ? options.error(err)
        : options.error;

      toast.update(id, {
        variant: 'error',
        title: errorMessage,
        ...options.errorOptions,
      });

      throw err;
    }
  },
};
