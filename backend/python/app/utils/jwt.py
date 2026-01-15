import base64
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict

from jose import jwt  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.config.constants.service import config_node_constants

# Global cache for RSA keys loaded at startup
_RSA_KEYS_CACHE: Optional[Dict[str, Dict[str, str]]] = None


def get_jwt_algorithm() -> str:
    """Get the JWT algorithm from environment configuration"""
    return "RS256" if os.getenv("JWT_ENCRYPTION_KEY") == "RS256" else "HS256"


async def initialize_rsa_keys_async(config_service) -> Dict[str, Dict[str, str]]:
    """
    Initialize and load RSA keys from etcd asynchronously.
    This should be called at application startup.
    
    Args:
        config_service: The configuration service instance
    
    Returns:
        dict: Dictionary with 'regular' and 'scoped' keys
    """
    global _RSA_KEYS_CACHE
    keys = {}
    
    algorithm = get_jwt_algorithm()
    if algorithm != "RS256":
        return keys
    
    print("🔑 Initializing RSA keys from etcd...")
    
    try:
        # Get secret keys from etcd
        secret_keys = await config_service.get_config(config_node_constants.SECRET_KEYS.value)
        
        if not secret_keys:
            print("⚠ No secret keys found in etcd")
            return keys
        
        # Extract RSA public keys
        if 'jwtPublicKey' in secret_keys:
            keys['regular'] = {
                'key': secret_keys['jwtPublicKey'],
                'algorithm': 'RS256'
            }
            print(f"✓ Loaded regular JWT public key from etcd")
        else:
            print("⚠ jwtPublicKey not found in etcd secret keys")
        
        if 'scopedJwtPublicKey' in secret_keys:
            keys['scoped'] = {
                'key': secret_keys['scopedJwtPublicKey'],
                'algorithm': 'RS256'
            }
            print(f"✓ Loaded scoped JWT public key from etcd")
        else:
            print("⚠ scopedJwtPublicKey not found in etcd secret keys")
        
    except Exception as e:
        print(f"❌ Failed to load RSA keys from etcd: {str(e)}")
        import traceback
        traceback.print_exc()
    
    _RSA_KEYS_CACHE = keys
    return keys


def initialize_rsa_keys_sync() -> Dict[str, Dict[str, str]]:
    """
    Initialize RSA keys synchronously (without async context).
    This is used when keys need to be loaded outside of async context.
    
    Returns:
        dict: Dictionary with 'regular' and 'scoped' keys
    """
    global _RSA_KEYS_CACHE
    
    if _RSA_KEYS_CACHE is not None:
        return _RSA_KEYS_CACHE
    
    keys = {}
    algorithm = get_jwt_algorithm()
    
    if algorithm != "RS256":
        return keys
    
    print("🔑 Initializing RSA keys synchronously...")
    
    # Try to load from etcd synchronously
    try:
        from app.config.key_value_store_factory import KeyValueStoreFactory, StoreConfig
        from app.config.constants.store_type import StoreType
        from app.config.constants.service import config_node_constants
        from app.utils.logger import create_logger
        import asyncio
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            logger = create_logger("jwt-init")
            
            async def fetch_keys():
                # Create etcd store config
                store_config = StoreConfig(
                    store_type=StoreType.ETCD,
                    host=os.getenv('ETCD_HOST', 'localhost'),
                    port=int(os.getenv('ETCD_PORT', '2379'))
                )
                
                # Get encrypted store instance
                kv_store = KeyValueStoreFactory.get_store(store_config, logger)
                
                # Get secret keys from etcd
                secret_keys = await kv_store.get_key(config_node_constants.SECRET_KEYS.value)
                
                if not secret_keys:
                    print("⚠ No secret keys found in etcd")
                    return {}
                
                if isinstance(secret_keys, str):
                    secret_keys = json.loads(secret_keys)
                
                result = {}
                if 'jwtPublicKey' in secret_keys:
                    result['regular'] = {
                        'key': secret_keys['jwtPublicKey'],
                        'algorithm': 'RS256'
                    }
                    print(f"✓ Loaded regular JWT public key from etcd")
                
                if 'scopedJwtPublicKey' in secret_keys:
                    result['scoped'] = {
                        'key': secret_keys['scopedJwtPublicKey'],
                        'algorithm': 'RS256'
                    }
                    print(f"✓ Loaded scoped JWT public key from etcd")
                
                return result
            
            keys = loop.run_until_complete(fetch_keys())
        finally:
            loop.close()
    except Exception as e:
        print(f"❌ Failed to initialize RSA keys from etcd: {str(e)}")
        import traceback
        traceback.print_exc()
    
    _RSA_KEYS_CACHE = keys
    return keys


def load_rsa_key(key_env_var: str, key_path_env_var: str, default_path: str, key_name: str = None) -> str:
    """
    Load RSA key from environment variable, etcd, or file.
    
    Args:
        key_env_var: Environment variable name for base64 encoded key
        key_path_env_var: Environment variable name for key file path
        default_path: Default file path if env vars not set
        key_name: Key name in etcd (e.g., 'jwtPrivateKey', 'jwtPublicKey')
        
    Returns:
        str: The RSA key content
        
    Raises:
        ValueError: If key cannot be loaded
    """
    print(f"🔑 Loading RSA key: {key_name or 'unknown'}")
    
    # First check if key is provided as base64 encoded string
    key_b64 = os.getenv(key_env_var)
    if key_b64:
        print(f"✓ Found {key_name} in environment variable {key_env_var}")
        return base64.b64decode(key_b64).decode('utf-8')
    
    # Try to get from etcd if key_name is provided
    if key_name:
        print(f"Attempting to load {key_name} from etcd...")
        try:
            key_from_etcd = _load_key_from_etcd(key_name)
            if key_from_etcd:
                print(f"✓ Successfully loaded {key_name} from etcd")
                return key_from_etcd
            else:
                print(f"⚠ {key_name} not found in etcd, falling back to file")
        except Exception as e:
            # Log error but continue to try file path
            print(f"❌ Failed to get {key_name} from etcd: {str(e)}")
    
    # Otherwise, read from file path
    key_path = os.getenv(key_path_env_var, default_path)
    print(f"Attempting to load {key_name} from file: {key_path}")
    try:
        with open(key_path, 'r') as f:
            content = f.read()
            print(f"✓ Successfully loaded {key_name} from file")
            return content
    except Exception as e:
        error_msg = f"Failed to load RSA key '{key_name}' from all sources. Last error (file): {str(e)}"
        print(f"❌ {error_msg}")
        raise ValueError(error_msg)


def _load_key_from_etcd(key_name: str) -> Optional[str]:
    """
    Load key from etcd store synchronously.
    
    Args:
        key_name: Key name in etcd (e.g., 'jwtPrivateKey', 'jwtPublicKey')
        
    Returns:
        str: The RSA key content or None if not found
    """
    try:
        from app.config.key_value_store_factory import KeyValueStoreFactory, StoreConfig
        from app.config.constants.store_type import StoreType
        from app.config.constants.service import config_node_constants
        from app.utils.logger import create_logger
        import asyncio
        
        # Get or create event loop
        try:
            loop = asyncio.get_running_loop()
            # We're already in an async context, can't use run_until_complete
            print(f"Cannot load {key_name} from etcd - already in async context")
            return None
        except RuntimeError:
            # No running loop, create a new one
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            try:
                logger = create_logger("jwt-etcd")
                
                async def fetch_key():
                    try:
                        # Create etcd store config
                        store_config = StoreConfig(
                            store_type=StoreType.ETCD,
                            host=os.getenv('ETCD_HOST', 'localhost'),
                            port=int(os.getenv('ETCD_PORT', '2379'))
                        )
                        
                        logger.debug(f"Connecting to etcd at {store_config.host}:{store_config.port}")
                        
                        # Get encrypted store instance
                        kv_store = KeyValueStoreFactory.get_store(store_config, logger)
                        
                        # Get encrypted secret keys from etcd
                        logger.debug(f"Fetching {config_node_constants.SECRET_KEYS.value} from etcd")
                        encrypted_data = await kv_store.get_key(config_node_constants.SECRET_KEYS.value)
                        
                        if not encrypted_data:
                            logger.warning(f"No secret keys found in etcd for path {config_node_constants.SECRET_KEYS.value}")
                            return None
                        
                        logger.debug(f"Got data from etcd, type: {type(encrypted_data)}")
                        
                        # The encrypted store automatically decrypts the data
                        # So encrypted_data is actually the decrypted JSON
                        if isinstance(encrypted_data, str):
                            secret_keys = json.loads(encrypted_data)
                        elif isinstance(encrypted_data, dict):
                            secret_keys = encrypted_data
                        else:
                            logger.warning(f"Unexpected data type from etcd: {type(encrypted_data)}")
                            return None
                        
                        result = secret_keys.get(key_name)
                        if result:
                            logger.debug(f"✓ Successfully loaded {key_name} from etcd")
                        else:
                            logger.warning(f"Key {key_name} not found in secret keys")
                        return result
                    except Exception as e:
                        logger.error(f"Error in fetch_key: {str(e)}")
                        import traceback
                        traceback.print_exc()
                        return None
                
                result = loop.run_until_complete(fetch_key())
                return result
            finally:
                loop.close()
    except Exception as e:
        print(f"Error loading key from etcd: {str(e)}")
        import traceback
        traceback.print_exc()
        return None


def is_jwt_expired(token: str) -> bool:
    """
    Check if JWT token is expired
    Args:
        token: JWT token string
    Returns:
        True if token is expired, False otherwise
    """
    if not token:
        return True

    # Split the JWT token into its parts
    TOKEN_PARTS = 3
    parts = token.split('.')
    if len(parts) != TOKEN_PARTS:
        return True

    # Decode the payload (second part)
    payload = parts[1]

    # Add padding if necessary
    padding = len(payload) % 4
    if padding:
        payload += '=' * (4 - padding)

    # Decode base64
    decoded_payload = base64.urlsafe_b64decode(payload)
    payload_data = json.loads(decoded_payload)

    # Check if 'exp' claim exists
    if 'exp' not in payload_data:
        return True

    # Get current timestamp
    current_time = datetime.utcnow().timestamp()

    # Check if token is expired
    return payload_data['exp'] < current_time


async def generate_jwt(config_service: ConfigurationService, token_payload: dict, use_scoped: bool = True) -> str:
    """
    Generate a JWT token using configured algorithm (HS256 or RS256).

    Args:
        config_service: Configuration service instance
        token_payload (dict): The payload to include in the JWT
        use_scoped (bool): Whether to use scoped JWT keys (default: True)

    Returns:
        str: The generated JWT token
    """
    from app.utils.jwt_config import get_jwt_config, get_signing_key, initialize_jwt_config
    
    # Ensure configuration is loaded
    config = get_jwt_config()
    if not config:
        print("⚠ JWT config not cached, loading from etcd...")
        config = await initialize_jwt_config(config_service)
    
    # Get the appropriate signing key
    key = get_signing_key(use_scoped)
    algorithm = config.algorithm

    # Add standard claims if not present
    if "exp" not in token_payload:
        # Set expiration to 1 hour from now
        token_payload["exp"] = datetime.now(timezone.utc) + timedelta(hours=1)

    if "iat" not in token_payload:
        # Set issued at to current time
        token_payload["iat"] = datetime.now(timezone.utc)

    # Generate the JWT token
    token = jwt.encode(token_payload, key, algorithm=algorithm)

    return token


def get_keys_for_verification() -> Dict[str, Dict[str, str]]:
    """
    Get all keys for JWT verification based on configured algorithm.
    Uses cached keys if available, otherwise initializes them.
    
    Returns:
        dict: Dictionary with 'regular' and 'scoped' keys
    """
    global _RSA_KEYS_CACHE
    
    algorithm = get_jwt_algorithm()
    
    if algorithm == "RS256":
        # Use cached keys if available
        if _RSA_KEYS_CACHE is not None:
            return _RSA_KEYS_CACHE
        
        # If not cached, try to initialize synchronously
        print("⚠ RSA keys not cached, attempting to load...")
        return initialize_rsa_keys_sync()
    else:
        # For HS256, we'll need to get secrets from config service
        # This will be handled in the auth middleware
        return {'algorithm': 'HS256'}


# Backward compatibility
def get_public_keys() -> dict:
    """
    Get all public keys for JWT verification.
    Maintained for backward compatibility.
    
    Returns:
        dict: Dictionary with 'regular' and 'scoped' public keys
    """
    keys_info = get_keys_for_verification()
    result = {}
    
    for key_type, info in keys_info.items():
        if key_type != 'algorithm' and isinstance(info, dict):
            result[key_type] = info.get('key', '')
    
    return result