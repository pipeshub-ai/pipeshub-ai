##### BUILD STAGE ########
FROM python:3.10 AS builder

WORKDIR /build

# Create a virtual environment in the /venv directory
RUN python -m venv /venv

# Activate the virtual environment and install build dependencies
ENV PATH="/venv/bin:$PATH"
# Install dependencies
COPY ./pyproject.toml ./
RUN pip3 install --no-cache-dir -e . && \
    pip3 install --no-cache-dir pyinstaller

# Copy application files
COPY app/ ./app/

# Set PYTHONPATH
ENV PYTHONPATH="/build:$PYTHONPATH"

# Build binaries
RUN pyinstaller --hidden-import=requests --hidden-import=urllib3 \
    --hidden-import=app.indexing_main --hidden-import=dateutil --hidden-import=dateutil.tz \
    --hidden-import=dateutil.zoneinfo --collect-all=requests --hidden-import=pydantic \
    --hidden-import=pydantic-core --hidden-import=pydantic.deprecated.decorator \
    --collect-submodules=dependency_injector --collect-all=dateutil app/indexing_main.py

RUN pyinstaller --hidden-import=requests --hidden-import=urllib3 \
    --hidden-import=app.connectors_main --hidden-import=dateutil --hidden-import=dateutil.tz \
    --hidden-import=dateutil.zoneinfo --collect-all=requests --hidden-import=pydantic \
    --hidden-import=pydantic-core --hidden-import=pydantic.deprecated.decorator \
    --collect-submodules=dependency_injector --collect-all=dateutil app/connectors_main.py

RUN pyinstaller --hidden-import=requests --hidden-import=urllib3 \
    --hidden-import=app.query_main --hidden-import=dateutil --hidden-import=dateutil.tz \
    --hidden-import=dateutil.zoneinfo --collect-all=requests --hidden-import=pydantic \
    --hidden-import=pydantic-core --hidden-import=pydantic.deprecated.decorator \
    --collect-submodules=dependency_injector --collect-all=dateutil app/query_main.py

##### FINAL STAGE #####
FROM python:3.10 AS final

WORKDIR /app

# Copy compiled binaries
COPY --from=builder /build/dist/indexing_main ./
COPY --from=builder /build/dist/connectors_main ./
COPY --from=builder /build/dist/query_main ./

ENV DEBIAN_FRONTEND=noninteractive TZ=Etc/UTC

# Install necessary runtime libraries
RUN apt-get update && apt-get install -y \
    librocksdb-dev libgflags-dev libsnappy-dev zlib1g-dev \
    libbz2-dev liblz4-dev libzstd-dev libssl-dev ca-certificates libspatialindex-dev libpq5\
 && rm -rf /var/lib/apt/lists/*
 # Ensure executable permission
RUN chmod +x /app/indexing_main
RUN chmod +x /app/connectors_main
RUN chmod +x /app/query_main

# Expose necessary ports
EXPOSE 8000 8088 8091

CMD sh -c "/app/connectors_main & /app/indexing_main & /app/query_main & wait"
