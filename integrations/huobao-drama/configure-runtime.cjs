// Pin the executable that can load this installation's native SQLite addon.
// Kept outside the vendored source so upstream files remain unchanged.
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');

const backendRequire = createRequire(path.join(__dirname, 'source/backend/package.json'));
const Database = backendRequire('better-sqlite3');
const database = new Database(':memory:');
database.close();

const runtime = {
  schemaVersion: 1,
  node: process.execPath,
  version: process.version,
  modules: process.versions.modules,
};
const destination = path.join(__dirname, '.runtime.json');
fs.writeFileSync(destination + '.tmp', JSON.stringify(runtime, null, 2) + '\n', { mode: 0o600 });
fs.renameSync(destination + '.tmp', destination);
console.log(`Huobao Node runtime pinned: ${runtime.version} (ABI ${runtime.modules})`);
