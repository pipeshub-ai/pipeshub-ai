"""
Sandbox execution layer for running untrusted code in isolated environments.

Supports two execution modes:
- LocalExecutor: subprocess-based, for developer mode
- DockerExecutor: container-based, for production/docker deployments
"""
