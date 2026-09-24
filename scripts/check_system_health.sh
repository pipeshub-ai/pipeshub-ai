#!/bin/bash
set -u

echo "================================================="
echo "       PipesHub Microservices Health Check       "
echo "================================================="
echo ""

# Format: "Service Name|Port|Path"
SERVICES=(
    "Frontend (Next.js)|3001|/"
    "API Gateway (Node.js)|3000|/api/v1/health"
    "Query Service (Python)|8000|/health"
    "Embedding Service (Python)|8002|/health"
    "Docling Service (Python)|8081|/health"
    "Connectors Service (Python)|8088|/health"
    "Indexing Service (Python)|8091|/health"
)

if [ "${USE_PARSING_SERVICE:-false}" = "true" ]; then
    SERVICES+=(
        "Parsing Service (Python)|8092|/health"
        "Extraction Service (Python)|8093|/health"
    )
fi

all_healthy=true

for service_info in "${SERVICES[@]}"; do
    IFS="|" read -r name port path <<< "$service_info"
    
    url="http://127.0.0.1:${port}${path}"
    
    # We use curl -s (silent) -o /dev/null (discard body) -w "%{http_code}" to get status code
    status_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 2 "$url")
    curl_exit=$?
    
    if [ $curl_exit -ne 0 ]; then
        echo -e "[\033[31mFAIL\033[0m] $name is offline (Unreachable on port $port)"
        all_healthy=false
    elif [ "$status_code" = "200" ]; then
        echo -e "[\033[32mOK\033[0m] $name is healthy on port $port"
    else
        echo -e "[\033[33mWARN\033[0m] $name returned HTTP $status_code on port $port"
        all_healthy=false
    fi
done

echo ""
echo "================================================="
if [ "$all_healthy" = true ]; then
    echo -e "Status: \033[32mAll Systems Go!\033[0m"
    exit 0
else
    echo -e "Status: \033[31mSome services are offline or degraded.\033[0m"
    exit 1
fi
