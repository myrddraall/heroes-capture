// Adds a map to the site, or refreshes it: copies a pack folder heroes-capture rendered
// (maps\<map id>\pack) into maps/<map id>/, replacing what was there.
//
//   pnpm run site.add-map <path to a pack folder>
import { cpSync, existsSync, readFileSync, rmSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const site = join(dirname(fileURLToPath(import.meta.url)), '..');
const source = process.argv[2] && resolve(process.env.INIT_CWD ?? process.cwd(), process.argv[2]);
if (!source || !existsSync(join(source, 'pack.json'))) {
  console.error('usage: pnpm run site.add-map <path to a pack folder (the one with pack.json)>');
  process.exit(1);
}
const pack = JSON.parse(readFileSync(join(source, 'pack.json'), 'utf8'));
const target = join(site, 'maps', pack.map.id);
rmSync(target, { recursive: true, force: true });
cpSync(source, target, { recursive: true });
console.log(`${pack.map.name}: ${source} -> ${target}`);
