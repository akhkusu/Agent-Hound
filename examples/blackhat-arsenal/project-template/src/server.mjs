import { createReadStream } from 'node:fs';
import { createServer } from 'node:http';
import { fileURLToPath } from 'node:url';

import { healthResponse } from './api/health.mjs';

const page = fileURLToPath(new URL('../public/index.html', import.meta.url));

createServer((request, response) => {
  if (request.url === '/api/health') {
    response.writeHead(200, { 'content-type': 'application/json' });
    response.end(JSON.stringify(healthResponse()));
    return;
  }

  response.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
  createReadStream(page).pipe(response);
}).listen(4173, '127.0.0.1', () => {
  console.log('Acme SaaS demo listening on http://127.0.0.1:4173');
});
