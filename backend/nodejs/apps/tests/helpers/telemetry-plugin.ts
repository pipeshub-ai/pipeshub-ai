import { loadPlugins, resetPlugins } from '../../src/libs/plugins';
import telemetryPlugin from '../../src/plugins/telemetry';

/**
 * Call inside a `describe`: loads the telemetry plug-in for its tests, so what
 * the code under test announces reaches the event buffer and the metrics as it
 * does in a default build.
 */
export function useTelemetryPlugin(): void {
  before(async () => {
    resetPlugins();
    await loadPlugins([telemetryPlugin]);
  });
  after(() => {
    resetPlugins();
  });
}
