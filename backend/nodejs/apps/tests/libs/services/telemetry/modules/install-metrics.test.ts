import { expect } from 'chai';
import {
  setInstallInfo,
  setInfraServiceUp,
} from '../../../../../src/libs/services/telemetry/modules/install-metrics';
import { metricsBackend } from '../../../../../src/libs/services/telemetry/metrics-backend';

describe('telemetry modules/install-metrics', () => {
  describe('setInstallInfo', () => {
    it('should publish a single info series with value 1', async () => {
      setInstallInfo({
        graph_db: 'arangodb',
        vector_db: 'qdrant',
        message_broker: 'kafka',
        kv_store: 'etcd',
      });

      const text = await metricsBackend.serialize();
      expect(text).to.include(
        'pipeshub_install_info{graph_db="arangodb",vector_db="qdrant",message_broker="kafka",kv_store="etcd"} 1',
      );
    });

    it('should drop the previous info series when backends change', async () => {
      setInstallInfo({
        graph_db: 'arangodb',
        vector_db: 'qdrant',
        message_broker: 'kafka',
        kv_store: 'etcd',
      });
      setInstallInfo({
        graph_db: 'neo4j',
        vector_db: 'qdrant',
        message_broker: 'redis',
        kv_store: 'redis',
      });

      const text = await metricsBackend.serialize();
      expect(text).to.not.include('graph_db="arangodb"');
      expect(text).to.include(
        'pipeshub_install_info{graph_db="neo4j",vector_db="qdrant",message_broker="redis",kv_store="redis"} 1',
      );
    });
  });

  describe('setInfraServiceUp', () => {
    it('should publish 1 for healthy and 0 for unhealthy services', async () => {
      setInfraServiceUp([
        { service: 'mongodb', healthy: true },
        { service: 'kafka', healthy: false },
      ]);

      const text = await metricsBackend.serialize();
      expect(text).to.include('pipeshub_infra_service_up{service="mongodb"} 1');
      expect(text).to.include('pipeshub_infra_service_up{service="kafka"} 0');
    });

    it('should drop services that disappear between refreshes', async () => {
      setInfraServiceUp([{ service: 'redis', healthy: true }]);
      setInfraServiceUp([{ service: 'arangodb', healthy: true }]);

      const text = await metricsBackend.serialize();
      expect(text).to.not.include('service="redis"');
      expect(text).to.include('pipeshub_infra_service_up{service="arangodb"} 1');
    });
  });
});
