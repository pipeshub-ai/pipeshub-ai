import { expect } from 'chai';
import {
  editionLabel,
  setInstallInfo,
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
        'pipeshub_install_info{graph_db="arangodb",vector_db="qdrant",message_broker="kafka",kv_store="etcd",edition="community"} 1',
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
        'pipeshub_install_info{graph_db="neo4j",vector_db="qdrant",message_broker="redis",kv_store="redis",edition="community"} 1',
      );
    });
  });

  describe('edition label', () => {
    it('should default to community', () => {
      expect(editionLabel({})).to.equal('community');
    });

    it('should read the EE image setting', () => {
      expect(editionLabel({ PIPESHUB_EDITION: 'Enterprise' })).to.equal('enterprise');
    });

    it('should collapse unknown values so the label stays bounded', () => {
      expect(editionLabel({ PIPESHUB_EDITION: 'pro-max' })).to.equal('other');
    });
  });
});
