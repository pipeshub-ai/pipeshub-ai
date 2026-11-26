// Stands in for the Node API: answers 200 on /api/v1/health at $PORT, on $PIPESHUB_BIND_HOST.
import http from "node:http";

http.createServer((_req, res) => res.end("ok")).listen(Number(process.env.PORT), process.env.PIPESHUB_BIND_HOST);
