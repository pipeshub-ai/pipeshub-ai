import { metricsBackend } from '../../src/libs/services/telemetry/metrics-backend';

/** The registry's samples of `name` (a counter's or gauge's own series), each with its labels. */
export async function samples(
  name: string,
): Promise<Array<{ labels: Record<string, string>; value: number }>> {
  const text = await metricsBackend.serialize();
  const out: Array<{ labels: Record<string, string>; value: number }> = [];
  for (const line of text.split('\n')) {
    const match = new RegExp(`^${name}(?:\\{(.*)\\})? (\\S+)$`).exec(line);
    if (match === null) continue;
    const labels: Record<string, string> = {};
    for (const [, key, value] of (match[1] ?? '').matchAll(/(\w+)="((?:[^"\\]|\\.)*)"/g)) {
      labels[key as string] = value as string;
    }
    out.push({ labels, value: Number(match[2]) });
  }
  return out;
}

/** The value of the one series of `name` with exactly `labels` (0 when it has none yet). */
export async function sampleValue(
  name: string,
  labels: Record<string, string>,
): Promise<number> {
  const wanted = Object.entries(labels);
  const found = (await samples(name)).find(
    (sample) =>
      Object.keys(sample.labels).length === wanted.length &&
      wanted.every(([key, value]) => sample.labels[key] === value),
  );
  return found?.value ?? 0;
}
