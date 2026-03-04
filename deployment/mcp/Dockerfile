FROM node:20-slim

RUN groupadd --gid 1001 mcp && \
    useradd --uid 1001 --gid mcp --shell /bin/false --create-home mcp

RUN npm install -g @pipeshub-ai/mcp

ENV TRANSPORT=sse \
    PORT=2718 \
    SERVER_URL="" \
    CLIENT_ID="" \
    CLIENT_SECRET="" \
    TOKEN_URL="" \
    LOG_LEVEL=info \
    NODE_ENV=production

EXPOSE 2718

USER mcp

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD node -e "fetch('http://localhost:2718/').then(r => { if (!r.ok) process.exit(1) }).catch(() => process.exit(1))"

ENTRYPOINT ["sh", "-c", "\
  ARGS=\"--transport $TRANSPORT --port $PORT --log-level $LOG_LEVEL\"; \
  [ -n \"$SERVER_URL\" ] && ARGS=\"$ARGS --server-url $SERVER_URL\"; \
  [ -n \"$CLIENT_ID\" ] && ARGS=\"$ARGS --client-id $CLIENT_ID\"; \
  [ -n \"$CLIENT_SECRET\" ] && ARGS=\"$ARGS --client-secret $CLIENT_SECRET\"; \
  [ -n \"$TOKEN_URL\" ] && ARGS=\"$ARGS --token-url $TOKEN_URL\"; \
  exec mcp start $ARGS"]
