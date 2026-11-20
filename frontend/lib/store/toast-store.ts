'use client';

import type { ReactNode } from 'react';
import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import { immer } from 'zustand/middleware/immer';
import { onSessionEnd } from '@/lib/api/session-cache';
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
  showCloseButton?: boolean;        // Default: every toast but a loading one; a caller may pass false
  action?: ToastAction;             // Optional action button
  duration?: number | null;         // Auto-dismiss duration in ms (null = persist)
  /** Kept for callers; every toast now has the one width. */
  contentLayout?: 'default' | 'expanded';
  /** Screen edge for the toast stack. Default `bottom`; use `top` when the bottom is occupied (e.g. upload tray). */
  placement?: ToastPlacement;
  createdAt: number;                // Timestamp for ordering
  isExiting?: boolean;              // For exit animation
  /** Raised while an Undo toast showed; it comes up, in order, once that one has gone. */
  isWaiting?: boolean;
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
// One auto-dismiss timer per toast, so a variant change replaces the old one instead of racing it.
// A timer pauses while the reader points at or focuses the toast, and resumes with the time it had left.
interface RemovalTimer {
  handle: ReturnType<typeof setTimeout> | null;
  remaining: number;
  startedAt: number;
  remove: (id: string) => void;
}
const removalTimers = new Map<string, RemovalTimer>();
let timersPaused = false;

function startTimer(id: string, entry: RemovalTimer): void {
  entry.startedAt = Date.now();
  entry.handle = setTimeout(() => {
    removalTimers.delete(id);
    entry.remove(id);
  }, entry.remaining);
}

function scheduleRemoval(id: string, duration: number | null | undefined, remove: (id: string) => void): void {
  const existing = removalTimers.get(id);
  if (existing?.handle) clearTimeout(existing.handle);
  removalTimers.delete(id);
  if (!duration) return;
  const entry: RemovalTimer = { handle: null, remaining: duration, startedAt: Date.now(), remove };
  removalTimers.set(id, entry);
  if (!timersPaused) startTimer(id, entry);
}

// One toast at a time means a newer toast can replace a loading toast whose
// outcome is still to come (toast.promise, or a caller's own update). The
// outcome must still be seen, so a replaced loading toast is remembered here,
// and its success or error comes back as the current toast when it arrives.
type ToastSnapshot = Omit<Toast, 'id' | 'createdAt' | 'isExiting'> & { renderDescription?: () => ReactNode };
const replacedLoading = new Map<string, ToastSnapshot>();
// A returned outcome has a new id; updates and dismissals sent to the old id follow it.
const forwardedIds = new Map<string, string>();

function resolveToastId(id: string): string {
  let current = id;
  const seen = new Set<string>();
  while (forwardedIds.has(current) && !seen.has(current)) {
    seen.add(current);
    current = forwardedIds.get(current)!;
  }
  return current;
}

function pauseRemovalTimers(): void {
  if (timersPaused) return;
  timersPaused = true;
  const now = Date.now();
  removalTimers.forEach((entry) => {
    if (!entry.handle) return;
    clearTimeout(entry.handle);
    entry.handle = null;
    entry.remaining = Math.max(0, entry.remaining - (now - entry.startedAt));
  });
}

function resumeRemovalTimers(): void {
  if (!timersPaused) return;
  timersPaused = false;
  removalTimers.forEach((entry, id) => {
    if (!entry.handle) startTimer(id, entry);
  });
}

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
  isHovered: boolean;               // Pointer or focus is on the toast: its timer is paused
  maxVisibleToasts: number;         // One toast at a time: a new one replaces the one showing
}

interface ToastActions {
  addToast: (toast: Omit<Toast, 'id' | 'createdAt' | 'isExiting'>) => string;
  /** Puts a built toast on screen now (internal: addToast decides whether it waits). */
  presentToast: (toast: Toast) => void;
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

// A toast never outlives the action that raised it, so it cannot sit on a
// control the reader needs next. Errors stay a little longer to be read.
/** Auto-dismiss per variant; a toast with an action (Undo, View) uses its variant's too. */
export const TOAST_DEFAULT_DURATIONS: Record<ToastVariant, number | null> = {
  loading: null,      // Persist until updated/removed
  success: 4000,
  error: 6000,
  info: 4000,
  warning: 4000,
};

/** The exit transition's length if the motion role cannot be read (tests, no CSS). */
const FALLBACK_EXIT_MS = 240;

/**
 * How long a dismissed toast stays in the store while it fades: the
 * --motion-surface role its exit transition runs on (0 under reduced motion).
 */
function exitMs(): number {
  if (typeof document === 'undefined') return FALLBACK_EXIT_MS;
  const raw = getComputedStyle(document.documentElement).getPropertyValue('--motion-surface').trim();
  const value = parseFloat(raw);
  if (!raw || Number.isNaN(value)) return FALLBACK_EXIT_MS;
  return raw.endsWith('ms') ? value : value * 1000;
}

// A toast with an action (Undo, View) is never replaced: the reader could lose
// the only way back from what they just did. Toasts raised while one shows stay
// in the store marked `isWaiting`, and come up in order, one at a time, once it
// has gone. Only the newest toast that is not waiting is drawn.
const isShowing = (t: Toast) => !t.isExiting && !t.isWaiting;

/** Whether a toast is on screen or waiting for its turn. */
export function isToastActive(id: string): boolean {
  return useToastStore.getState().toasts.some((t) => t.id === id && !t.isExiting);
}

const initialState: ToastState = {
  toasts: [],
  isHovered: false,
  maxVisibleToasts: 1,
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
        // Action toasts (Undo, View) close on the same clock; the pause on hover or focus gives time to reach them.
        const duration = toastFields.duration !== undefined
          ? toastFields.duration
          : TOAST_DEFAULT_DURATIONS[toastFields.variant];

        const toast: Toast = {
          ...toastFields,
          id,
          createdAt: Date.now(),
          placement: toastFields.placement ?? 'bottom',
          showCloseButton: toastFields.showCloseButton ?? (toastFields.variant !== 'loading'),
          duration,
        };

        const holdsUndo = get().toasts.some((t) => isShowing(t) && t.action && t.variant !== 'loading');
        if (holdsUndo || get().toasts.some((t) => t.isWaiting)) {
          set((state) => {
            state.toasts.push({ ...toast, isWaiting: true });
          });
          return id;
        }
        get().presentToast(toast);
        return id;
      },

      presentToast: (toast) => {
        const { id, duration } = toast;
        // A pause belongs to the card that was pointed at or focused. That card is
        // leaving (replaced, closed, or its action pressed), and a removed card
        // fires no leave or blur event, so the pause ends here: the new card
        // starts its full lifetime, and pauses again on the next pointer move or focus.
        if (get().isHovered) get().setHovered(false);
        // One toast at a time: the one showing makes way for the new one. A
        // loading toast is remembered so its outcome can still be shown.
        get()
          .toasts.filter(isShowing)
          .forEach((t) => {
            if (t.variant === 'loading') {
              const { id: replacedId, createdAt: _createdAt, isExiting: _isExiting, isWaiting: _isWaiting, ...fields } = t;
              replacedLoading.set(replacedId, { ...fields, renderDescription: renderDescriptionByToastId.get(replacedId) });
            }
            get().removeToast(t.id);
          });

        set((state) => {
          // Add new toast to the beginning (newest first); a waiting one moves up from the queue.
          state.toasts = state.toasts.filter((t) => t.id !== id);
          state.toasts.unshift({ ...toast, isWaiting: false });
        });

        scheduleRemoval(id, duration, (toastId) => get().removeToast(toastId));
      },

      removeToast: (requestedId) => {
        const id = resolveToastId(requestedId);
        // Dismissing a replaced loading toast means its outcome is no longer wanted.
        if (!get().toasts.some((t) => t.id === id && isShowing(t))) {
          replacedLoading.delete(requestedId);
          replacedLoading.delete(id);
        }
        // A toast still waiting for its turn is simply dropped.
        if (get().toasts.some((t) => t.id === id && t.isWaiting)) {
          renderDescriptionByToastId.delete(id);
          set((state) => {
            state.toasts = state.toasts.filter((t) => t.id !== id);
          });
          return;
        }
        // Check if toast exists and isn't already exiting
        const existingToast = get().toasts.find((t) => t.id === id);
        if (!existingToast || existingToast.isExiting) return;
        scheduleRemoval(id, null, () => {});

        set((state) => {
          const index = state.toasts.findIndex((t) => t.id === id);
          if (index !== -1) {
            state.toasts[index].isExiting = true;
          }
        });

        // The next waiting toast comes up as this one leaves, with its full lifetime from now.
        const next = get().toasts.find((t) => t.isWaiting);
        if (next) get().presentToast({ ...next, createdAt: Date.now() });

        // Remove after the exit transition
        setTimeout(() => {
          // Drop the out-of-state render callback so the module-level map does
          // not retain closures for dismissed toasts (memory leak otherwise).
          renderDescriptionByToastId.delete(id);
          set((state) => {
            state.toasts = state.toasts.filter((t) => t.id !== id);
          });
          // A card removed from under the pointer or focus fires no leave or blur event,
          // so with nothing left on screen the pause ends here.
          if (get().toasts.length === 0 && get().isHovered) get().setHovered(false);
        }, exitMs());
      },

      updateToast: (requestedId, updates) => {
        const id = resolveToastId(requestedId);
        const { renderDescription, ...toastUpdates } = updates;

        // The toast was replaced while it was loading: keep its progress, and
        // bring its outcome back as the current toast.
        const replaced = replacedLoading.get(id);
        const showing = get().toasts.some((t) => t.id === id && !t.isExiting);
        // (a waiting toast counts as showing here: it is updated where it waits)
        if (replaced && !showing) {
          const next: ToastSnapshot = {
            ...replaced,
            ...toastUpdates,
            renderDescription: renderDescription === undefined ? replaced.renderDescription : renderDescription,
          };
          if (!toastUpdates.variant || toastUpdates.variant === 'loading') {
            replacedLoading.set(id, next);
            return;
          }
          replacedLoading.delete(id);
          // The outcome's own close button and lifetime, not the loading toast's.
          const { duration: _loadingDuration, showCloseButton: _loadingClose, ...outcome } = next;
          const newId = get().addToast({
            ...outcome,
            ...(toastUpdates.duration !== undefined ? { duration: toastUpdates.duration } : {}),
            ...(toastUpdates.showCloseButton !== undefined ? { showCloseButton: toastUpdates.showCloseButton } : {}),
          });
          forwardedIds.set(id, newId);
          return;
        }

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

            // A changed variant (e.g. loading -> error) gets the new variant's close button and
            // auto-dismiss, with an explicit duration taking precedence over the default.
            if (toastUpdates.variant) {
              const newDuration = toastUpdates.duration !== undefined
                ? toastUpdates.duration
                : TOAST_DEFAULT_DURATIONS[toastUpdates.variant];
              toast.duration = newDuration;
              toast.showCloseButton = toastUpdates.showCloseButton ?? (toastUpdates.variant !== 'loading');

              // A waiting toast's clock starts when it comes up, not now.
              if (!toast.isWaiting) scheduleRemoval(id, newDuration, (toastId) => get().removeToast(toastId));
            }
          }
        });
      },

      clearAll: () => {
        renderDescriptionByToastId.clear();
        replacedLoading.clear();
        forwardedIds.clear();
        removalTimers.forEach((timer) => {
          if (timer.handle) clearTimeout(timer.handle);
        });
        removalTimers.clear();
        timersPaused = false;
        set((state) => {
          state.toasts = [];
          state.isHovered = false;
        });
      },

      setHovered: (hovered) => {
        if (hovered) pauseRemovalTimers();
        else resumeRemovalTimers();
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
   * Show a success toast (auto-dismiss after 4s)
   */
  success: (title: string, options?: ToastOptions): string => {
    return useToastStore.getState().addToast({
      variant: 'success',
      title,
      ...options,
    });
  },

  /**
   * Show an error toast (auto-dismiss after 6s)
   */
  error: (title: string, options?: ToastOptions): string => {
    return useToastStore.getState().addToast({
      variant: 'error',
      title,
      ...options,
    });
  },

  /**
   * Show an info toast (auto-dismiss after 4s)
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

// A loading toast waits on work of the session that just ended, which never settles; other toasts
// (e.g. "Password updated" before a planned sign-out) stay.
onSessionEnd(() => {
  useToastStore
    .getState()
    .toasts.filter((t) => t.variant === 'loading')
    .forEach((t) => toast.dismiss(t.id));
});
