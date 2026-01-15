"""
JWT Configuration Service for Python

This module provides centralized JWT configuration management,
similar to the Node.js getJwtConfig() and getJwtKeyFromConfig() utilities.
"""

import os
from dataclasses import dataclass
from typing import Optional, Dict
from app.config.configuration_service import ConfigurationService
from app.config.constants.service import config_node_constants


@dataclass
class JwtConfig:
    """JWT Configuration"""
    algorithm: str  # 'HS256' or 'RS256'
    use_rsa: bool
    
    # For HS256
    jwt_secret: Optional[str] = None
    scoped_jwt_secret: Optional[str] = None
    
    # For RS256
    jwt_private_key: Optional[str] = None
    jwt_public_key: Optional[str] = None
    scoped_jwt_private_key: Optional[str] = None
    scoped_jwt_public_key: Optional[str] = None


# Global cache for JWT configuration
_JWT_CONFIG_CACHE: Optional[JwtConfig] = None


def get_jwt_algorithm() -> str:
    """
    Get the JWT algorithm from environment configuration.
    
    Returns:
        str: 'RS256' or 'HS256'
    """
    return "RS256" if os.getenv("JWT_ENCRYPTION_KEY") == "RS256" else "HS256"


async def load_jwt_config(config_service: ConfigurationService) -> JwtConfig:
    """
    Load complete JWT configuration from etcd.
    Should be called at application startup.
    
    Args:
        config_service: Configuration service instance
        
    Returns:
        JwtConfig: Complete JWT configuration with keys
    """
    algorithm = get_jwt_algorithm()
    use_rsa = algorithm == "RS256"
    
    print(f"📋 Loading JWT configuration with algorithm: {algorithm}")
    
    # Get secret keys from etcd
    secret_keys = await config_service.get_config(
        config_node_constants.SECRET_KEYS.value
    )
    
    if not secret_keys:
        raise ValueError("SECRET_KEYS configuration not found in etcd")
    
    if use_rsa:
        # Load RSA keys
        jwt_private_key = secret_keys.get("jwtPrivateKey")
        jwt_public_key = secret_keys.get("jwtPublicKey")
        scoped_jwt_private_key = secret_keys.get("scopedJwtPrivateKey")
        scoped_jwt_public_key = secret_keys.get("scopedJwtPublicKey")
        
        if not all([jwt_private_key, jwt_public_key, scoped_jwt_private_key, scoped_jwt_public_key]):
            missing = []
            if not jwt_private_key: missing.append("jwtPrivateKey")
            if not jwt_public_key: missing.append("jwtPublicKey")
            if not scoped_jwt_private_key: missing.append("scopedJwtPrivateKey")
            if not scoped_jwt_public_key: missing.append("scopedJwtPublicKey")
            raise ValueError(f"Missing RSA keys in etcd: {', '.join(missing)}")
        
        config = JwtConfig(
            algorithm=algorithm,
            use_rsa=True,
            jwt_private_key=jwt_private_key,
            jwt_public_key=jwt_public_key,
            scoped_jwt_private_key=scoped_jwt_private_key,
            scoped_jwt_public_key=scoped_jwt_public_key
        )
        print(f"✓ Loaded RSA keys (private + public) for JWT generation and verification")
    else:
        # Load HMAC secrets
        jwt_secret = secret_keys.get("jwtSecret")
        scoped_jwt_secret = secret_keys.get("scopedJwtSecret")
        
        if not jwt_secret or not scoped_jwt_secret:
            missing = []
            if not jwt_secret: missing.append("jwtSecret")
            if not scoped_jwt_secret: missing.append("scopedJwtSecret")
            raise ValueError(f"Missing JWT secrets in etcd: {', '.join(missing)}")
        
        config = JwtConfig(
            algorithm=algorithm,
            use_rsa=False,
            jwt_secret=jwt_secret,
            scoped_jwt_secret=scoped_jwt_secret
        )
        print(f"✓ Loaded HMAC secrets for JWT generation and verification")
    
    return config


async def initialize_jwt_config(config_service: ConfigurationService) -> JwtConfig:
    """
    Initialize JWT configuration and cache it.
    Should be called at application startup.
    
    Args:
        config_service: Configuration service instance
        
    Returns:
        JwtConfig: Complete JWT configuration
    """
    global _JWT_CONFIG_CACHE
    
    if _JWT_CONFIG_CACHE is not None:
        print("✓ JWT configuration already cached")
        return _JWT_CONFIG_CACHE
    
    config = await load_jwt_config(config_service)
    _JWT_CONFIG_CACHE = config
    
    print(f"✅ JWT configuration initialized and cached")
    return config


def get_jwt_config() -> Optional[JwtConfig]:
    """
    Get cached JWT configuration.
    Returns None if not initialized.
    
    Returns:
        JwtConfig: Cached JWT configuration or None
    """
    return _JWT_CONFIG_CACHE


def get_signing_key(use_scoped: bool = True) -> str:
    """
    Get the appropriate key for signing JWTs.
    
    Args:
        use_scoped: Whether to use scoped JWT key
        
    Returns:
        str: The signing key (private key for RS256, secret for HS256)
        
    Raises:
        ValueError: If configuration is not initialized or key is missing
    """
    config = get_jwt_config()
    
    if not config:
        raise ValueError("JWT configuration not initialized. Call initialize_jwt_config() at startup.")
    
    if config.use_rsa:
        if use_scoped:
            if not config.scoped_jwt_private_key:
                raise ValueError("scopedJwtPrivateKey not available")
            return config.scoped_jwt_private_key
        else:
            if not config.jwt_private_key:
                raise ValueError("jwtPrivateKey not available")
            return config.jwt_private_key
    else:
        if use_scoped:
            if not config.scoped_jwt_secret:
                raise ValueError("scopedJwtSecret not available")
            return config.scoped_jwt_secret
        else:
            if not config.jwt_secret:
                raise ValueError("jwtSecret not available")
            return config.jwt_secret


def get_verification_key(use_scoped: bool = True) -> str:
    """
    Get the appropriate key for verifying JWTs.
    
    Args:
        use_scoped: Whether to use scoped JWT key
        
    Returns:
        str: The verification key (public key for RS256, secret for HS256)
        
    Raises:
        ValueError: If configuration is not initialized or key is missing
    """
    config = get_jwt_config()
    
    if not config:
        raise ValueError("JWT configuration not initialized. Call initialize_jwt_config() at startup.")
    
    if config.use_rsa:
        if use_scoped:
            if not config.scoped_jwt_public_key:
                raise ValueError("scopedJwtPublicKey not available")
            return config.scoped_jwt_public_key
        else:
            if not config.jwt_public_key:
                raise ValueError("jwtPublicKey not available")
            return config.jwt_public_key
    else:
        # For HS256, signing and verification use the same secret
        if use_scoped:
            if not config.scoped_jwt_secret:
                raise ValueError("scopedJwtSecret not available")
            return config.scoped_jwt_secret
        else:
            if not config.jwt_secret:
                raise ValueError("jwtSecret not available")
            return config.jwt_secret
