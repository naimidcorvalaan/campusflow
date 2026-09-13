const { chromium } = require("playwright");
const http = require("http");
const net = require("net");

function startHeaderReplacingProxy(target, expected) {
  const targetUrl = new URL(target);
  const targetPort = Number(targetUrl.port || 80);
  const replaceHeaders = (source) => ({
    ...source,
    host: targetUrl.host,
    "x-campusflow-probe": expected,
  });
  const server = http.createServer((request, response) => {
    const upstream = http.request({
      hostname: targetUrl.hostname,
      port: targetPort,
      path: request.url,
      method: request.method,
      headers: replaceHeaders(request.headers),
    }, (upstreamResponse) => {
      response.writeHead(upstreamResponse.statusCode, upstreamResponse.headers);
      upstreamResponse.pipe(response);
    });
    request.pipe(upstream);
  });
  const sockets = new Set();
  server.on("connection", (socket) => {
    sockets.add(socket);
    socket.on("close", () => sockets.delete(socket));
  });
  server.probeSockets = sockets;
  server.on("upgrade", (request, clientSocket) => {
    const upstreamSocket = net.connect(targetPort, targetUrl.hostname, () => {
      const headers = replaceHeaders(request.headers);
      const lines = [`${request.method} ${request.url} HTTP/${request.httpVersion}`];
      for (const [name, value] of Object.entries(headers)) {
        if (Array.isArray(value)) {
          for (const item of value) lines.push(`${name}: ${item}`);
        } else if (value !== undefined) {
          lines.push(`${name}: ${value}`);
        }
      }
      upstreamSocket.write(`${lines.join("\r\n")}\r\n\r\n`);
      clientSocket.pipe(upstreamSocket);
      upstreamSocket.pipe(clientSocket);
    });
    upstreamSocket.on("error", () => clientSocket.destroy());
  });
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolve(server));
  });
}

(async () => {
  const target = process.argv[2];
  const expected = process.env.CAMPUSFLOW_HEADER_PROBE_EXPECTED;
  if (!target || !expected) throw new Error("probe URL and expected marker required");
  const proxy = await startHeaderReplacingProxy(target, expected);
  const address = proxy.address();
  const url = `http://127.0.0.1:${address.port}`;
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CAMPUSFLOW_BROWSER_EXECUTABLE ||
      "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  });
  try {
    const context = await browser.newContext({
      // The client tries to spoof the protected name; the controlled proxy
      // replaces it for both HTTP and WebSocket requests.
      extraHTTPHeaders: { "X-CampusFlow-Probe": "spoofed-by-browser" },
    });
    const page = await context.newPage();
    await page.goto(url, { waitUntil: "domcontentloaded" });
    await page.getByText(/probe_header_received=(true|false)/).last().waitFor({ timeout: 15000 });
    const body = await page.locator("body").innerText();
    const marker = (name) => {
      const match = body.match(new RegExp(`${name}=(true|false)`));
      return match ? match[1] : "missing";
    };
    const received = marker("probe_header_received");
    const present = marker("probe_header_present");
    const configured = marker("probe_expected_configured");
    process.stdout.write(`probe_header_received=${received}\n`);
    process.stdout.write(`probe_header_present=${present}\n`);
    process.stdout.write(`probe_expected_configured=${configured}\n`);
    if (received !== "true") process.exitCode = 2;
  } finally {
    await browser.close();
    for (const socket of proxy.probeSockets) socket.destroy();
    await new Promise((resolve) => proxy.close(resolve));
  }
})().catch((error) => {
  process.stderr.write(`header_probe=failed (${error.name})\n`);
  process.exit(2);
});
