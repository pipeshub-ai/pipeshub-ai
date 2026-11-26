/** How many records one erasure statement or command names at most, on either store. */
export const ERASE_BATCH = 1_000;

export const chunks = <T>(items: readonly T[], size: number): T[][] =>
  Array.from({ length: Math.ceil(items.length / size) }, (_, index) =>
    items.slice(index * size, (index + 1) * size),
  );
