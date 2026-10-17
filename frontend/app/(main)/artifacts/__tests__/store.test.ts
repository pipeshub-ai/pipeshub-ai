import { beforeEach, describe, expect, it } from 'vitest';
import { useArtifactsStore } from '../store';
import type { ArtifactListItem } from '../types';

function item(artifactId: string): ArtifactListItem {
  return { artifactId, name: `${artifactId}.csv`, artifactType: 'DATA_FILE', version: 1 };
}

beforeEach(() => {
  useArtifactsStore.setState({ items: [], page: 1, limit: 2, totalCount: 0, totalPages: 0 });
});

describe('removeItem', () => {
  it('takes a deleted artifact out of the All and Mine tab counts', () => {
    useArtifactsStore.setState({
      items: [{ ...item('a'), role: 'OWNER' }, item('b')],
      totalCount: 2,
      counts: { all: 5, mine: 3, shared: 2 },
    });

    useArtifactsStore.getState().removeItem('a');

    expect(useArtifactsStore.getState().counts).toEqual({ all: 4, mine: 2, shared: 2 });
  });

  it('drops the item and recounts pages', () => {
    useArtifactsStore.setState({ items: [item('a'), item('b')], page: 1, limit: 2, totalCount: 3, totalPages: 2 });

    useArtifactsStore.getState().removeItem('a');

    const s = useArtifactsStore.getState();
    expect(s.items.map((i) => i.artifactId)).toEqual(['b']);
    expect(s.totalCount).toBe(2);
    expect(s.totalPages).toBe(1);
    expect(s.page).toBe(1);
  });

  it('steps back a page when it empties the last page', () => {
    useArtifactsStore.setState({ items: [item('c')], page: 2, limit: 2, totalCount: 3, totalPages: 2 });

    useArtifactsStore.getState().removeItem('c');

    const s = useArtifactsStore.getState();
    expect(s.items).toEqual([]);
    expect(s.totalCount).toBe(2);
    expect(s.totalPages).toBe(1);
    expect(s.page).toBe(1);
  });

  it('stays on page 1 when the only item goes', () => {
    useArtifactsStore.setState({ items: [item('a')], page: 1, limit: 2, totalCount: 1, totalPages: 1 });

    useArtifactsStore.getState().removeItem('a');

    const s = useArtifactsStore.getState();
    expect(s.items).toEqual([]);
    expect(s.totalCount).toBe(0);
    expect(s.totalPages).toBe(0);
    expect(s.page).toBe(1);
  });

  it('ignores an id that is not on the page', () => {
    useArtifactsStore.setState({ items: [item('a')], page: 1, limit: 2, totalCount: 1, totalPages: 1 });

    useArtifactsStore.getState().removeItem('zzz');

    expect(useArtifactsStore.getState().totalCount).toBe(1);
  });
});
