from app.config.constants.service import RedisConfig


def build_redis_url(redis_config: dict) -> str:
    """Builds a Redis connection URL from a configuration dictionary."""
    host = redis_config.get('host', 'localhost')
    port = redis_config.get('port', 6379)
    username = redis_config.get('username')
    password = redis_config.get('password')
    db = redis_config.get('db', RedisConfig.REDIS_DB.value)
    tls = redis_config.get('tls', False)

    # Use rediss:// scheme for TLS (equivalent to redis-cli --tls)
    scheme = "rediss" if tls else "redis"

    # Build auth part of URL
    auth_part = ""
    if username and username.strip():
        auth_part = username
        if password and password.strip():
            auth_part += f":{password}"
    elif password and password.strip():
        auth_part = f":{password}"

    if auth_part:
        auth_part += "@"

    return f"{scheme}://{auth_part}{host}:{port}/{db}"
