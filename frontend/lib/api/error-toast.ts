import { i18n } from '@/lib/i18n';
import { isToastActive, toast } from '@/lib/store/toast-store';
import { ErrorType, getUserFacingErrorMessage, ProcessedError } from './api-error';

interface ErrorToastConfig {
  /** Key under `errorToast` in the locale files; `<titleKey>Description` is the sentence under it. */
  titleKey: string;
}

const activeErrorToasts = new Map<string, string>();

const BUSY_STATUSES = new Set([429, 503, 504]);

const ERROR_TOAST_MAP: Record<ErrorType, ErrorToastConfig | null> = {
  [ErrorType.AUTHENTICATION_ERROR]: null, // Handled by redirect
  [ErrorType.REQUEST_CANCELLED]: null, // Abort/superseded request — not user-actionable
  [ErrorType.AUTHORIZATION_ERROR]: { titleKey: 'accessDenied' },
  [ErrorType.VALIDATION_ERROR]: { titleKey: 'invalidRequest' },
  [ErrorType.NOT_FOUND]: { titleKey: 'notFound' },
  [ErrorType.CONFLICT]: { titleKey: 'actionRequired' },
  [ErrorType.NETWORK_ERROR]: { titleKey: 'connectionError' },
  [ErrorType.SERVER_ERROR]: { titleKey: 'serverError' },
  [ErrorType.TIMEOUT_ERROR]: { titleKey: 'timedOut' },
  [ErrorType.UNKNOWN_ERROR]: { titleKey: 'somethingWentWrong' },
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

  // Busy or slow is worth a retry, not a "Server error" scare. A 502 that our
  // API answered (it carries a reference) means a service it relies on, such as
  // the mail server, failed; a bare proxy 502 is still a server error.
  const busy = BUSY_STATUSES.has(error.statusCode ?? 0);
  const titleKey = busy
    ? 'serverBusy'
    : error.statusCode === 502 && error.requestId
      ? 'connectedServiceProblem'
      : config.titleKey;
  // The server's words when they were written for a reader; otherwise our
  // sentence for this kind of failure, which always says what to do next.
  const fallbackKey = busy ? 'serverBusyDescription' : `${config.titleKey}Description`;
  const base = asSentence(getUserFacingErrorMessage(error, i18n.t(`errorToast.${fallbackKey}`)));
  // Quoting the reference is how an admin finds this failure in the logs.
  const description = error.requestId
    ? `${base} Reference: ${error.requestId}`
    : base;
  const title = i18n.t(`errorToast.${titleKey}`);

  // Deduplicate on what the reader sees, not just the error type: a repeat of
  // the same failure is skipped, but a different one (say, the mail server
  // rejecting its login while a generic server error is open) still shows.
  // The reference differs on every request, so it is left out of the key.
  const dedupeKey = `${title}\n${base}`;
  const existingId = activeErrorToasts.get(dedupeKey);
  if (existingId) {
    // On screen or still waiting behind an Undo toast: either way, already said.
    if (isToastActive(existingId)) return;
    activeErrorToasts.delete(dedupeKey);
  }

  // Closes on the error default like every other toast, so it never sits on a
  // control the reader needs next; pointing at it or focusing it pauses the clock.
  const id = toast.error(title, {
    description,
    showCloseButton: true,
  });

  activeErrorToasts.set(dedupeKey, id);
}
