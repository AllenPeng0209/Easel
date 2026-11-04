import { readFileSync } from 'node:fs';
import original from '../../.scratch/beeftv/source/web/vite.config.ts';

// Only document saving and read-only discovery receive an ordinary workspace
// client identity. This proxy never reads or forwards an owner credential.
const config = original as any;
const proxy = config.server.proxy['/api'];
proxy.configure = (server: any) => {
  server.on('proxyReq', (request: any, incoming: any) => {
    const path = String(incoming.url || '').split('?')[0];
    const allowed = (incoming.method === 'POST' && path === '/api/ops/canvas.document.commit') ||
      (incoming.method === 'GET' && path === '/api/ops');
    const file = process.env.BEEFTV_WORKSHOP_CLIENT_FILE;
    if (allowed && file) {
      const client = JSON.parse(readFileSync(file, 'utf8'));
      request.setHeader('X-Beeftv-Client', client.client_id);
      request.setHeader('Authorization', 'Bearer ' + client.client_token);
    }
  });
};
export default config;
