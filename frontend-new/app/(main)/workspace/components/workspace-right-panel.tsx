'use client';

import React, {
  createContext,
  startTransition,
  useCallback,
  useContext,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { createPortal } from 'react-dom';
import { Theme, Flex, Box, Text, Button, IconButton, VisuallyHidden, Tooltip } from '@radix-ui/themes';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { useTranslation } from 'react-i18next';

import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

// ========================================
// Types
// ========================================

interface WorkspaceRightPanelProps {
  /** Controls open/close */
  open: boolean;
  onOpenChange: (open: boolean) => void;

  /** Panel header */
  title: string;
  icon?: React.ReactNode;
  iconSize?: number;
  /** Optional React node to replace the plain title text (e.g. an instance switcher dropdown) */
  titleNode?: React.ReactNode;

  /** Optional action buttons rendered in the header (e.g. Import CSV) */
  headerActions?: React.ReactNode;

  /** Panel body content */
  children: React.ReactNode;

  /** Footer button labels */
  primaryLabel?: string;
  secondaryLabel?: string;

  /** Footer button states */
  primaryDisabled?: boolean;
  primaryLoading?: boolean;

  /** Footer button callbacks */
  onPrimaryClick?: () => void;
  onSecondaryClick?: () => void;

  /** Hide the footer entirely (for read-only panels) */
  hideFooter?: boolean;

  /** Tooltip shown on the primary button when it is disabled */
  primaryTooltip?: string;

  /** Override styles for the Dialog.Content container */
  style?: React.CSSProperties;
  /** Secondary (Cancel) button style — `ghost` for text-like actions */
  secondaryVariant?: 'outline' | 'ghost';
}

const TOAST_REGION_SELECTOR = '[data-ph-toast-region]';

/**
 * Portalled `Select` / popper content should use this node as `container` (see Radix Themes
 * `Select.Content`) so menus stack above the drawer and receive clicks. {@link SchemaFormField}
 * reads this automatically; other panels (e.g. workspace actions setup) should pass
 * `container={useContext(WorkspaceRightPanelBodyPortalContext) ?? undefined}` on `Select.Content`.
 *
 * After OAuth, call {@link WorkspaceRightPanelBodyRefreshContextValue.requestRefresh} from
 * {@link useWorkspaceRightPanelBodyRefresh} so the drawer body/footer pick up updated state.
 */
export const WorkspaceRightPanelBodyPortalContext = createContext<HTMLElement | null>(null);

/**
 * After OAuth (or similar) popups, bump this from {@link WorkspaceRightPanel} so nested
 * content + footer re-commit inside `startTransition` — same idea as toolset
 * `UserToolsetConfigDialog` calling `startTransition(onClose)` after verify.
 */
export type WorkspaceRightPanelBodyRefreshContextValue = {
  requestRefresh: () => void;
  /** Increments when {@link requestRefresh} runs; use in `key` to remount sensitive subtrees. */
  refreshNonce: number;
};

export const WorkspaceRightPanelBodyRefreshContext =
  createContext<WorkspaceRightPanelBodyRefreshContextValue | null>(null);

const WORKSPACE_RIGHT_PANEL_BODY_REFRESH_NOOP: WorkspaceRightPanelBodyRefreshContextValue = {
  requestRefresh: () => {},
  refreshNonce: 0,
};

/** No-op when the tree is not under {@link WorkspaceRightPanel}. */
export function useWorkspaceRightPanelBodyRefresh(): WorkspaceRightPanelBodyRefreshContextValue {
  const v = useContext(WorkspaceRightPanelBodyRefreshContext);
  return v ?? WORKSPACE_RIGHT_PANEL_BODY_REFRESH_NOOP;
}

function isInsideToastRegion(node: EventTarget | null | undefined): boolean {
  return node instanceof Element && Boolean(node.closest(TOAST_REGION_SELECTOR));
}

/** Above main app chrome; nested Radix modals (e.g. confirm) should use z-index > PANEL. */
const Z_BACKDROP = 9200;
const Z_PANEL = 9201;

/** Select/Dropdown portals render on `document.body`; must stack above the drawer (`Z_PANEL`). */
export const WORKSPACE_DRAWER_POPPER_Z_INDEX = Z_PANEL + 99;

/**
 * Radix `Dialog` / `AlertDialog` overlays default below the workspace drawer portaled at `Z_PANEL`.
 * Portal nested confirmations into a host at this z-index so overlay + content stack above the drawer.
 */
export const WORKSPACE_DRAWER_MODAL_LAYER_Z_INDEX = Z_PANEL + 200;

/**
 * Creates a fixed full-viewport host on `document.body` for Radix modal `container` when UI is
 * embedded inside `WorkspaceRightPanel`. Host uses `pointer-events: none`; Radix overlay/content
 * re-enable interaction on their nodes.
 */
export function useWorkspaceDrawerNestedModalHost(enabled: boolean): HTMLElement | null {
  const [host, setHost] = useState<HTMLElement | null>(null);
  useLayoutEffect(() => {
    if (!enabled || typeof document === 'undefined') return;
    const el = document.createElement('div');
    el.setAttribute('data-ph-workspace-nested-modal-host', 'true');
    Object.assign(el.style, {
      position: 'fixed',
      inset: '0',
      zIndex: String(WORKSPACE_DRAWER_MODAL_LAYER_Z_INDEX),
      pointerEvents: 'none',
    });
    document.body.appendChild(el);
    setHost(el);
    return () => {
      document.body.removeChild(el);
      setHost(null);
    };
  }, [enabled]);
  return host;
}

// ========================================
// Component
// ========================================

/**
 * Right-side workspace drawer. **Not** implemented with Radix Themes `Dialog` because that
 * package forces `modal` on `Dialog.Root`, which stacks `RemoveScroll` + dismiss layers and
 * breaks pointer events when the body hosts nested dialogs (e.g. `UserToolsetConfigDialog`).
 */
export function WorkspaceRightPanel({
  open,
  onOpenChange,
  title,
  icon,
  iconSize = 24,
  titleNode,
  headerActions,
  children,
  primaryLabel = 'Submit',
  secondaryLabel = 'Cancel',
  primaryDisabled = false,
  primaryLoading = false,
  onPrimaryClick,
  onSecondaryClick,
  hideFooter = false,
  primaryTooltip,
  style,
  secondaryVariant = 'outline',
}: WorkspaceRightPanelProps) {
  const { t } = useTranslation();
  const primaryButtonTooltipText =
    primaryTooltip ??
    (primaryLoading
      ? t('workspace.rightPanel.primaryLoadingHint')
      : primaryDisabled
        ? t('workspace.rightPanel.primaryDisabledHint')
        : undefined);
  const showPrimaryButtonTooltip = Boolean(
    (primaryDisabled || primaryLoading) && primaryButtonTooltipText
  );
  const handleClose = () => onOpenChange(false);
  const handleSecondaryClick = onSecondaryClick ?? handleClose;
  const titleId = useId();
  const panelRef = useRef<HTMLDivElement>(null);
  const [bodyPortalEl, setBodyPortalEl] = useState<HTMLElement | null>(null);
  const [bodyRefreshNonce, setBodyRefreshNonce] = useState(0);

  const requestBodyRefresh = useCallback(() => {
    // Synchronous bump so the same task as Zustand updates can re-render footer + tab chrome.
    // A deferred transition pass helps Radix nested layers flush after that paint.
    setBodyRefreshNonce((n) => n + 1);
    startTransition(() => {
      setBodyRefreshNonce((n) => n + 1);
    });
  }, []);

  const bodyRefreshApi = useMemo<WorkspaceRightPanelBodyRefreshContextValue>(
    () => ({
      requestRefresh: requestBodyRefresh,
      refreshNonce: bodyRefreshNonce,
    }),
    [requestBodyRefresh, bodyRefreshNonce]
  );

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onOpenChange(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onOpenChange]);

  useEffect(() => {
    if (!open || typeof document === 'undefined') return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = prev;
    };
  }, [open]);

  if (!open || typeof document === 'undefined') return null;

  return createPortal(
    /**
     * Portaled nodes are not under the app root `div.radix-themes`, so Radix Themes
     * tokens (`--space-*`, button surfaces, etc.) do not apply unless we add a local Theme.
     */
    <Theme appearance="inherit" hasBackground={false}>
      <Box
        role="presentation"
        aria-hidden
        data-ph-workspace-drawer-backdrop
        onPointerDown={(e) => {
          if (panelRef.current?.contains(e.target as Node)) return;
          if (isInsideToastRegion(e.target)) return;
          onOpenChange(false);
        }}
        style={{
          position: 'fixed',
          inset: 0,
          zIndex: Z_BACKDROP,
          backgroundColor: 'rgba(8, 10, 12, 0.45)',
        }}
      />
      <Box
        ref={panelRef}
        data-ph-workspace-drawer-panel
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        style={{
          position: 'fixed',
          top: 10,
          right: 10,
          bottom: 10,
          width: '37.5rem',
          maxWidth: '100vw',
          maxHeight: 'calc(100vh - 20px)',
          zIndex: Z_PANEL,
          padding: 0,
          margin: 0,
          background: 'var(--effects-translucent)',
          border: '1px solid var(--olive-3)',
          borderRadius: 'var(--radius-2)',
          backdropFilter: 'blur(25px)',
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
          boxShadow: '0 20px 48px 0 rgba(0, 0, 0, 0.25)',
          ...style,
          pointerEvents: 'auto',
        }}
      >
        <VisuallyHidden>
          <span id={titleId}>{title}</span>
        </VisuallyHidden>

        <Flex
          align="center"
          justify="between"
          style={{
            padding: 'var(--space-2) var(--space-2) var(--space-2) var(--space-4)',
            borderBottom: '1px solid var(--olive-3)',
            background: 'var(--effects-translucent)',
            backdropFilter: 'blur(8px)',
            flexShrink: 0,
          }}
        >
          <Flex align="center" gap="2" style={{ minWidth: 0, flex: 1 }}>
            {icon && (
              typeof icon === 'string'
                ? <MaterialIcon name={icon} size={iconSize} color="var(--slate-12)"/>
                : icon
            )}
            {titleNode ?? (
              <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }} truncate>
                {title}
              </Text>
            )}
          </Flex>

          <Flex align="center" gap="2" style={{ flexShrink: 0 }}>
            {headerActions}
            <IconButton
              variant="ghost"
              color="gray"
              size="2"
              onClick={handleClose}
              style={{ cursor: 'pointer' }}
            >
              <MaterialIcon name="close" size={18} color="var(--slate-11)" />
            </IconButton>
          </Flex>
        </Flex>

        <Box
          ref={setBodyPortalEl}
          style={{
            flex: 1,
            overflow: 'auto',
            padding: 'var(--space-4)',
            background: 'var(--effects-translucent)',
            minHeight: 0,
          }}
        >
          <WorkspaceRightPanelBodyRefreshContext.Provider value={bodyRefreshApi}>
            <WorkspaceRightPanelBodyPortalContext.Provider value={bodyPortalEl}>
              {children}
            </WorkspaceRightPanelBodyPortalContext.Provider>
          </WorkspaceRightPanelBodyRefreshContext.Provider>
        </Box>

        {!hideFooter && (
          <Flex
            align="center"
            justify="end"
            wrap="wrap"
            gap="2"
            style={{
            padding: 'var(--space-2) var(--space-2) var(--space-2) var(--space-4)',
              borderTop: '1px solid var(--olive-3)',
              background: 'var(--effects-translucent)',
              backdropFilter: 'blur(8px)',
              flexShrink: 0,
            }}
          >
            <Button
              variant={secondaryVariant}
              color="gray"
              size="2"
              onClick={handleSecondaryClick}
              disabled={primaryLoading}
              style={{ cursor: primaryLoading ? 'not-allowed' : 'pointer' }}
            >
              {secondaryLabel}
            </Button>
            {showPrimaryButtonTooltip && primaryButtonTooltipText ? (
              <Tooltip content={primaryButtonTooltipText}>
                <span
                  className="inline-flex"
                  style={{ maxWidth: '100%' }}
                >
                  <LoadingButton
                    variant="solid"
                    size="2"
                    onClick={onPrimaryClick}
                    disabled={primaryDisabled}
                    loading={primaryLoading}
                    style={{
                      backgroundColor:
                        primaryDisabled || primaryLoading ? 'var(--slate-6)' : 'var(--emerald-9)',
                    }}
                  >
                    {primaryLabel}
                  </LoadingButton>
                </span>
              </Tooltip>
            ) : (
              <LoadingButton
                variant="solid"
                size="2"
                onClick={onPrimaryClick}
                disabled={primaryDisabled}
                loading={primaryLoading}
                style={{
                  backgroundColor:
                    primaryDisabled || primaryLoading ? 'var(--slate-6)' : 'var(--emerald-9)',
                }}
              >
                {primaryLabel}
              </LoadingButton>
            )}
          </Flex>
        )}
      </Box>
    </Theme>,
    document.body
  );
}

export type { WorkspaceRightPanelProps };
