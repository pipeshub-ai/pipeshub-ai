import { i18n } from '@/lib/i18n';
import {
  UPLOAD_FAILURE_KIND_ORDER,
  type UploadFailureKind,
  primaryUploadFailureKind,
} from '@/lib/constants/file-rejection-reason';
import { selectIsAdmin, useUserStore } from '@/lib/store/user-store';
import { toast } from '@/lib/store/toast-store';
import type { UploadItem } from '@/lib/store/upload-store';

const LABS_PATH = '/workspace/labs/';

type ToastAction = {
  label: string;
  icon?: string;
  href?: string;
  openInNewTab?: boolean;
};

/** Context handed to every presentation hook so they stay pure (no globals). */
interface FailureContext {
  count: number; // failures of this kind
  total: number; // failures across all kinds
  maxFileSizeMB: number;
  isAdmin: boolean;
}

/**
 * One row per failure kind drives the ENTIRE summary toast — title, severity,
 * the count chips, the description, and any action. Adding a new kind (or
 * promoting `other` errors like rate-limit/5xx to their own messaging) is a
 * single entry here; there are no parallel switch/if-chains to keep in sync.
 * Order matches {@link UPLOAD_FAILURE_KIND_ORDER} (most specific first), which
 * also decides the dominant description and the count-chip order.
 */
interface FailureKindPresentation {
  kind: UploadFailureKind;
  severity: 'warning' | 'info';
  /** i18n base key for the "{{count}} …" count chip (expects `_one`/`_other`). */
  summaryKey: string;
  /** Extra interpolation params for the count chip (e.g. the size limit). */
  summaryParams?: (ctx: FailureContext) => Record<string, unknown>;
  /** Primary description sentence when this is the dominant (highest-priority) kind. */
  description?: (ctx: FailureContext) => string;
  /** Title override used only when EVERY failure is this kind. */
  exclusiveTitleKey?: string;
  /** Optional CTA (e.g. a Labs link) shown when this kind is present. */
  action?: (ctx: FailureContext) => ToastAction | undefined;
}

const PRESENTATIONS: Record<UploadFailureKind, FailureKindPresentation> = {
  size_limit: {
    kind: 'size_limit',
    severity: 'warning',
    summaryKey: 'uploadProgress.rejectionSummary.tooLarge',
    summaryParams: (ctx) => ({ maxMb: ctx.maxFileSizeMB }),
    exclusiveTitleKey: 'uploadProgress.rejectionToast.sizeLimitTitle',
    description: (ctx) =>
      ctx.isAdmin
        ? i18n.t('uploadProgress.rejectionToast.sizeLimitDescriptionAdmin', {
            maxMb: ctx.maxFileSizeMB,
          })
        : i18n.t('uploadProgress.rejectionToast.sizeLimitDescription', {
            maxMb: ctx.maxFileSizeMB,
          }),
    action: (ctx) =>
      ctx.isAdmin
        ? {
            label: i18n.t('uploadProgress.rejectionToast.openLabs'),
            icon: 'science',
            href: LABS_PATH,
            openInNewTab: true,
          }
        : undefined,
  },
  unsupported_type: {
    kind: 'unsupported_type',
    severity: 'info',
    summaryKey: 'uploadProgress.rejectionSummary.unsupported',
    description: () => i18n.t('uploadProgress.rejectionToast.unsupportedDescription'),
  },
  duplicate_name: {
    kind: 'duplicate_name',
    severity: 'info',
    summaryKey: 'uploadProgress.rejectionSummary.duplicate',
    description: () => i18n.t('uploadProgress.rejectionToast.duplicateDescription'),
  },
  other: {
    kind: 'other',
    severity: 'warning',
    summaryKey: 'uploadProgress.rejectionSummary.other',
  },
};

/** Presentations in display priority order (most specific first, `other` last). */
const ORDERED_PRESENTATIONS: FailureKindPresentation[] =
  UPLOAD_FAILURE_KIND_ORDER.map((kind) => PRESENTATIONS[kind]);

/**
 * Platform admin flag from the user profile store (`profile.isAdmin === true`).
 * Read at toast time (not from a React closure) so async uploads see the latest value.
 */
function isPlatformAdmin(): boolean {
  return selectIsAdmin(useUserStore.getState()) === true;
}

function countByKind(items: UploadItem[]): Record<UploadFailureKind, number> {
  const counts = Object.fromEntries(
    UPLOAD_FAILURE_KIND_ORDER.map((k) => [k, 0]),
  ) as Record<UploadFailureKind, number>;

  for (const item of items) {
    const kind = item.rejectionReasons?.length
      ? primaryUploadFailureKind(item.rejectionReasons)
      : 'other';
    counts[kind] += 1;
  }
  return counts;
}

function pluralKey(
  base: string,
  count: number,
  options?: Record<string, unknown>,
): string {
  return i18n.t(`${base}_${count === 1 ? 'one' : 'other'}`, { count, ...options });
}

/**
 * Top-of-screen toast for upload failures. Stays until the user dismisses it;
 * the bottom upload panel holds per-file detail. All per-kind specifics come
 * from {@link PRESENTATIONS}, so this function never branches on a kind.
 */
export function notifyUploadFailures(
  failedItems: UploadItem[],
  maxFileSizeMB: number,
): void {
  if (failedItems.length === 0) return;

  const total = failedItems.length;
  const isAdmin = isPlatformAdmin();
  const counts = countByKind(failedItems);
  const ctxFor = (p: FailureKindPresentation): FailureContext => ({
    count: counts[p.kind],
    total,
    maxFileSizeMB,
    isAdmin,
  });

  const present = ORDERED_PRESENTATIONS.filter((p) => counts[p.kind] > 0);

  // Count chips, in priority order.
  const summaryParts = present.map((p) =>
    pluralKey(p.summaryKey, counts[p.kind], p.summaryParams?.(ctxFor(p))),
  );

  // Title: a kind-specific title only when every failure is that single kind.
  const sole = present.length === 1 ? present[0] : undefined;
  const title =
    sole?.exclusiveTitleKey && counts[sole.kind] === total
      ? pluralKey(sole.exclusiveTitleKey, counts[sole.kind])
      : pluralKey('uploadProgress.rejectionToast.title', total);

  // Warning if any present kind is a warning, else info.
  const variant: 'warning' | 'info' = present.some((p) => p.severity === 'warning')
    ? 'warning'
    : 'info';

  // Description: the dominant kind's sentence, then a multi-kind chip summary,
  // then the "see the panel" hint.
  const segments: string[] = [];
  const dominant = present.find((p) => p.description);
  if (dominant?.description) segments.push(dominant.description(ctxFor(dominant)));
  if (present.length > 1 && summaryParts.length > 0) {
    segments.push(summaryParts.join(' · '));
  }
  segments.push(i18n.t('uploadProgress.rejectionToast.trackerHint'));

  // Action: the first present kind that offers one.
  let action: ToastAction | undefined;
  for (const p of present) {
    action = p.action?.(ctxFor(p));
    if (action) break;
  }

  toast[variant](title, {
    placement: 'top',
    duration: null,
    showCloseButton: true,
    description: segments.join(' '),
    action,
  });
}
