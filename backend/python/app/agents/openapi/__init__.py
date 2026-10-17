"""API Toolsets: agent tools compiled from an org's OpenAPI spec and called over plain HTTP.

Separate from MCP on purpose: these servers are ordinary REST APIs. The spec is compiled
once, when the admin uploads it (`spec_compiler`); each selected operation is then one
tool whose call is built by `request_builder`, authenticated by `auth` and sent by
`executor`.
"""
