// Builds the site into dist/: every map in maps/ with the current reference viewer as its
// index.html (its entry in pack.json updated to match), and a home page listing the maps.
import { createHash } from 'node:crypto';
import { cpSync, existsSync, mkdirSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const site = join(dirname(fileURLToPath(import.meta.url)), '..');
const viewer = join(site, '..', 'heroes-capture', 'src', 'heroes_capture', 'viewer.html');
const dist = join(site, 'dist');
const escape = (text) => String(text).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);

rmSync(dist, { recursive: true, force: true });
mkdirSync(join(dist, 'maps'), { recursive: true });
const maps = [];
for (const id of readdirSync(join(site, 'maps')).sort()) {
  const folder = join(site, 'maps', id);
  if (!existsSync(join(folder, 'pack.json'))) continue;
  const out = join(dist, 'maps', id);
  cpSync(folder, out, { recursive: true });
  cpSync(viewer, join(out, 'index.html'));
  const pack = JSON.parse(readFileSync(join(out, 'pack.json'), 'utf8'));
  const page = readFileSync(join(out, 'index.html'));
  pack.files['index.html'] = { bytes: statSync(join(out, 'index.html')).size, sha256: createHash('sha256').update(page).digest('hex') };
  writeFileSync(join(out, 'pack.json'), JSON.stringify(pack, null, 2));
  maps.push(pack);
}
if (!maps.length) throw new Error('no maps in maps/: add one with pnpm run site.add-map <pack folder>');

const cards = maps
  .map((pack) => {
    const thumb = pack.images?.thumbnail;
    const picture = thumb ? `<img src="maps/${pack.map.id}/${thumb.file}" width="${thumb.size[0]}" height="${thumb.size[1]}" alt="">` : '';
    return `      <li><a href="maps/${pack.map.id}/">${picture}<span>${escape(pack.map.name)}</span><small>${escape(pack.map.category ?? '')}</small></a></li>`;
  })
  .join('\n');
writeFileSync(
  join(dist, 'index.html'),
  `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Heroes of the Storm maps</title>
<style>
  :root { --backdrop: #202024; --text: #e8e8ec; --card: #2c2c32; --accent: #8fb4ff; }
  body { margin: 0; padding: 24px 16px; background: var(--backdrop); color: var(--text); font: 15px/1.5 system-ui, sans-serif; }
  main { max-width: 1100px; margin: 0 auto; }
  h1 { font-size: 1.6rem; margin: 0 0 4px; }
  p { margin: 0 0 20px; opacity: 0.8; }
  ul { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 16px; }
  a { display: flex; flex-direction: column; gap: 4px; padding: 10px; border-radius: 10px; background: var(--card); color: inherit; text-decoration: none; }
  a:hover span, a:focus-visible span { color: var(--accent); }
  a:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  img { width: 100%; height: auto; border-radius: 6px; background: #303036; }
  span { font-weight: 600; }
  small { opacity: 0.7; }
</style>
</head>
<body>
<main>
  <h1>Heroes of the Storm maps</h1>
  <p>Rendered from the game by heroes-capture. Choose a map: drag to pan, mouse wheel to zoom.</p>
  <ul>
${cards}
  </ul>
</main>
</body>
</html>
`,
);
console.log(`site -> ${dist} (${maps.length} map${maps.length === 1 ? '' : 's'}: ${maps.map((p) => p.map.name).join(', ')})`);
