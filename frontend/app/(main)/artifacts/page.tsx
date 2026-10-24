'use client';

import { useCallback, useEffect, useMemo, useRef, useState, Suspense } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Flex, TextField } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useDebouncedSearch } from '@/knowledge-base/hooks/use-debounced-search';
import { useTranslation } from 'react-i18next';
import { ServiceGate } from '@/app/components/ui/service-gate';
import { FilePreviewSidebar } from '@/app/components/file-preview';
import {
  isLegacyWordDocFile,
  isPresentationFile,
  resolvePreviewMimeAfterStream,
} from '@/app/components/file-preview/utils';
import { KnowledgeBaseApi } from '@/app/(main)/knowledge-base/api';
import { ShareSidebar } from '@/app/components/share';
import { toast } from '@/lib/store/toast-store';
import { ConfirmationDialog } from '@/workspace/components';
import { isProcessedError } from '@/lib/api';
import { ArtifactsApi } from './api';
import { createArtifactShareAdapter } from './share-adapter';
import { useArtifactsStore, DEFAULT_PAGE_SIZE } from './store';
import { parseArtifactsParams, serializeArtifactsParams } from './url-params';
import type { ArtifactListItem, ArtifactsSortField } from './types';
import { ArtifactsHeader } from './components/header';
import { ArtifactsFilterBar } from './components/filter-bar';
import { ArtifactsDataTable } from './components/artifacts-data-table';

function ArtifactsPageContent() {
  const { t } = useTranslation();
  const router = useRouter();
  const searchParams = useSearchParams();
  const [isSearchActive, setIsSearchActive] = useState(false);
  // Seeded from the URL so the debounced value starts equal to the store's
  // search; starting empty would clear a linked search and reset its page.
  const [searchInput, setSearchInput] = useState(() => parseArtifactsParams(searchParams).search);
  const debouncedSearch = useDebouncedSearch(searchInput, 300);
  const [previewItem, setPreviewItem] = useState<ArtifactListItem | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewBlob, setPreviewBlob] = useState<Blob | undefined>();
  const [previewType, setPreviewType] = useState<string | undefined>();
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | undefined>();
  const [previewVersion, setPreviewVersion] = useState<number | undefined>();
  const [latestVersion, setLatestVersion] = useState<number | undefined>();
  const [isSwitchingVersion, setIsSwitchingVersion] = useState(false);
  const [shareItem, setShareItem] = useState<ArtifactListItem | null>(null);
  const [isShareOpen, setIsShareOpen] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<ArtifactListItem | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const blobUrlRef = useRef<string | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const listRequestIdRef = useRef(0);
  const previewRequestIdRef = useRef(0);

  const items = useArtifactsStore((s) => s.items);
  const page = useArtifactsStore((s) => s.page);
  const limit = useArtifactsStore((s) => s.limit);
  const totalCount = useArtifactsStore((s) => s.totalCount);
  const totalPages = useArtifactsStore((s) => s.totalPages);
  const search = useArtifactsStore((s) => s.search);
  const artifactTypes = useArtifactsStore((s) => s.artifactTypes);
  const dateFrom = useArtifactsStore((s) => s.dateFrom);
  const dateTo = useArtifactsStore((s) => s.dateTo);
  const sortBy = useArtifactsStore((s) => s.sortBy);
  const sortOrder = useArtifactsStore((s) => s.sortOrder);
  const viewMode = useArtifactsStore((s) => s.viewMode);
  const ownership = useArtifactsStore((s) => s.ownership);
  const isLoading = useArtifactsStore((s) => s.isLoading);
  const error = useArtifactsStore((s) => s.error);
  const setItems = useArtifactsStore((s) => s.setItems);
  const setSearch = useArtifactsStore((s) => s.setSearch);
  const setPage = useArtifactsStore((s) => s.setPage);
  const setSort = useArtifactsStore((s) => s.setSort);
  const setViewMode = useArtifactsStore((s) => s.setViewMode);
  const setLoading = useArtifactsStore((s) => s.setLoading);
  const setError = useArtifactsStore((s) => s.setError);
  const hydrateFromUrl = useArtifactsStore((s) => s.hydrateFromUrl);
  const removeItem = useArtifactsStore((s) => s.removeItem);
  const clearFilters = useArtifactsStore((s) => s.clearFilters);

  const hydratedRef = useRef(false);
  const skipUrlSyncRef = useRef(true);

  useEffect(() => {
    if (hydratedRef.current) return;
    const parsed = parseArtifactsParams(searchParams);
    hydrateFromUrl({
      search: parsed.search,
      artifactTypes: parsed.artifactTypes,
      dateFrom: parsed.dateFrom,
      dateTo: parsed.dateTo,
      sortBy: parsed.sortBy,
      sortOrder: parsed.sortOrder,
      page: parsed.page,
      viewMode: parsed.viewMode,
      ownership: parsed.ownership,
    });
    if (parsed.search) {
      setSearchInput(parsed.search);
      setIsSearchActive(true);
    }
    hydratedRef.current = true;
  }, [searchParams, hydrateFromUrl]);

  const listParams = useMemo(
    () => ({
      page,
      limit: limit || DEFAULT_PAGE_SIZE,
      search: search || undefined,
      artifactTypes: artifactTypes.length ? artifactTypes : undefined,
      dateFrom,
      dateTo,
      sortBy,
      sortOrder,
      ownership,
    }),
    [page, limit, search, artifactTypes, dateFrom, dateTo, sortBy, sortOrder, ownership],
  );

  const load = useCallback(async () => {
    const requestId = ++listRequestIdRef.current;
    setLoading(true);
    setError(null);
    try {
      const result = await ArtifactsApi.list(listParams);
      if (requestId !== listRequestIdRef.current) return;
      setItems(result.items, result.pagination, result.counts);
    } catch {
      if (requestId !== listRequestIdRef.current) return;
      setError(t('artifacts.loadFailed', { defaultValue: "Couldn't load artifacts" }));
    } finally {
      if (requestId === listRequestIdRef.current) {
        setLoading(false);
      }
    }
  }, [listParams, setError, setItems, setLoading, t]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!hydratedRef.current) return;
    if (skipUrlSyncRef.current) {
      skipUrlSyncRef.current = false;
      return;
    }
    // The deep-link effect owns the URL until it drops the id.
    if (searchParams.has('artifactId')) return;
    const qs = serializeArtifactsParams({
      search,
      artifactTypes,
      dateFrom,
      dateTo,
      sortBy,
      sortOrder,
      page,
      viewMode,
      ownership,
    });
    if (qs === searchParams.toString()) return;
    const next = qs ? `/artifacts/?${qs}` : '/artifacts/';
    router.replace(next, { scroll: false });
  }, [search, artifactTypes, dateFrom, dateTo, sortBy, sortOrder, page, viewMode, ownership, router, searchParams]);

  const revokePreviewUrl = useCallback(() => {
    if (blobUrlRef.current) {
      URL.revokeObjectURL(blobUrlRef.current);
      blobUrlRef.current = null;
    }
  }, []);

  useEffect(() => () => revokePreviewUrl(), [revokePreviewUrl]);

  const loadPreview = useCallback(async (item: ArtifactListItem, version?: number, requestId?: number) => {
    const id = requestId ?? ++previewRequestIdRef.current;
    setPreviewLoading(true);
    setPreviewError(undefined);
    const mimeType = item.mimeType ?? undefined;
    const streamAsPdf =
      isPresentationFile(mimeType, item.name) ||
      isLegacyWordDocFile(mimeType, item.name);
    try {
      const blob = await KnowledgeBaseApi.streamRecord(item.artifactId, {
        version,
        ...(streamAsPdf ? { convertTo: 'application/pdf' } : {}),
      });
      if (id !== previewRequestIdRef.current) return;
      revokePreviewUrl();
      const url = URL.createObjectURL(blob);
      blobUrlRef.current = url;
      setPreviewUrl(url);
      setPreviewBlob(blob);
      setPreviewType(resolvePreviewMimeAfterStream(item.mimeType || '', item.name, blob, streamAsPdf));
      setPreviewVersion(version ?? item.version);
    } catch {
      if (id !== previewRequestIdRef.current) return;
      setPreviewError(t('artifacts.previewFailed', { defaultValue: "Couldn't preview this file" }));
    } finally {
      if (id === previewRequestIdRef.current) {
        setPreviewLoading(false);
        setIsSwitchingVersion(false);
      }
    }
  }, [revokePreviewUrl, t]);

  const openPreview = useCallback(async (item: ArtifactListItem) => {
    const requestId = ++previewRequestIdRef.current;
    setPreviewItem(item);
    setPreviewType(item.mimeType || 'application/octet-stream');
    setLatestVersion(item.version);
    setPreviewVersion(item.version);
    try {
      const versions = await ArtifactsApi.listVersions(item.artifactId);
      if (requestId !== previewRequestIdRef.current) return;
      const maxVersion = versions.reduce((max, entry) => Math.max(max, entry.version), item.version);
      setLatestVersion(maxVersion);
    } catch {
      if (requestId !== previewRequestIdRef.current) return;
      setLatestVersion(item.version);
    }
    if (requestId !== previewRequestIdRef.current) return;
    await loadPreview(item, item.version, requestId);
  }, [loadPreview]);

  // A shared-artifact notification links to `?tab=shared&artifactId=…`, also
  // while this page is already open: show that tab, open the preview, and drop
  // the id so a reload does not reopen it.
  const deepLinkId = searchParams.get('artifactId');
  useEffect(() => {
    if (!deepLinkId) return;
    const parsed = parseArtifactsParams(searchParams);
    hydrateFromUrl(parsed);
    setSearchInput(parsed.search);
    const qs = serializeArtifactsParams(parsed);
    router.replace(qs ? `/artifacts/?${qs}` : '/artifacts/', { scroll: false });
    ArtifactsApi.get(deepLinkId)
      .then((detail) => openPreview(detail))
      .catch(() => {
        toast.error(t('artifacts.notAvailable'));
      });
  }, [deepLinkId, searchParams, hydrateFromUrl, router, openPreview, t]);

  const handleVersionChange = useCallback(async (version: number) => {
    if (!previewItem) return;
    setIsSwitchingVersion(true);
    await loadPreview(previewItem, version);
  }, [loadPreview, previewItem]);

  const handleDownload = useCallback(async (item: ArtifactListItem, version?: number) => {
    try {
      await KnowledgeBaseApi.streamDownloadRecord(item.artifactId, item.name, {
        version: version ?? item.version,
        suppressErrorToast: true,
      });
    } catch {
      toast.error(t('artifacts.downloadFailed', { defaultValue: "Couldn't download this file" }));
    }
  }, [t]);

  const closePreview = useCallback(() => {
    previewRequestIdRef.current += 1;
    setPreviewItem(null);
    setPreviewUrl(null);
    setPreviewBlob(undefined);
    setPreviewType(undefined);
    setPreviewLoading(false);
    setIsSwitchingVersion(false);
    revokePreviewUrl();
  }, [revokePreviewUrl]);

  const handleConfirmDelete = useCallback(async () => {
    if (!pendingDelete || isDeleting) return;
    const item = pendingDelete;
    setIsDeleting(true);
    try {
      await ArtifactsApi.delete(item.artifactId);
    } catch (error) {
      if (!(isProcessedError(error) && error.statusCode === 404)) {
        // apiClient has already toasted the reason; keep the row.
        setIsDeleting(false);
        setPendingDelete(null);
        return;
      }
    }
    const pageBefore = useArtifactsStore.getState().page;
    removeItem(item.artifactId);
    // The row and its menu button are gone; keep keyboard focus in the list.
    listRef.current?.focus();
    if (previewItem?.artifactId === item.artifactId) closePreview();
    toast.success(t('artifacts.deleteSuccess', { name: item.name }));
    setIsDeleting(false);
    setPendingDelete(null);
    // A page change already triggers a reload; otherwise refill the page from the server.
    if (useArtifactsStore.getState().page === pageBefore) void load();
  }, [closePreview, isDeleting, load, pendingDelete, previewItem, removeItem, t]);

  const handleOpenChat = useCallback((item: ArtifactListItem) => {
    if (!item.conversationId) return;
    router.push(`/chat/?conversationId=${encodeURIComponent(item.conversationId)}`);
  }, [router]);

  const handleShare = useCallback((item: ArtifactListItem) => {
    setShareItem(item);
    setIsShareOpen(true);
  }, []);

  const shareAdapter = useMemo(
    () => (shareItem ? createArtifactShareAdapter(shareItem.artifactId) : null),
    [shareItem],
  );

  useEffect(() => {
    // Read the store directly: depending on `search` would also run this when
    // the URL or Clear filters sets it, and push a stale debounced value back.
    if (debouncedSearch !== useArtifactsStore.getState().search) setSearch(debouncedSearch);
  }, [debouncedSearch, setSearch]);

  const toggleSearch = useCallback(() => {
    if (isSearchActive) {
      setSearchInput('');
      setSearch('');
    }
    setIsSearchActive(!isSearchActive);
  }, [isSearchActive, setSearch]);

  const handleClearFilters = useCallback(() => {
    setSearchInput('');
    clearFilters();
  }, [clearFilters]);

  const hasActiveFilters = !!search || artifactTypes.length > 0 || dateFrom !== undefined || dateTo !== undefined;

  const handleSort = useCallback((field: ArtifactsSortField) => {
    const nextOrder = sortBy === field && sortOrder === 'asc' ? 'desc' : 'asc';
    setSort(field, nextOrder);
  }, [setSort, sortBy, sortOrder]);

  return (
    <Flex direction="column" style={{ height: '100%', minHeight: 0 }}>
      <ArtifactsHeader
        onFind={toggleSearch}
        onRefresh={() => void load()}
        isSearchActive={isSearchActive}
        viewMode={viewMode}
        onViewModeChange={setViewMode}
      />
      {isSearchActive && (
        <Flex style={{ padding: 'var(--space-2) var(--space-4)' }}>
          <TextField.Root
            autoFocus
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') toggleSearch();
            }}
            placeholder={t('artifacts.searchPlaceholder')}
            aria-label={t('artifacts.searchPlaceholder')}
            style={{ width: '100%' }}
          >
            <TextField.Slot>
              <MaterialIcon name="search" size={16} color="var(--slate-9)" />
            </TextField.Slot>
          </TextField.Root>
        </Flex>
      )}
      <ArtifactsFilterBar />
      <ArtifactsDataTable
        items={items}
        viewMode={viewMode}
        ownership={ownership}
        isLoading={isLoading}
        error={error}
        hasActiveFilters={hasActiveFilters}
        onRetry={() => void load()}
        onClearFilters={handleClearFilters}
        listRef={listRef}
        sortBy={sortBy}
        sortOrder={sortOrder}
        onSort={handleSort}
        onPreview={(item) => void openPreview(item)}
        onDownload={(item) => void handleDownload(item)}
        onOpenChat={handleOpenChat}
        onShare={handleShare}
        onDelete={setPendingDelete}
        pagination={{ page, limit, totalCount, totalPages }}
        onPageChange={setPage}
      />
      {previewItem && (
        <FilePreviewSidebar
          open
          source="artifacts"
          hideFileDetails
          showDownload
          onDownload={() => {
            void handleDownload(previewItem, previewVersion);
          }}
          file={{
            id: previewItem.artifactId,
            name: previewItem.name,
            url: previewUrl || '',
            type: previewType || previewItem.mimeType || 'application/octet-stream',
            size: previewItem.sizeInBytes ?? undefined,
            blob: previewBlob,
            version: previewVersion,
          }}
          latestVersion={latestVersion}
          onVersionChange={handleVersionChange}
          isSwitchingVersion={isSwitchingVersion}
          isLoading={previewLoading}
          error={previewError}
          onOpenChange={(open) => {
            if (!open) closePreview();
          }}
        />
      )}
      {shareAdapter && (
        <ShareSidebar open={isShareOpen} onOpenChange={setIsShareOpen} adapter={shareAdapter} />
      )}
      <ConfirmationDialog
        open={!!pendingDelete}
        onOpenChange={(open) => {
          if (!open && !isDeleting) setPendingDelete(null);
        }}
        title={t('artifacts.deleteTitle')}
        message={t('artifacts.deleteConfirmMessage', { name: pendingDelete?.name ?? '' })}
        confirmLabel={t('action.delete')}
        cancelLabel={t('action.cancel')}
        confirmVariant="danger"
        isLoading={isDeleting}
        confirmLoadingLabel={t('action.deleting')}
        onConfirm={() => void handleConfirmDelete()}
      />
    </Flex>
  );
}

export default function ArtifactsPage() {
  return (
    <ServiceGate services={['connector']}>
      <Suspense>
        <ArtifactsPageContent />
      </Suspense>
    </ServiceGate>
  );
}
