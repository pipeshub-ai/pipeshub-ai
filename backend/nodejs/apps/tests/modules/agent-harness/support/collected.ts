/** Items as they arrive; `first(n)` resolves once n have, so a test waits on delivery, never on a clock. */
export function collected<T>(): { push(item: T): void; first(count: number): Promise<T[]> } {
  const items: T[] = [];
  const waiters: Array<{ count: number; resolve: (items: T[]) => void }> = [];
  return {
    push(item: T) {
      items.push(item);
      for (const waiter of [...waiters]) {
        if (items.length >= waiter.count) {
          waiters.splice(waiters.indexOf(waiter), 1);
          waiter.resolve(items.slice(0, waiter.count));
        }
      }
    },
    first: (count) =>
      items.length >= count
        ? Promise.resolve(items.slice(0, count))
        : new Promise<T[]>((resolve) => waiters.push({ count, resolve })),
  };
}
