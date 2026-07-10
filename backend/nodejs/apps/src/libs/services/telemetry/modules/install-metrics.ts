import { metricsBackend } from '../metrics-backend';

const installInfo = metricsBackend.createGauge({
  name: 'pipeshub_install_info',
  help: 'Infrastructure backends used by this install (value is always 1)',
  labelNames: ['graph_db', 'vector_db', 'message_broker', 'kv_store'],
});

export function setInstallInfo(labels: {
  graph_db: string;
  vector_db: string;
  message_broker: string;
  kv_store: string;
}): void {
  installInfo.reset();
  installInfo.set(labels, 1);
}
