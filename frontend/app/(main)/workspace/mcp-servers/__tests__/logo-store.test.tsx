import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render } from '@testing-library/react';

const getLogos = vi.fn();
vi.mock('../api', () => ({ McpServersApi: { getLogos: (...args: unknown[]) => getLogos(...args) } }));

import {
  ensureMcpLogosLoaded,
  reloadMcpLogos,
  resetMcpLogos,
  setMcpServerLogo,
  useMcpServerLogo,
} from '../logo-store';
import { clearSessionCache } from '@/lib/api/session-cache';

const LOGO = 'data:image/png;base64,AAAA';
const OLD = 'data:image/png;base64,b2xk';

function Probe({ id }: { id: string | null }) {
  return <span data-testid="logo">{useMcpServerLogo(id) ?? 'none'}</span>;
}

beforeEach(() => {
  resetMcpLogos();
  getLogos.mockReset();
});
afterEach(cleanup);

describe('MCP server logos', () => {
  it('reads every logo with one request, however many icons ask', async () => {
    getLogos.mockResolvedValue({ logos: { a: LOGO } });

    const { getAllByTestId } = render(
      <>
        <Probe id="a" />
        <Probe id="a" />
        <Probe id="b" />
      </>
    );
    await act(() => ensureMcpLogosLoaded());

    expect(getLogos).toHaveBeenCalledTimes(1);
    expect(getAllByTestId('logo').map((el) => el.textContent)).toEqual([LOGO, LOGO, 'none']);
  });

  it('asks for nothing when no icon names a server', async () => {
    render(<Probe id={null} />);
    await act(async () => {});

    expect(getLogos).not.toHaveBeenCalled();
  });

  it('shows a logo saved or removed on this page at once', async () => {
    getLogos.mockResolvedValue({ logos: {} });
    const { getByTestId } = render(<Probe id="a" />);
    await act(() => ensureMcpLogosLoaded());

    act(() => setMcpServerLogo('a', LOGO));
    expect(getByTestId('logo').textContent).toBe(LOGO);

    act(() => setMcpServerLogo('a', null));
    expect(getByTestId('logo').textContent).toBe('none');
  });

  it('keeps a logo saved or removed here while the read is still on its way', async () => {
    let answer: (value: { logos: Record<string, string> }) => void = () => {};
    getLogos.mockReturnValue(new Promise((resolve) => (answer = resolve)));
    const { getAllByTestId } = render(
      <>
        <Probe id="a" />
        <Probe id="b" />
      </>
    );
    const read = ensureMcpLogosLoaded();

    act(() => {
      setMcpServerLogo('a', LOGO);
      setMcpServerLogo('b', null);
    });
    await act(async () => {
      answer({ logos: { a: OLD, b: OLD } });
      await read;
    });

    expect(getAllByTestId('logo').map((el) => el.textContent)).toEqual([LOGO, 'none']);
  });

  it('shows no logos when the read fails, and waits before trying again', async () => {
    getLogos.mockRejectedValue(new Error('offline'));
    const { getByTestId } = render(<Probe id="a" />);
    await act(() => ensureMcpLogosLoaded());

    expect(getByTestId('logo').textContent).toBe('none');
    await ensureMcpLogosLoaded();
    expect(getLogos).toHaveBeenCalledTimes(1);
  });

  it('treats an API call that throws at once as a failed read', async () => {
    getLogos.mockImplementation(() => {
      throw new TypeError('getLogos is not a function');
    });

    await expect(ensureMcpLogosLoaded()).resolves.toBeUndefined();
  });

  it('reads the logos again on reload', async () => {
    getLogos.mockResolvedValueOnce({ logos: { a: LOGO } }).mockResolvedValueOnce({ logos: {} });
    const { getByTestId } = render(<Probe id="a" />);
    await act(() => ensureMcpLogosLoaded());
    expect(getByTestId('logo').textContent).toBe(LOGO);

    await act(() => reloadMcpLogos());

    expect(getLogos).toHaveBeenCalledTimes(2);
    expect(getByTestId('logo').textContent).toBe('none');
  });

  describe('when the session ends', () => {
    function pendingRead() {
      const read: { answer: (value: { logos: Record<string, string> }) => void; fail: (error: Error) => void } = {
        answer: () => {},
        fail: () => {},
      };
      getLogos.mockReturnValueOnce(
        new Promise((resolve, reject) => {
          read.answer = resolve;
          read.fail = reject;
        })
      );
      return read;
    }

    it('forgets every logo on sign-out and reads them again for the next user', async () => {
      getLogos.mockResolvedValueOnce({ logos: { a: LOGO } }).mockResolvedValueOnce({ logos: {} });
      const { getByTestId } = render(<Probe id="a" />);
      await act(() => ensureMcpLogosLoaded());
      expect(getByTestId('logo').textContent).toBe(LOGO);

      act(() => clearSessionCache());
      expect(getByTestId('logo').textContent).toBe('none');

      await act(() => ensureMcpLogosLoaded());
      expect(getLogos).toHaveBeenCalledTimes(2);
      expect(getByTestId('logo').textContent).toBe('none');
    });

    it('drops the answer of a read started before the reset', async () => {
      const old = pendingRead();
      const { getByTestId } = render(<Probe id="a" />);
      const oldLoad = ensureMcpLogosLoaded();

      act(() => clearSessionCache());
      await act(async () => {
        old.answer({ logos: { a: OLD } });
        await oldLoad;
      });

      expect(getByTestId('logo').textContent).toBe('none');
    });

    it("leaves the next session's read in flight when an old read finishes", async () => {
      const old = pendingRead();
      const oldLoad = ensureMcpLogosLoaded();
      clearSessionCache();
      const next = pendingRead();
      const nextLoad = ensureMcpLogosLoaded();

      old.answer({ logos: { a: OLD } });
      await oldLoad;

      expect(ensureMcpLogosLoaded()).toBe(nextLoad);
      next.answer({ logos: {} });
      await nextLoad;
      expect(getLogos).toHaveBeenCalledTimes(2);
    });

    it("does not hold back the next session's read when an old read fails", async () => {
      const old = pendingRead();
      const oldLoad = ensureMcpLogosLoaded();
      clearSessionCache();

      old.fail(new Error('offline'));
      await oldLoad;
      getLogos.mockResolvedValueOnce({ logos: { a: LOGO } });
      await ensureMcpLogosLoaded();

      expect(getLogos).toHaveBeenCalledTimes(2);
    });
  });
});
