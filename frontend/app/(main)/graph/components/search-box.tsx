'use client';

import { useEffect, useId, useRef, useState } from 'react';
import { Badge, Flex, Select, Text, TextField } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { GraphApi } from '../api';
import type { CodeRepoInfo, SearchMatch } from '../types';

export interface SearchPick {
  repo: string;
  filePath: string;
  qualifiedName: string;
}

export interface SearchBoxApi {
  repos: () => Promise<CodeRepoInfo[]>;
  search: (body: { repo: string; pattern: string; include_tests?: boolean }) => Promise<SearchMatch[]>;
}

export interface SearchBoxProps {
  includeTests: boolean;
  /** A repository to start in (from a link). */
  initialRepo?: string | null;
  onPick: (pick: SearchPick) => void;
  api?: SearchBoxApi;
  /** Milliseconds to wait after typing before asking. */
  debounceMs?: number;
  /** Focus the input when this changes (the `/` shortcut). */
  focusSignal?: number;
}

/** Hits asked for; a full list means there may be more, so the box says to narrow the search. */
export const SEARCH_PAGE = 20;

type State = 'idle' | 'loading' | 'done' | 'failed';

/**
 * Search first: a name in one repository, then a click opens its graph. The repository list is the
 * one the caller can read, so no name is offered that they could not. A failed search says so and
 * never reads as "no matches".
 */
export function SearchBox({ includeTests, initialRepo, onPick, api = GraphApi, debounceMs = 250, focusSignal }: SearchBoxProps) {
  const { t } = useTranslation();
  const listId = useId();
  const input = useRef<HTMLInputElement>(null);
  const repoTrigger = useRef<HTMLButtonElement>(null);
  const [repos, setRepos] = useState<CodeRepoInfo[] | null>(null);
  const [reposFailed, setReposFailed] = useState(false);
  const [repo, setRepo] = useState<string>(initialRepo ?? '');
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<SearchMatch[]>([]);
  const [state, setState] = useState<State>('idle');
  const [active, setActive] = useState(-1);
  const [dismissed, setDismissed] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    api.repos().then(
      (list) => {
        if (!alive) return;
        setRepos(list);
        // A repository the link named that is not in the readable list is ignored, not shown as a blank choice.
        setRepo((current) => (list.some((r) => r.repo === current) ? current : list.length === 1 ? list[0].repo : ''));
      },
      () => alive && setReposFailed(true),
    );
    return () => {
      alive = false;
    };
  }, [api]);

  useEffect(() => {
    // `/` goes to the search; with no repository chosen yet it goes to the repository choice instead.
    if (focusSignal) (input.current && !input.current.disabled ? input.current : repoTrigger.current)?.focus();
  }, [focusSignal]);

  // Each keystroke restarts the wait; an answer for an older query is dropped.
  useEffect(() => {
    const pattern = query.trim();
    if (!repo || pattern.length < 2) {
      setHits([]);
      setActive(-1);
      setState('idle');
      return;
    }
    let alive = true;
    setState('loading');
    const timer = setTimeout(() => {
      api.search({ repo, pattern, include_tests: includeTests }).then(
        (matches) => {
          if (!alive) return;
          setHits(matches);
          setDismissed(false);
          setActive(0);
          setState('done');
        },
        () => {
          if (!alive) return;
          setHits([]);
          setState('failed');
        },
      );
    }, debounceMs);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [api, repo, query, includeTests, debounceMs]);

  // A click anywhere else closes the list (the typed text stays).
  useEffect(() => {
    const onDown = (event: PointerEvent) => {
      if (root.current && !root.current.contains(event.target as Node)) setDismissed(true);
    };
    document.addEventListener('pointerdown', onDown);
    return () => document.removeEventListener('pointerdown', onDown);
  }, []);

  const pick = (hit: SearchMatch) => {
    onPick({ repo: hit.repo ?? repo, filePath: hit.file_path, qualifiedName: hit.qualified_name });
    setQuery('');
    setHits([]);
    setActive(-1);
    setState('idle');
  };

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === 'ArrowDown' && hits.length) {
      event.preventDefault();
      setActive((i) => Math.min(hits.length - 1, i + 1));
    } else if (event.key === 'ArrowUp' && hits.length) {
      event.preventDefault();
      setActive((i) => Math.max(0, i - 1));
    } else if (event.key === 'Enter' && hits.length) {
      pick(hits[Math.max(0, active)]);
    } else if (event.key === 'Escape') {
      setQuery('');
      setActive(-1);
    }
  };

  return (
    <Flex ref={root} direction="column" gap="2" style={{ position: 'relative' }}>
      <Flex gap="2" wrap="wrap" align="center">
        <Select.Root value={repo} onValueChange={setRepo} disabled={!repos?.length}>
          <Select.Trigger ref={repoTrigger} aria-label={t('graph.ui.search.repo')} placeholder={t('graph.ui.search.repoPlaceholder')} />
          <Select.Content>
            {(repos ?? []).map((r) => (
              <Select.Item key={`${r.connector_id}:${r.repo}`} value={r.repo}>
                {r.repo}
              </Select.Item>
            ))}
          </Select.Content>
        </Select.Root>
        <TextField.Root
          ref={input}
          style={{ flex: 1, minWidth: 220 }}
          value={query}
          placeholder={repo ? t('graph.ui.search.placeholder') : t('graph.ui.search.chooseFirst')}
          aria-label={t('graph.ui.search.label')}
          role="combobox"
          aria-autocomplete="list"
          aria-haspopup="listbox"
          aria-expanded={hits.length > 0}
          aria-controls={listId}
          aria-activedescendant={active >= 0 ? `${listId}-${active}` : undefined}
          disabled={!repo}
          onChange={(e) => {
            setDismissed(false);
            setQuery(e.target.value);
          }}
          onFocus={() => setDismissed(false)}
          onKeyDown={onKeyDown}
        />
      </Flex>
      {reposFailed ? <Text size="1" color="red">{t('graph.error.unavailable')}</Text> : null}
      {repos && repos.length === 0 ? <Text size="1" color="gray">{t('graph.ui.search.noRepos')}</Text> : null}
      {/* Hints and results messages float under the box, so they never push the toolbar and the graph around. */}
      <div role="status" aria-live="polite" style={{ position: 'absolute', top: '100%', left: 0, zIndex: 19, background: 'var(--color-background)', borderRadius: 'var(--radius-2)' }}>
        {state === 'idle' && repo && query.trim().length === 1 ? <Text size="1" color="gray">{t('graph.ui.search.minChars')}</Text> : null}
        {state === 'failed' ? <Text size="1" color="red">{t('graph.error.unavailable')}</Text> : null}
        {state === 'done' && hits.length === 0 ? <Text size="1" color="gray">{t('graph.ui.search.noMatches')}</Text> : null}
        {state === 'done' && hits.length >= SEARCH_PAGE && !dismissed ? <Text size="1" color="gray">{t('graph.ui.search.more')}</Text> : null}
      </div>
      {/* A popover over the page, so typing never pushes the graph down. */}
      {hits.length > 0 && !dismissed ? (
      <ul
        id={listId}
        role="listbox"
        aria-label={t('graph.ui.search.results')}
        style={{
          listStyle: 'none',
          margin: 0,
          padding: 4,
          position: 'absolute',
          top: '100%',
          left: 0,
          right: 0,
          zIndex: 20,
          maxHeight: 320,
          overflowY: 'auto',
          background: 'var(--color-panel-solid)',
          border: '1px solid var(--gray-6)',
          borderRadius: 'var(--radius-2)',
          boxShadow: 'var(--shadow-4)',
        }}
      >
        {hits.map((hit, i) => (
          <li
            key={`${hit.file_path}#${hit.qualified_name}`}
            id={`${listId}-${i}`}
            role="option"
            aria-selected={i === active}
            onClick={() => pick(hit)}
            style={{ cursor: 'pointer', padding: '6px 8px', borderRadius: 'var(--radius-2)', background: i === active ? 'var(--accent-3)' : undefined }}
          >
            <Flex gap="2" align="center">
              {hit.kind ? <Badge color="gray">{hit.kind}</Badge> : null}
              <Text size="2" weight="medium">{hit.qualified_name.replace(/^[a-z]+:/, '')}</Text>
              <Text size="1" color="gray">{hit.file_path}</Text>
            </Flex>
          </li>
        ))}
      </ul>
      ) : null}
    </Flex>
  );
}
