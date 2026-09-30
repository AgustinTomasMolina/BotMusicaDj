// `npm run test:unidad`: corre test/*.test.mjs con `node --test`.
//
// Con el Node que corre npm, no con el del PATH: en la PC del trabajo el del PATH es Node 21 y
// Vite 8 (que carga los módulos del front en los tests) pide ^20.19 || >=22.12. Mismo criterio
// que el build de e2e/run.mjs.
import { spawnSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const FRONT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const node = process.env.npm_node_execpath || process.execPath
const r = spawnSync(node, ['--test', 'test/*.test.mjs'], { cwd: FRONT, stdio: 'inherit' })
process.exit(r.status ?? 1)
