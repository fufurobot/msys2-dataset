#!/usr/bin/env node
/**
 * HTTP transport for the Arch scraper.
 *
 * The Python scraper does all parsing, but it delegates network access to this
 * script. Node's TLS stack reaches the Arch mirrors and the AUR reliably from
 * this environment, whereas Python's `urllib` fails per-host with
 * `[Errno 2] No such file or directory` (the sandbox denies the socket).
 *
 * Reads a JSON request from argv[2] and writes the response body to the path in
 * argv[3]. Retries across a list of mirrors, because the public Arch mirrors are
 * intermittently unreachable.
 *
 * Usage:
 *   node tools/fetch.js '{"urls":["https://..."],"out":"/path/body.bin"}'
 */

const fs = require('fs');

const RETRIES_PER_URL = 4;
const BASE_DELAY_MS = 1500;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function fetchWithRetry(urls, timeoutMs) {
  const errors = [];
  for (const url of urls) {
    for (let attempt = 0; attempt < RETRIES_PER_URL; attempt += 1) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeoutMs);
      try {
        const response = await fetch(url, {
          headers: { 'User-Agent': 'msys2-dataset/0.1 (+https://github.com/fufurobot/msys2-dataset)' },
          signal: controller.signal,
        });
        clearTimeout(timer);
        if (!response.ok) {
          errors.push(`${url} -> HTTP ${response.status}`);
          break; // A 4xx/5xx is not fixed by retrying the same URL.
        }
        const buffer = Buffer.from(await response.arrayBuffer());
        return { url, buffer };
      } catch (error) {
        clearTimeout(timer);
        errors.push(`${url} -> ${error.name}: ${error.message}`);
        // Exponential backoff: mirrors can be briefly saturated.
        await sleep(BASE_DELAY_MS * (attempt + 1));
      }
    }
  }
  throw new Error(`all URLs failed:\n  ${errors.join('\n  ')}`);
}

async function main() {
  const request = JSON.parse(process.argv[2]);
  const { urls, out, timeout_ms: timeoutMs = 120000 } = request;
  if (!Array.isArray(urls) || urls.length === 0) throw new Error('urls must be a non-empty array');
  const { url, buffer } = await fetchWithRetry(urls, timeoutMs);
  fs.writeFileSync(out, buffer);
  process.stderr.write(`fetched ${buffer.length} bytes from ${url}\n`);
}

main().catch((error) => {
  process.stderr.write(`${error.message}\n`);
  process.exit(1);
});
