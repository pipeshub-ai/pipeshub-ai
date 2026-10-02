import type { TFunction } from 'i18next';
import type { ServiceHealthDetail } from '@/lib/store/services-health-store';

/**
 * The line under a service on the Services page, worded in the viewer's
 * language from the health API's `state`. The API's own `message` is English,
 * so it is shown only for a state this build doesn't know yet.
 */
export function serviceStateMessage(
  detail: ServiceHealthDetail | undefined,
  name: string,
  t: TFunction,
): string | undefined {
  if (!detail) return undefined;
  switch (detail.state) {
    case 'healthy':
      return t('workspace.services.stateMessage.healthy', { name });
    case 'unhealthy':
      return t('workspace.services.stateMessage.unhealthy', { name });
    case 'starting':
      return t('workspace.services.stateMessage.starting', { name });
    case 'pending':
      return t('workspace.services.stateMessage.pending');
    case 'unknown':
      return t('workspace.services.stateMessage.unknown', { name });
    default:
      return detail.message?.trim() || undefined;
  }
}
