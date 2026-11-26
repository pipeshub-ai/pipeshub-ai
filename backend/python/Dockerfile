##### BUILD STAGE ########
FROM python:3.12@sha256:cad37dd20c7cac861af9003af8796d03ea7a998ec1e3a664429740fc9d7d2691 AS builder

WORKDIR /build

# Create a virtual environment in the /venv directory
RUN python -m venv /venv

# Activate the virtual environment and install build dependencies
ENV PATH="/venv/bin:$PATH"
# Install dependencies from the hashed locks (SEC-SC-07); pyinstaller comes from tools.lock
COPY ./pyproject.toml ./
COPY ./requirements/ ./requirements/
RUN PYTHON=/venv/bin/python PIP_NO_CACHE_DIR=1 sh requirements/install.sh runtime --project . --editable && \
    PYTHON=/venv/bin/python PIP_NO_CACHE_DIR=1 sh requirements/install.sh tools

# Copy application files
COPY app/ ./app/
COPY harness_core/ ./harness_core/

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
FROM python:3.12@sha256:cad37dd20c7cac861af9003af8796d03ea7a998ec1e3a664429740fc9d7d2691 AS final

WORKDIR /app

# Copy compiled binaries
COPY --from=builder /build/dist/indexing_main ./
COPY --from=builder /build/dist/connectors_main ./
COPY --from=builder /build/dist/query_main ./

ENV DEBIAN_FRONTEND=noninteractive TZ=Etc/UTC LANG=C.UTF-8

# Install necessary runtime libraries
# libreoffice-nogui: headless LibreOffice for PPT/PPTX to PDF conversion without
#   GUI/X11 dependencies, significantly reducing image size (fixes #2710)
# fonts-noto-cjk, fonts-wqy-zenhei, fonts-wqy-microhei: CJK font support for
#   correct rendering of Chinese/Japanese/Korean text in converted PDFs (fixes #2712)
RUN apt-get update && apt-get install -y --no-install-recommends \
    librocksdb-dev libgflags-dev libsnappy-dev zlib1g-dev \
    libbz2-dev liblz4-dev libzstd-dev libssl-dev ca-certificates libspatialindex-dev libpq5 \
    ffmpeg \
    libreoffice-nogui \
    fonts-noto-cjk \
    fonts-wqy-zenhei \
    fonts-wqy-microhei \
 && rm -rf /var/lib/apt/lists/*

# Ensure executable permission
RUN chmod +x /app/indexing_main
RUN chmod +x /app/connectors_main
RUN chmod +x /app/query_main

# Expose necessary ports
EXPOSE 8000 8088 8091

CMD sh -c "/app/connectors_main & /app/indexing_main & /app/query_main & wait"
