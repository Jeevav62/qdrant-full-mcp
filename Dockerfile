FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 app \
    && mkdir /models && chown app /models

USER app
# Bake the default embedding model into the image so the first store/find call does not download it.
ENV FASTEMBED_CACHE_PATH=/models
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('sentence-transformers/all-MiniLM-L6-v2')"

# Serve over HTTP on all interfaces inside the container: http://<host>:8000/mcp
ENV QDRANT_MCP_TRANSPORT=streamable-http \
    FASTMCP_SERVER_HOST=0.0.0.0 \
    FASTMCP_SERVER_PORT=8000
EXPOSE 8000
ENTRYPOINT ["qdrant-full-mcp"]
