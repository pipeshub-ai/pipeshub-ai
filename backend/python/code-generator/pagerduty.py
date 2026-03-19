# ruff: noqa
"""
PagerDuty Data Source - SDK-based (no code generation needed)

The PagerDuty DataSource wraps the official `pagerduty` Python SDK
(PyPI: pagerduty) which provides RestApiV2Client.

Unlike HTTP-based data sources (Figma, Coda, Gong), PagerDuty uses
the SDK's built-in methods (rget, rpost, rput, rdelete, list_all)
directly. The DataSource is hand-written, not generated.

SDK: https://github.com/PagerDuty/python-pagerduty
API: https://developer.pagerduty.com/api-reference/

The data source file is at:
    app/sources/external/pagerduty/pagerduty.py
"""

print("PagerDuty DataSource uses the SDK directly. No code generation needed.")
print("See: app/sources/external/pagerduty/pagerduty.py")
