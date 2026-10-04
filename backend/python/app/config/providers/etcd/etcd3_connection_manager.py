import asyncio
from dataclasses import dataclass, field
from typing import List, Optional

import etcd3

from app.utils.logger import create_logger

logger = create_logger("etcd")


@dataclass
class ConnectionConfig:
    """Configuration for ETCD connection."""

    hosts: List[str]
    port: int = 2379
    timeout: float = 5.0
    ca_cert: Optional[str] = None
    cert_key: Optional[str] = None
    cert_cert: Optional[str] = None
    username: str | None = None
    password: str | None = field(default=None, repr=False)

    @property
    def auth_enabled(self) -> bool:
        return bool(self.username and self.password)


class ConnectionState:
    """Enum-like class for connection states."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    FAILED = "failed"


class Etcd3ConnectionManager:
    """
    Manages ETCD3 client connections with automatic reconnection and health checks.

    Attributes:
        config: Connection configuration
        client: ETCD3 client instance
        state: Current connection state
        retry_policy: Policy for connection retries
    """

    def __init__(self, config: ConnectionConfig) -> None:
        """
        Initialize the connection manager.

        Args:
            config: Connection configuration
        """
        logger.debug("🔧 Initializing ETCD3 Connection Manager")
        logger.debug("📋 Connection config:")
        logger.debug("   - Hosts: %s", config.hosts)
        logger.debug("   - Port: %s", config.port)
        logger.debug("   - Timeout: %s", config.timeout)
        logger.debug("   - SSL enabled: %s", bool(config.ca_cert or config.cert_key))
        logger.debug("   - Auth enabled: %s", config.auth_enabled)
        if bool(config.username) != bool(config.password):
            logger.warning(
                "Only one of ETCD_USERNAME and ETCD_PASSWORD is set, so etcd will be "
                "reached without logging in. Set both to use etcd authentication, "
                "or neither to connect without it."
            )

        self.config = config
        self.client: Optional[etcd3.client] = None
        self.state = ConnectionState.DISCONNECTED
        logger.debug("📋 Initial state: %s", self.state)

        self._health_check_task: Optional[asyncio.Task] = None
        # connect() skips a second caller while one is connecting, which would
        # hand that caller no client, or the closed one during a reconnect.
        self._connect_lock = asyncio.Lock()
        logger.debug("✅ Connection manager initialized")

    async def connect(self) -> None:
        """Establish connection to ETCD cluster."""
        logger.debug("🔄 Attempting to connect to ETCD")
        logger.debug("📋 Current state: %s", self.state)

        if self.state == ConnectionState.CONNECTING:
            logger.debug("⚠️ Already attempting to connect, skipping")
            return

        self.state = ConnectionState.CONNECTING
        logger.info(
            "🔄 Connecting to ETCD cluster at %s:%s",
            self.config.hosts[0],
            self.config.port,
        )

        try:
            logger.debug("🔄 Creating client in separate thread")
            self.client = await asyncio.to_thread(self._create_client)
            self.state = ConnectionState.CONNECTED
            logger.info("✅ Successfully connected to ETCD cluster")
            logger.debug("📋 Client details: %s", self.client)

        except Exception as e:
            self.state = ConnectionState.FAILED
            logger.error("❌ Failed to connect to ETCD: %s", str(e))
            logger.debug("📋 Connection attempt details:")
            logger.debug("   - Host: %s", self.config.hosts[0])
            logger.debug("   - Port: %s", self.config.port)
            logger.debug("   - Error type: %s", type(e).__name__)
            logger.exception("Detailed error stack:")
            raise ConnectionError(f"Failed to connect to ETCD: {str(e)}")

    def _create_client(self) -> etcd3.client:
        """Create new ETCD client instance."""
        logger.debug("🔄 Creating new ETCD client")
        try:
            logger.debug("📋 Client configuration:")
            logger.debug("   - Host: %s", self.config.hosts[0])
            logger.debug("   - Port: %s", self.config.port)
            logger.debug("   - Timeout: %s", self.config.timeout)

            client_kwargs = {
                "host": self.config.hosts[0],
                "port": self.config.port,
                "timeout": self.config.timeout,
            }

            if any([self.config.ca_cert, self.config.cert_key, self.config.cert_cert]):
                client_kwargs.update(
                    {
                        "ca_cert": self.config.ca_cert,
                        "cert_key": self.config.cert_key,
                        "cert_cert": self.config.cert_cert,
                    }
                )

            if self.config.auth_enabled:
                client_kwargs["user"] = self.config.username
                client_kwargs["password"] = self.config.password

            # Create client synchronously since etcd3 doesn't support async
            client = etcd3.client(**client_kwargs)

            logger.debug("🔍 Testing connection with status check")
            status = client.status()
            logger.debug("📋 ETCD cluster status: %s", status)

            logger.debug("✅ ETCD client created successfully")
            return client

        except Exception as e:
            logger.error("❌ Failed to create ETCD client: %s", str(e))
            logger.debug("📋 Creation attempt details:")
            logger.debug("   - Error type: %s", type(e).__name__)
            logger.debug("   - Error message: %s", str(e))
            logger.exception("Detailed error stack:")
            raise

    async def reconnect(self) -> None:
        """Attempt to reconnect to ETCD cluster."""
        logger.debug("🔄 Initiating reconnection to ETCD")
        logger.debug("📋 Current state: %s", self.state)
        async with self._connect_lock:
            await self._reconnect()

    async def _reconnect(self) -> None:
        self.state = ConnectionState.DISCONNECTED
        if self.client:
            try:
                logger.debug("🔄 Closing existing client")
                self.client.close()
                logger.debug("✅ Existing client closed")
            except Exception as e:
                logger.warning("⚠️ Error closing ETCD client: %s", str(e))
                logger.debug("📋 Close error details:")
                logger.debug("   - Error type: %s", type(e).__name__)
                logger.debug("   - Error message: %s", str(e))

        logger.debug("🔄 Initiating new connection")
        await self.connect()
        logger.debug("📋 New connection state: %s", self.state)

    async def get_client(self) -> etcd3.client:
        """
        Get the current ETCD client, connecting if necessary.

        Returns:
            etcd3.client: Connected ETCD client

        Raises:
            ConnectionError: If no connection is available
        """
        logger.debug("🔍 Getting ETCD client")
        logger.debug("📋 Current state: %s", self.state)

        async with self._connect_lock:
            if self.state != ConnectionState.CONNECTED:
                logger.debug("🔄 Client not connected, initiating connection")
                await self.connect()

        if not self.client:
            logger.error("❌ No ETCD client available after connection attempt")
            raise ConnectionError("No ETCD client available")

        logger.debug("✅ Returning active ETCD client")
        return self.client

    async def close(self) -> None:
        """Clean up resources and close connection."""
        logger.debug("🔄 Closing ETCD connection")
        logger.debug("📋 Current state: %s", self.state)

        if self.client:
            try:
                logger.debug("🔄 Closing client connection")
                self.client.close()
                logger.debug("✅ Client connection closed")
            except Exception as e:
                logger.error("❌ Error during client close: %s", str(e))
                logger.debug("📋 Close error details:")
                logger.debug("   - Error type: %s", type(e).__name__)
                logger.debug("   - Error message: %s", str(e))

        self.client = None
        self.state = ConnectionState.DISCONNECTED
        logger.debug("📋 Final state: %s", self.state)
