import { i18n } from '@/lib/i18n';
import { toast, useToastStore } from '@/lib/store/toast-store';
import { ErrorType, getUserFacingErrorMessage, ProcessedError } from './api-error';

interface ErrorToastConfig {
  /** Key under `errorToast` in the locale files. */
  titleKey: string;
  description: string;
}

const activeErrorToasts = new Map<ErrorType, string>();

const BUSY_STATUSES = new Set([429, 503, 504]);

const ERROR_TOAST_MAP: Record<ErrorType, ErrorToastConfig | null> = {
  [ErrorType.AUTHENTICATION_ERROR]: null, // Handled by redirect
  [ErrorType.REQUEST_CANCELLED]: null, // Abort/superseded request — not user-actionable
  [ErrorType.AUTHORIZATION_ERROR]: {
    titleKey: 'accessDenied',
    description: 'You don\'t have permission to perform this action.',
  },
  [ErrorType.VALIDATION_ERROR]: {
    titleKey: 'invalidRequest',
    description: 'Please check your input and try again.',
  },
  [ErrorType.NOT_FOUND]: {
    titleKey: 'notFound',
    description: 'The requested resource was not found.',
  },
  [ErrorType.CONFLICT]: {
    titleKey: 'actionRequired',
    description: 'A conflict occurred. Please check the details and try again.',
  },
  [ErrorType.NETWORK_ERROR]: {
    titleKey: 'connectionError',
    description: 'Please check your internet connection and try again.',
  },
  [ErrorType.SERVER_ERROR]: {
    titleKey: 'serverError',
    description:
      'Something went wrong on our end. Please try again in a moment; if it keeps happening, contact your workspace admin.',
  },
  [ErrorType.TIMEOUT_ERROR]: {
    titleKey: 'timedOut',
    description: 'The server took too long to respond. Please try again.',
  },
  [ErrorType.UNKNOWN_ERROR]: {
    titleKey: 'somethingWentWrong',
    description:
      'That didn\'t work. Please try again; if it keeps happening, contact your workspace admin.',
  },
};

/**
 * Server messages often arrive as lower-case fragments ("admin access required").
 * Capitalise them and end them with a full stop, unless they already end in
 * sentence punctuation, an ellipsis, a closing bracket or a closing quote.
 */
export function asSentence(text: string): string {
  const sentence = text.charAt(0).toUpperCase() + text.slice(1);
  return /[.!?…)"'”’»]$/.test(sentence) ? sentence : `${sentence}.`;
}

export function showErrorToast(error: ProcessedError): void {
  const config = ERROR_TOAST_MAP[error.type];
  if (!config) return;

  // Deduplicate: skip if a toast for this error type is already showing
  const existingId = activeErrorToasts.get(error.type);
  if (existingId) {
    const stillExists = useToastStore.getState().toasts.some(
      (t) => t.id === existingId && !t.isExiting
    );
    if (stillExists) return;
    activeErrorToasts.delete(error.type);
  }

  // The server's words when they were written for a reader; otherwise the
  // sentence above, which always says what to do next.
  const base = asSentence(getUserFacingErrorMessage(error, config.description));
  // Quoting the reference is how an admin finds this failure in the logs.
  const description = error.requestId
    ? `${base} Reference: ${error.requestId}`
    : base;
  // Busy or slow is worth a retry, not a "Server error" scare.
  const titleKey = BUSY_STATUSES.has(error.statusCode ?? 0) ? 'tryAgainShortly' : config.titleKey;
  const title = i18n.t(`errorToast.${titleKey}`);

  const id = toast.error(title, {
    description,
    duration: null,
    showCloseButton: true,
  });

  activeErrorToasts.set(error.type, id);
}
