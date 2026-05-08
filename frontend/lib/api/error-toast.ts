import { toast, useToastStore } from '@/lib/store/toast-store';
import { ErrorType, ProcessedError } from './api-error';

interface ErrorToastConfig {
  title: string;
  description: string;
}

const activeErrorToasts = new Map<ErrorType, string>();

const ERROR_TOAST_MAP: Record<ErrorType, ErrorToastConfig | null> = {
  [ErrorType.AUTHENTICATION_ERROR]: null, // Handled by redirect
  [ErrorType.REQUEST_CANCELLED]: null, // Abort/superseded request — not user-actionable
  [ErrorType.AUTHORIZATION_ERROR]: {
    title: 'Access Denied',
    description: 'You don\'t have permission to perform this action.',
  },
  [ErrorType.VALIDATION_ERROR]: {
    title: 'Invalid Request',
    description: 'Please check your input and try again.',
  },
  [ErrorType.NOT_FOUND]: {
    title: 'Not Found',
    description: 'The requested resource was not found.',
  },
  [ErrorType.CONFLICT]: {
    title: 'Action Required',
    description: 'A conflict occurred. Please check the details and try again.',
  },
  [ErrorType.NETWORK_ERROR]: {
    title: 'Connection Error',
    description: 'Please check your internet connection and try again.',
  },
  [ErrorType.SERVER_ERROR]: {
    title: 'Server Error',
    description: 'Something went wrong on our end. Please try again later.',
  },
  [ErrorType.TIMEOUT_ERROR]: {
    title: 'Request Timed Out',
    description: 'The server took too long to respond. Please try again.',
  },
  [ErrorType.UNKNOWN_ERROR]: {
    title: 'Something Went Wrong',
    description: 'An unexpected error occurred.',
  },
};

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

  // Always prefer backend error message over hardcoded fallback
  const description = error.message || config.description;

  const id = toast.error(config.title, {
    description,
    duration: null,
    showCloseButton: true,
  });

  activeErrorToasts.set(error.type, id);
}
