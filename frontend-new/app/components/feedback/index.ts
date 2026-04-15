/**
 * Feedback Components
 *
 * Toast notification system for the application.
 *
 * @example
 * ```tsx
 * import { toast, ToastContainer } from '@/app/components/feedback';
 *
 * // Show a success toast
 * toast.success('File uploaded successfully');
 *
 * // Show a loading toast and update it later
 * const id = toast.loading('Uploading file...');
 * toast.update(id, { variant: 'success', title: 'Upload complete!' });
 *
 * // Show toast with action button
 * toast.error('Upload failed', {
 *   action: { label: 'Retry', onClick: () => retryUpload() }
 * });
 * ```
 */

export { Toast } from './toast';
export { ToastContainer } from './toast-container';
export { toast } from '@/lib/store/toast-store';
export type {
  Toast as ToastType,
  ToastOptions,
  ToastVariant,
  ToastAction,
} from '@/lib/store/toast-store';
