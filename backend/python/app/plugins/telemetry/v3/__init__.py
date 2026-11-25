"""Usage telemetry v3: the authenticated, catalogued contract of the usage collector.

The legacy contract pushes a text dump of this process's whole metric registry
and every buffered event, without credentials. v3 sends only what the usage
catalogue names, as counter deltas and current gauge values, signed with the
install's credential.

Setting
-------
``USAGE_TELEMETRY_CONTRACT`` picks the contract (read once, at pusher start):

* ``auto`` (default, and what any unrecognised value means): v3 when the install
  holds a credential and the collector answers as a v3 collector; the legacy
  endpoints otherwise.
* ``v3``: v3 only. Nothing is ever sent to the legacy endpoints; until v3 is
  possible, counts and events wait in the process.
* ``legacy``: the legacy endpoints only, exactly as before v3 existed.

``USAGE_TELEMETRY_EDITION`` is the edition a v3 push reports: ``ee`` when it is
exactly that (any case), ``oss`` otherwise.

Neither setting widens what is sent: ``TELEMETRY_COLLECTION_FLAG`` and the install's
``enableMetricCollection`` consent decide, as before, whether anything is sent.

Credential
----------
The Node service registers the install and stores the secret as ``installSecret``
in the ``/services/metricsCollection`` config, beside ``installId``. Python only
reads it, on every tick, and never registers or writes that config. The secret
is never logged.

What the legacy contract carries that v3 does not (Python services)
------------------------------------------------------------------
Metrics: every series other than the three below, that is everything the
modules under ``telemetry/modules`` record apart from ``activity_metrics`` and
``connector_metrics`` (HTTP, database, record storage, vector search,
scheduling, indexing, knowledge graph, LLM gateway, schema, permissions, web
tools, ...), every histogram, and ``pipeshub_metric_collection_enabled``. Of
the three that are sent (``pipeshub_connector_active``,
``pipeshub_usage_records``, ``pipeshub_activity_total``) the labels ``domain``,
``org``, ``kb``, ``service`` and ``mimetype`` are dropped and the values summed
over them.

Events: ``retrieve_performed`` and ``answer_generated`` have no v3 counterpart
and are not sent. From the events that are sent, ``orgId``, ``userId``,
``email`` and ``domain`` are dropped, and so is ``streaming`` on
``chat_session_started``.

Envelope: ``instanceId`` (the collector takes the install from the credential),
``apiKey``, ``metricsVersion``, ``schemaVersion`` and the push ``timestamp``.
"""

import os

CONTRACT_ENV = "USAGE_TELEMETRY_CONTRACT"
EDITION_ENV = "USAGE_TELEMETRY_EDITION"
INSTALL_SECRET_FIELD = "installSecret"


def edition() -> str:
    """The edition a push reports: ``ee`` when ``USAGE_TELEMETRY_EDITION`` says so, else ``oss``."""
    return "ee" if os.getenv(EDITION_ENV, "").strip().lower() == "ee" else "oss"
