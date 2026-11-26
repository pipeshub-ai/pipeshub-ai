/**
 * The region a process runs in (OWD-12). Every run and stored harness object
 * carries it, and a worker claims only runs of its own region. Which region a
 * process is in is an extension point (`HarnessEdition.region`); this build
 * has one region, `default`.
 */

/** The region of a process no extension point places: one region for the whole deployment. */
export const UNSET_REGION = 'default';
const REGION = /^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/;

export const isRegion = (value: unknown): value is string =>
  typeof value === 'string' && REGION.test(value);

let regionSource: () => string = () => UNSET_REGION;

/**
 * Sets where `deploymentRegion` reads this process's region. Called once at
 * boot, before any store writes: the stores read the region once per process.
 */
export function useRegionSource(read: () => string): void {
  regionSource = read;
}

/** Restores the single region (tests). */
export function resetRegionSource(): void {
  regionSource = () => UNSET_REGION;
}

/** This process's region. */
export const deploymentRegion = (): string => regionSource();

/** Whether a worker in `workerRegion` may run a run of `runRegion` (a run from before regions is the control plane's). */
export const workerMayRun = (
  runRegion: string | undefined,
  workerRegion: string,
  controlPlaneRegion: string,
): boolean => (runRegion ?? controlPlaneRegion) === workerRegion;
