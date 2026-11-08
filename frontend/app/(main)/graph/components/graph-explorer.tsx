'use client';

import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { Box, Flex, Spinner, Text } from '@radix-ui/themes';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import { GraphApi } from '../api';
import { ExplorerController } from '../controller';
import { probeCapabilities } from '../engine/capabilities';
import { createCytoscapeEngine } from '../engine/cytoscape-engine';
import { Revalidator, type VerifyState } from '../engine/revalidate';
import { chooseTier, FrameMeter, qualityFor, type Capabilities } from '../engine/tiers';
import type { GraphEngine } from '../engine/types';
import { emptyStateKey, noticesFor, type NoticeAction } from '../status';
import { useGraphStore } from '../store';
import { parseGraphEntry, parseGraphUrl, parseRepoScope, parseTrailPosition, serializeGraphUrl } from '../url-params';
import type { Direction, NodeId } from '../types';
import { DetailPanel } from './detail-panel';
import { Legend } from './legend';
import { Notices } from './notices';
import { SearchBox } from './search-box';
import { TableTwin } from './table-twin';
import { Toolbar } from './toolbar';

/** A spinner appears only if the wait is longer than this, so quick answers do not flicker. */
const SPINNER_DELAY_MS = 200;

/**
 * The explorer. It wires the pieces and holds no rule of its own: the controller decides
 * what to ask, the model what is shown, the policy on the server what may be seen.
 */
export function GraphExplorer() {
  const { t } = useTranslation();
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const store = useGraphStore();
  const controller = useMemo(() => new ExplorerController(GraphApi), []);
  const { model, failure, loading, lastAdded, selectionGone, moving } = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot);

  const [caps, setCaps] = useState<Capabilities | null>(null);
  const [tableView, setTableView] = useState(false);
  const [searchFocus, setSearchFocus] = useState(0);
  const [hover, setHover] = useState<{ id: NodeId; x: number; y: number } | null>(null);
  const [waiting, setWaiting] = useState(false);
  /** Bumped when a followed Back or Forward has finished, so the address is looked at again once `following` is clear. */
  const [followed, setFollowed] = useState(0);
  const [announcement, setAnnouncement] = useState('');
  const main = useRef<HTMLDivElement>(null);
  /** A link named where to start and it has not resolved yet: its parameters stay in the address bar until it does. */
  const entryPending = useRef(false);
  /** The place on the trail the address named after Back or Forward, until the view has moved there. */
  const following = useRef<string | null>(null);
  /** The addresses this page wrote that the router has not shown back yet. The router applies a write a moment later,
   * and that late arrival must never read as a Back or Forward. */
  const written = useRef<string[]>([]);
  /** The browser landed on an entry and the page followed it: the entry already is the visit, so its address is corrected in place, never pushed. */
  const fromBrowser = useRef(false);
  const repoScope = useMemo(() => parseRepoScope(params), []);
  const host = useRef<HTMLDivElement>(null);
  const canvasBox = useRef<HTMLDivElement>(null);
  const engine = useRef<GraphEngine | null>(null);
  const meter = useRef(new FrameMeter());

  useEffect(() => setCaps(probeCapabilities()), []);
  // Leaving the page leaves nothing behind for the next visit: no trail, selection or verify state.
  useEffect(() => () => useGraphStore.getState().reset(), []);
  const tier = caps ? chooseTier(caps) : 'canvas';
  const quality = useMemo(() => (caps ? qualityFor(caps, tier, store.qualityStep) : null), [caps, tier, store.qualityStep]);
  const tableOnly = Boolean(quality?.tableOnly) || tableView;
  const hasNodes = Object.keys(model.nodes).length > 0;

  // The link is the starting state: ids and filters only. Applied once per mount, then the store leads. A mount that
  // is replayed (React Strict Mode runs the effects, their cleanup and the effects again in development) applies it
  // again, because the cleanup above reset the store.
  const started = useRef(false);
  useEffect(() => {
    started.current = true;
    const url = parseGraphUrl(params);
    store.setFilters({ direction: url.direction, edgeTypes: url.edgeTypes, includeTests: url.includeTests });
    const entry = parseGraphEntry(params);
    entryPending.current = Boolean(url.node || entry);
    if (url.node) void controller.open(url.node);
    else if (entry) void controller.openAddress({ source: 'code', ...entry, includeTests: url.includeTests });
    return () => {
      started.current = false;
    };
  }, []);

  useEffect(() => {
    // What the store holds now, not what this render saw: the first run comes in the same pass that applied the
    // filters of the link, and must not write the defaults over them.
    const now = useGraphStore.getState();
    const next = serializeGraphUrl(
      { node: now.node, direction: now.direction, edgeTypes: now.edgeTypes, includeTests: now.includeTests },
      now.trailIndex,
    ).toString();
    if (now.node) entryPending.current = false;
    // A link that has not resolved (or that failed for a reason a retry or reload can fix) keeps its address.
    if (entryPending.current) {
      if (failure && failure !== 'unavailable' && failure !== 'unexpected') entryPending.current = false;
      else return;
    }
    // The address leads while the view is catching up with Back or Forward: a filter finishing then must not write the old node back.
    if (!started.current || following.current || moving) return;
    const followedBrowser = fromBrowser.current;
    fromBrowser.current = false;
    if (next === params.toString()) return;
    const url = next ? `${pathname}?${next}` : pathname;
    written.current.push(next);
    const shown = parseGraphUrl(params).node;
    const shownAt = parseTrailPosition(params);
    // Moving to another place on the trail from the page is a step the browser's Back can undo; the first centre, a
    // rewritten entry link, a filter change and the entry a Back or Forward just landed on replace the entry instead.
    const moved = now.node !== shown || (shownAt !== undefined && shownAt !== now.trailIndex);
    if (shown && now.node && moved && !followedBrowser) router.push(url, { scroll: false });
    else router.replace(url, { scroll: false });
  }, [store.node, store.trailIndex, store.direction, store.edgeTypes, store.includeTests, failure, moving, followed]);

  // The browser's Back and Forward change the address: follow it to the place on the trail it names.
  const search = params.toString();
  const urlNode = parseGraphUrl(params).node;
  const urlAt = parseTrailPosition(params);
  useEffect(() => {
    const now = useGraphStore.getState();
    // Our own write arriving is not a navigation (and neither is any write before it).
    const arrived = written.current.lastIndexOf(search);
    if (arrived >= 0) {
      written.current.splice(0, arrived + 1);
      return;
    }
    if (!started.current || entryPending.current || !urlNode) return;
    const move = `${urlNode}@${urlAt}`;
    if (following.current === move) return; // already on its way
    // The view matching the address means nothing while an earlier move is still loading: it is about to leave it.
    if (!following.current && urlNode === now.node && (urlAt === undefined || urlAt === now.trailIndex)) return;
    following.current = move;
    fromBrowser.current = true;
    void controller.follow(urlNode, urlAt).finally(() => {
      if (following.current === move) following.current = null;
      setFollowed((n) => n + 1);
    });
  }, [search]);

  // The canvas engine lives while the canvas is on screen. The canvas element is always there (the
  // empty prompt sits over it), so the engine is ready before the first node arrives.
  useEffect(() => {
    if (!host.current || !quality || quality.tableOnly || tableView) return;
    const e = createCytoscapeEngine();
    engine.current = e;
    let alive = true;
    void e
      .mount(host.current, quality, {
        onSelect: (id) => {
          useGraphStore.getState().select(id);
          controller.acknowledgeSelection();
        },
        onExpand: (id) => void controller.expand([id]),
        onHover: (id, point) => {
          // Placed inside the picture here, where its size can be read, so the card is never cut off at an edge.
          const box = canvasBox.current;
          setHover(
            id && point
              ? {
                  id,
                  x: Math.max(0, Math.min(point.x + 12, (box?.clientWidth ?? 9999) - 332)),
                  y: Math.max(0, Math.min(point.y + 12, (box?.clientHeight ?? 9999) - 64)),
                }
              : null,
          );
        },
        onFrame: (at, ms) => {
          if (meter.current.observe(at, ms)) useGraphStore.getState().stepDown();
        },
      })
      .then(() => {
        if (!alive) return;
        e.setModel(controller.getSnapshot().model);
        e.setFocus(useGraphStore.getState().node);
        e.select(useGraphStore.getState().selection);
      });
    return () => {
      alive = false;
      e.destroy();
      engine.current = null;
    };
    // The engine is rebuilt only when the canvas appears or goes; quality changes go through setQuality.
  }, [Boolean(quality), tableOnly]);

  useEffect(() => {
    engine.current?.setModel(model);
  }, [model]);
  useEffect(() => {
    if (quality) engine.current?.setQuality(quality);
  }, [quality]);
  useEffect(() => {
    engine.current?.select(store.selection);
  }, [store.selection]);
  useEffect(() => {
    engine.current?.setFocus(store.node);
  }, [store.node]);

  // Access is re-checked on a timer, on focus and when the tab returns. When it could not be checked for
  // long enough the picture is cleared; when a check works again the view comes back.
  useEffect(() => {
    let previous: VerifyState = 'ok';
    const revalidator = new Revalidator({
      getIds: controller.ids,
      call: async (ids, signal) =>
        (await GraphApi.revalidate({ source: 'code', nodeIds: ids, includeTests: useGraphStore.getState().includeTests }, signal)).visible,
      onResult: (asked, visible) => controller.revoke(asked, visible),
      onState: (state: VerifyState) => {
        useGraphStore.getState().setVerify(state);
        if (state === 'blank') controller.clear();
        else if (previous === 'blank' && state === 'ok') void controller.restoreFocus();
        previous = state;
      },
    });
    revalidator.start();
    const onVisibility = () => revalidator.setVisible(document.visibilityState === 'visible');
    document.addEventListener('visibilitychange', onVisibility);
    window.addEventListener('focus', onVisibility);
    return () => {
      document.removeEventListener('visibilitychange', onVisibility);
      window.removeEventListener('focus', onVisibility);
      revalidator.stop();
    };
  }, [controller]);

  // `/` goes to the search box and Escape clears the selection, unless the person is typing.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(target?.tagName ?? '');
      // A menu, list or dialog that handled the key (Escape closing the repository list) has had its say.
      if (event.defaultPrevented || target?.closest?.('[role=listbox],[role=menu],[role=dialog]')) return;
      if (event.key === '/' && !typing && !event.metaKey && !event.ctrlKey) {
        event.preventDefault();
        setSearchFocus((n) => n + 1);
      } else if (event.key === 'Escape' && !typing) {
        useGraphStore.getState().select(null);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  useEffect(() => {
    if (!loading) {
      setWaiting(false);
      return;
    }
    const timer = setTimeout(() => setWaiting(true), SPINNER_DELAY_MS);
    return () => clearTimeout(timer);
  }, [loading]);

  // The result of a call is said in words, so a screen reader hears what the picture shows.
  const nodeCount = Object.keys(model.nodes).length;
  const announced = useRef(0);
  useEffect(() => {
    if (loading || nodeCount === announced.current) return;
    announced.current = nodeCount;
    setAnnouncement(lastAdded ? t('graph.ui.announce.added', { n: lastAdded }) : t('graph.ui.announce.opened', { n: nodeCount }));
  }, [nodeCount, loading, lastAdded, t]);

  // When what had focus goes away (a revoked node's row, a closed panel) focus lands on the explorer, not on the page.
  const hadSelection = useRef(false);
  useEffect(() => {
    // Only when a selection goes away, never on page load.
    if (store.selection === null && hadSelection.current && (document.activeElement === document.body || document.activeElement === null)) {
      main.current?.focus({ preventScroll: true });
    }
    hadSelection.current = store.selection !== null;
  }, [store.selection, nodeCount]);

  const select = useCallback((id: NodeId | null) => {
    store.select(id);
    controller.acknowledgeSelection();
  }, [controller, store]);

  const open = useCallback((id: NodeId) => void controller.open(id), [controller]);
  const expand = useCallback((id: NodeId) => void controller.expand([id]), [controller]);
  const showMore = useCallback((id: NodeId) => void controller.showMore(id), [controller]);
  const onAction = (action: NoticeAction) => {
    if (action === 'retry') void controller.retry();
    else store.restoreQuality();
  };

  const empty = emptyStateKey(model.status);
  const notices = noticesFor({
    status: empty ? 'ready' : model.status,
    incomplete: model.incomplete,
    verify: store.verify,
    failure,
    simplified: store.qualityStep > 0,
    nodeCount: Object.keys(model.nodes).length,
    softCap: quality?.softCap,
    hardCap: quality?.hardCap,
    includeTests: store.includeTests,
    lastAdded,
    tableOnly: Boolean(quality?.tableOnly),
  });
  const hovered = hover ? model.nodes[hover.id] : undefined;

  return (
    <Flex ref={main} tabIndex={-1} className="graph-explorer" direction="column" gap="3" p="4" style={{ height: '100%', minHeight: 0, minWidth: 0, outline: 'none' }}>
      <div role="status" aria-live="polite" style={{ position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)', whiteSpace: 'nowrap' }}>
        {announcement}
      </div>
      <SearchBox
        includeTests={store.includeTests}
        initialRepo={repoScope}
        focusSignal={searchFocus}
        onPick={({ repo, filePath, qualifiedName }) =>
          void controller.openAddress({ source: 'code', repo, filePath, qualifiedName, includeTests: store.includeTests })
        }
      />
      <Toolbar
        direction={store.direction}
        includeTests={store.includeTests}
        canBack={store.trailIndex > 0}
        canForward={store.trailIndex < store.trail.length - 1}
        hasGraph={hasNodes}
        tableView={tableView}
        tableOnly={Boolean(quality?.tableOnly)}
        onDirection={(direction: Direction) => void controller.applyFilters({ direction })}
        onIncludeTests={(includeTests) => void controller.applyFilters({ includeTests })}
        onBack={() => void controller.go(-1)}
        onForward={() => void controller.go(1)}
        onFit={() => engine.current?.reveal()}
        onToggleView={() => setTableView((v) => !v)}
      />
      <Legend />
      <Flex gap="3" style={{ flex: 1, minHeight: 0 }} direction={{ initial: 'column', md: 'row' }}>
        <Box ref={canvasBox} style={{ position: 'relative', flex: 1, minHeight: 'max(420px, 55dvh)', minWidth: 0, overflow: tableOnly ? 'auto' : 'hidden' }} aria-busy={loading}>

          {/* Over the picture's top edge, so a message appearing or going never moves the picture under the pointer. */}
          <div style={tableOnly ? { position: 'sticky', top: 0, zIndex: 3 } : { position: 'absolute', top: 0, left: 0, right: 0, zIndex: 3, pointerEvents: notices.length ? 'auto' : 'none' }}>
            <Notices notices={notices} onAction={onAction} />
          </div>
          {tableOnly ? (
            <TableTwin model={model} selection={store.selection} focus={store.node} onSelect={select} onOpen={open} onExpand={expand} onShowMore={showMore} />
          ) : (
            <>
              {/* The picture is for the eyes; this line is for everything else, and points at the way in. */}
              <span style={{ position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)', whiteSpace: 'nowrap' }}>
                {t('graph.ui.canvasHint')}
              </span>
              <div ref={host} style={{ position: 'absolute', inset: 0 }} aria-hidden="true" />
            </>
          )}
          {empty || (!hasNodes && !loading && !store.node && !failure) ? (
            <Flex align="center" justify="center" p="4" style={{ position: 'absolute', inset: 0, pointerEvents: 'none', background: empty ? 'var(--color-background)' : undefined }}>
              <Text color="gray" align="center">{t(empty ?? 'graph.ui.emptyPrompt')}</Text>
            </Flex>
          ) : null}
          {waiting ? (
            <Flex align="center" gap="2" style={{ position: 'absolute', top: 8, left: 8 }} role="status">
              <Spinner size="2" />
              <Text size="1" color="gray">{t('graph.ui.loading')}</Text>
            </Flex>
          ) : null}
          {hovered && hover && !tableOnly ? (
            <Box
              role="tooltip"
              style={{
                position: 'absolute',
                left: hover.x,
                top: hover.y,
                pointerEvents: 'none',
                background: 'var(--color-panel-solid)',
                border: '1px solid var(--gray-6)',
                borderRadius: 'var(--radius-2)',
                padding: '4px 8px',
                maxWidth: 320,
                zIndex: 2,
              }}
            >
              <Text size="2" weight="bold" as="div">{hovered.label ?? hovered.id}</Text>
              <Text size="1" color="gray" as="div">
                {hovered.kind ?? ''} {hovered.file ?? ''}
                {hovered.line ? `:${hovered.line}` : ''}
              </Text>
            </Box>
          ) : null}
        </Box>
        <Box style={{ width: 'min(100%, 320px)' }}>
          <DetailPanel model={model} selection={store.selection} selectionGone={selectionGone && store.selection === null && failure !== 'revoked'} onOpen={open} onExpand={expand} onShowMore={showMore} />
        </Box>
      </Flex>
    </Flex>
  );
}
