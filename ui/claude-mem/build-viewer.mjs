import { build } from 'esbuild';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
const root = path.dirname(fileURLToPath(import.meta.url));
await build({ entryPoints: [path.join(root, 'viewer/index.tsx')], outfile: path.join(root, '../../contextrail/ui_static/viewer-bundle.js'), bundle: true, minify: true, sourcemap: false, target: 'es2022', define: { 'process.env.NODE_ENV': '"production"' }, legalComments: 'linked' });
