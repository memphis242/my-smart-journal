// Serves a built web UI with the same headers `msj serve` sends (strict CSP),
// so end-to-end tests catch anything the CSP would block in production.
// Usage: node scripts/serve-static.mjs --dir <build dir> --port <port>
import { createReadStream } from 'node:fs';
import { stat } from 'node:fs/promises';
import { createServer } from 'node:http';
import path from 'node:path';

const args = new Map();
for (let i = 2; i < process.argv.length; i += 2) args.set(process.argv[i], process.argv[i + 1]);
const root = path.resolve(args.get('--dir') ?? '');
const port = Number(args.get('--port') ?? '15174');
if (args.get('--dir') === undefined || !Number.isInteger(port)) {
  console.error('Usage: node scripts/serve-static.mjs --dir <build dir> --port <port>');
  process.exit(2);
}
try {
  if (!(await stat(path.join(root, 'index.html'))).isFile()) throw new Error('not a file');
} catch (error) {
  console.error(`No index.html in ${root} (${error instanceof Error ? error.message : String(error)}). Build first: pnpm build:mock`);
  process.exit(1);
}

const TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json',
  '.webmanifest': 'application/manifest+json',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.woff2': 'font/woff2',
  '.woff': 'font/woff',
  '.map': 'application/json',
};

const HEADERS = {
  'Content-Security-Policy':
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'no-referrer',
  'Permissions-Policy': 'microphone=(self)',
};

async function resolve(urlPath) {
  const decoded = decodeURIComponent(urlPath.split('?')[0] ?? '/');
  const candidate = path.resolve(root, `.${decoded}`);
  if (!candidate.startsWith(root)) return null;
  try {
    const info = await stat(candidate);
    if (info.isFile()) return candidate;
  } catch {
    // Unknown paths fall back to index.html below, like the backend.
  }
  return decoded.startsWith('/assets/') || decoded.startsWith('/api/') ? null : path.join(root, 'index.html');
}

createServer((request, response) => {
  void resolve(request.url ?? '/').then((file) => {
    if (file === null) {
      response.writeHead(404, { ...HEADERS, 'Content-Type': 'text/plain' });
      response.end('Not found');
      return;
    }
    const type = TYPES[path.extname(file)] ?? 'application/octet-stream';
    const immutable = file.includes(`${path.sep}assets${path.sep}`);
    response.writeHead(200, {
      ...HEADERS,
      'Content-Type': type,
      'Cache-Control': immutable ? 'public, max-age=31536000, immutable' : 'no-cache',
    });
    createReadStream(file).pipe(response);
  });
}).listen(port, '127.0.0.1', () => {
  console.log(`Serving ${root} on http://127.0.0.1:${port}`);
});
