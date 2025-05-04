#!/bin/bash

# Start the Ollama server in the background
ollama serve &

# Wait for the server to be ready
echo "Waiting for Ollama server to start..."
until curl -s -f http://localhost:11434/api/tags > /dev/null 2>&1; do
    echo "Ollama server not ready yet, waiting..."
    sleep 2
done
echo "Ollama server is up and running!"

# Pull the model you want to use
echo "Pulling the phi4 model..."
ollama pull ${OLLAMA_MODEL:-phi4} # Use environment variable for model name, default to phi4

# Keep the container running
echo "Model downloaded. Ollama is ready for use!"
wait
