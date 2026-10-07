'use client';

import { useCallback, useEffect, useMemo, useRef, useState, Suspense } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Flex, TextField } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { ServiceGate } from '@/app/components/ui/service-gate';
import { FilePreviewSidebar } from '@/app/components/file-preview';
import { KnowledgeBaseApi } from '@/app/(main)/knowledge-base/api';
import { toast } from '@/lib/store/toast-store';
import { ArtifactsApi } from './api';
import { useArtifactsStore, DEFAULT_PAGE_SIZE } from './store';
import { parseArtifactsParams, serializeArtifactsParams } from './url-params';
import type { ArtifactListItem, ArtifactsSortField } from './types';
import { ArtifactsHeader } from './components/header';
import { ArtifactsFilterBar } from './components/filter-bar';
import { ArtifactsDataTable } from './components/artifacts-data-table';

function needsPdfConversion(item: ArtifactListItem): boolean {
  const mime = (item.mimeType || '').toLowerCase();
  return mime.includes('presentation') || /\.(ppt|pptx)$/i.test(item.name);
}

function ArtifactsPageContent() {
  const { t } = useTranslation();
  const router = useRouter();
  const searchParams = useSearchParams();
  const [isSearchActive, setIsSearchActive] = useState(false);
  const [previewItem, setPreviewItem] = useState<ArtifactListItem | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewBlob, setPreviewBlob] = useState<Blob | undefined>();
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | undefined>();
  const [previewVersion, setPreviewVersion] = useState<number | undefined>();
  const [latestVersion, setLatestVersion] = useState<number | undefined>();
  const [isSwitchingVersion, setIsSwitchingVersion] = useState(false);
  const blobUrlRef = useRef<string | null>(null);
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
    });
    if (parsed.search) setIsSearchActive(true);
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
    }),
    [page, limit, search, artifactTypes, dateFrom, dateTo, sortBy, sortOrder],
  );

  const load = useCallback(async () => {
    const requestId = ++listRequestIdRef.current;
    setLoading(true);
    setError(null);
    try {
      const result = await ArtifactsApi.list(listParams);
      if (requestId !== listRequestIdRef.current) return;
      setItems(result.items, result.pagination);
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
    const qs = serializeArtifactsParams({
      search,
      artifactTypes,
      dateFrom,
      dateTo,
      sortBy,
      sortOrder,
      page,
      viewMode,
    });
    if (qs === searchParams.toString()) return;
    const next = qs ? `/artifacts/?${qs}` : '/artifacts/';
    router.replace(next, { scroll: false });
  }, [search, artifactTypes, dateFrom, dateTo, sortBy, sortOrder, page, viewMode, router, searchParams]);

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
    try {
      const blob = await KnowledgeBaseApi.streamRecord(item.artifactId, {
        version,
        convertTo: needsPdfConversion(item) ? 'pdf' : undefined,
      });
      if (id !== previewRequestIdRef.current) return;
      revokePreviewUrl();
      const url = URL.createObjectURL(blob);
      blobUrlRef.current = url;
      setPreviewUrl(url);
      setPreviewBlob(blob);
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

  const handleVersionChange = useCallback(async (version: number) => {
    if (!previewItem) return;
    setIsSwitchingVersion(true);
    await loadPreview(previewItem, version);
  }, [loadPreview, previewItem]);

  const handleDownload = useCallback(async (item: ArtifactListItem) => {
    try {
      await KnowledgeBaseApi.streamDownloadRecord(item.artifactId, item.name, {
        version: item.version,
      });
    } catch {
      toast.error(t('artifacts.downloadFailed', { defaultValue: "Couldn't download this file" }));
    }
  }, [t]);

  const handleOpenChat = useCallback((item: ArtifactListItem) => {
    if (!item.conversationId) return;
    router.push(`/chat/?conversationId=${encodeURIComponent(item.conversationId)}`);
  }, [router]);

  const handleSort = useCallback((field: ArtifactsSortField) => {
    const nextOrder = sortBy === field && sortOrder === 'asc' ? 'desc' : 'asc';
    setSort(field, nextOrder);
  }, [setSort, sortBy, sortOrder]);

  return (
    <Flex direction="column" style={{ height: '100%', minHeight: 0 }}>
      <ArtifactsHeader
        onFind={() => setIsSearchActive((open) => !open)}
        onRefresh={() => void load()}
        isSearchActive={isSearchActive}
        viewMode={viewMode}
        onViewModeChange={setViewMode}
      />
      {isSearchActive && (
        <Flex style={{ padding: 'var(--space-2) var(--space-4)' }}>
          <TextField.Root
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t('artifacts.searchPlaceholder', { defaultValue: 'Search artifacts' })}
            style={{ width: '100%' }}
          />
        </Flex>
      )}
      <ArtifactsFilterBar />
      <ArtifactsDataTable
        items={items}
        viewMode={viewMode}
        isLoading={isLoading}
        error={error}
        sortBy={sortBy}
        sortOrder={sortOrder}
        onSort={handleSort}
        onPreview={(item) => void openPreview(item)}
        onDownload={(item) => void handleDownload(item)}
        onOpenChat={handleOpenChat}
        pagination={{ page, limit, totalCount, totalPages }}
        onPageChange={setPage}
      />
      {previewItem && (
        <FilePreviewSidebar
          open
          source="artifacts"
          hideFileDetails
          showDownload
          file={{
            id: previewItem.artifactId,
            name: previewItem.name,
            url: previewUrl || '',
            type: previewItem.mimeType || 'application/octet-stream',
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
            if (!open) {
              previewRequestIdRef.current += 1;
              setPreviewItem(null);
              setPreviewUrl(null);
              setPreviewBlob(undefined);
              setPreviewLoading(false);
              setIsSwitchingVersion(false);
              revokePreviewUrl();
            }
          }}
        />
      )}
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
