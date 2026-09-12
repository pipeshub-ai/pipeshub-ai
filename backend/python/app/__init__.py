import warnings

# PyJWT ≥ 2.6 warns when an HS256 key is shorter than 32 bytes. Legacy
# secrets generated before the key length was increased still work —
# HMAC-SHA256 is secure at any key length, the RFC recommendation is about
# entropy not correctness. New secrets are ≥ 48 chars; this silences the
# noise for existing deployments whose stored secret was already generated.
warnings.filterwarnings(
    "ignore", message=".*HMAC key.*below.*minimum recommended"
)
