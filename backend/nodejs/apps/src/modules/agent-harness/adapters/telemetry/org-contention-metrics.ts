import { metricsBackend } from '../../../../libs/services/telemetry/metrics-backend';
import { AuditLinkMetrics } from '../../application/audit-link-watch';
import { AdmissionQuotaMetrics } from '../../../../libs/repositories/agent-harness/admission-quota-ledger/mongo';

const unlinkedAppends = metricsBackend.createGauge({
  name: 'pipeshub_agent_harness_audit_unlinked_appends',
  help: 'Audit appends committed and not yet linked onto their org chain, as this Node last saw them',
  labelNames: [],
});

const unlinkedOldest = metricsBackend.createGauge({
  name: 'pipeshub_agent_harness_audit_unlinked_oldest_seconds',
  help: 'Age of the oldest audit append not yet linked; above 60 the link is not running and anchors are held back',
  labelNames: [],
});

const placesReturned = metricsBackend.createCounter({
  name: 'pipeshub_agent_harness_admission_places_returned_total',
  help: "Places in an org's admission window given back: the admission was refused or failed (not_admitted) or was a replay (replayed)",
  labelNames: ['why'],
});

export const prometheusAuditLinkMetrics: AuditLinkMetrics = {
  unlinked: (appends, oldestAgeS) => {
    unlinkedAppends.set({}, appends);
    unlinkedOldest.set({}, oldestAgeS);
  },
};

export const prometheusAdmissionQuotaMetrics: AdmissionQuotaMetrics = {
  returned: (why) => placesReturned.inc({ why }),
};
