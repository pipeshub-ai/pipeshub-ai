from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import jwt
from fastapi import HTTPException
from jose import JWTError
from pydantic import BaseModel, ValidationError

from app.config.configuration_service import ConfigurationService
from app.config.constants.service import DefaultEndpoints, config_node_constants


class SignedUrlConfig(BaseModel):
    private_key: str | None = None
    expiration_minutes: int = 60
    algorithm: str = "HS256"
    url_prefix: str = "/api/v1/index"

    @classmethod
    async def create(cls, config_service: ConfigurationService) -> "SignedUrlConfig":
        """Async factory method to create config using configuration service"""
        try:
            # Assuming there's a config node for JWT settings
            secret_keys = await config_service.get_config(
                config_node_constants.SECRET_KEYS.value
            )
            private_key = secret_keys.get("scopedJwtSecret")
            if not private_key:
                raise ValueError(
                    "Private key must be provided through configuration or environment"
                )
            return cls(private_key=private_key)
        except Exception:
            raise

    def __init__(self, **data) -> None:
        super().__init__(**data)
        if not self.private_key:
            raise ValueError(
                "Private key must be provided through configuration or environment"
            )


class TokenPayload(BaseModel):
    record_id: str
    user_id: str
    exp: datetime
    iat: datetime
    additional_claims: Dict[str, Any] = {}

    class Config:
        json_encoders = {
            datetime: lambda v: v.timestamp()  # Convert datetime to timestamp
        }


class SignedUrlHandler:
    def __init__(
        self,
        logger,
        config: SignedUrlConfig,
        config_service: ConfigurationService,
    ) -> None:
        self.logger = logger
        self.signed_url_config = config
        self.config_service = config_service
        self._validate_config()

    def _validate_config(self) -> None:
        """Validate handler configuration"""
        if not self.signed_url_config.private_key:
            raise ValueError("Private key is required")
        if self.signed_url_config.expiration_minutes <= 0:
            raise ValueError("Expiration minutes must be positive")

    async def get_signed_url(
        self,
        record_id: str,
        org_id: str,
        user_id: str,
        additional_claims: Dict[str, Any] = None,
        connector: str = None,
    ) -> str:
        """Create a signed URL with optional additional claims"""
        try:
            # Use UTC consistently for both iat and exp
            now_utc = datetime.now(timezone.utc)
            expiration = now_utc + timedelta(minutes=self.signed_url_config.expiration_minutes)

            endpoints = await self.config_service.get_config(
                config_node_constants.ENDPOINTS.value
            )
            connector_endpoint = endpoints.get("connectors").get("endpoint", DefaultEndpoints.CONNECTOR_ENDPOINT.value)

            self.logger.info(f"user_id: {user_id}")

            payload = TokenPayload(
                record_id=record_id,
                user_id=user_id,
                exp=expiration,
                iat=now_utc,
                additional_claims=additional_claims or {},
            )

            # Convert to dict before encoding
            payload_dict = {
                "record_id": record_id,  # Ensure file_id is at the top level
                "user_id": user_id,
                "exp": payload.exp.timestamp(),  # Convert datetime to timestamp
                "iat": payload.iat.timestamp(),
                "additional_claims": additional_claims or {},
            }

            token = jwt.encode(
                payload_dict,
                self.signed_url_config.private_key,
                algorithm=self.signed_url_config.algorithm,
            )

            self.logger.info(
                "Created signed URL for record %s with connector %s",
                record_id,
                connector,
            )

            return f"{connector_endpoint}{self.signed_url_config.url_prefix}/{org_id}/{connector}/record/{record_id}?token={token}"

        except ValidationError as e:
            self.logger.error("Payload validation error: %s", str(e))
            raise HTTPException(status_code=400, detail="Invalid payload data")
        except Exception as e:
            self.logger.error("Error creating signed URL: %s", str(e))
            raise HTTPException(status_code=500, detail="Error creating signed URL")

    def validate_token(
        self, token: str, required_claims: Dict[str, Any] = None
    ) -> TokenPayload:
        """Validate the JWT token and optional required claims"""
        try:
            self.logger.debug(f"Validating token: {token}")
            payload = jwt.decode(
                token,
                self.signed_url_config.private_key,
                algorithms=[self.signed_url_config.algorithm],
            )
            self.logger.debug(f"Payload: {payload}")

            # Convert timestamps back to datetime for validation (ensure UTC timezone)
            if "exp" in payload:
                payload["exp"] = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
            if "iat" in payload:
                payload["iat"] = datetime.fromtimestamp(payload["iat"], tz=timezone.utc)

            token_data = TokenPayload(**payload)
            self.logger.debug(f"Token data: {token_data}")

            if required_claims:
                for key, value in required_claims.items():
                    if token_data.additional_claims.get(key) != value:
                        raise HTTPException(
                            status_code=401, detail=f"Required claim '{key}' is invalid"
                        )

            return token_data

        except JWTError as e:
            self.logger.error("JWT validation error: %s", str(e))
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        except ValidationError as e:
            self.logger.error("Payload validation error: %s", str(e))
            raise HTTPException(status_code=400, detail="Invalid token payload")
        except Exception as e:
            self.logger.error("Unexpected error during token validation: %s", str(e))
            raise HTTPException(status_code=500, detail="Error validating token")
