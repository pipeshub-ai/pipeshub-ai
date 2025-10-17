from app.config.constants.service import RedisConfig


def build_redis_url(redis_config: dict) -> str:
    """Builds a Redis connection URL from a configuration dictionary."""
    host = redis_config.get('host', 'localhost')
    port = redis_config.get('port', 6379)
    password = redis_config.get('password')
    db = redis_config.get('db', RedisConfig.REDIS_DB.value)

    if password and password.strip():
        return f"redis://:{password}@{host}:{port}/{db}"
    return f"redis://{host}:{port}/{db}"
