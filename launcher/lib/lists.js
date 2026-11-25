const VERSION = /\b(?:v)?([0-9]{2}\.[0-9]{1,2}\.[0-9]{1,2}(?:\.[0-9]+)?)\b/;
const stripAnsi = s => s.replace(/\x1b\[[0-9;]*m/g, '');

export function parsePlugins(harness, output) {
  const text = stripAnsi(output);
  if (harness === 'copilot' && !text.trim().startsWith('[') && !text.trim().startsWith('{')) {
    if (/No plugins installed/i.test(text)) return [];
    // Copilot prints human-readable blocks, one header per plugin.
    const blocks = text.split(/(?=^.*\b[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+\b)/m);
    const result = [];
    for (const block of blocks) {
      if (!/\bmarc@nexaduo\b/.test(block)) continue;
      const version = block.match(VERSION)?.[1];
      if (!version) throw new Error('Cannot read Copilot plugin version');
      result.push({ version, scope: '', enabled: !/disabled|enabled:\s*false/i.test(block), source: block.match(/Source:\s*(.+)/i)?.[1] });
    }
    if (!result.length && !/plugins?\s*:\s*0/i.test(text)) throw new Error('Unrecognized Copilot plugin list');
    return result;
  }
  let data;
  try { data = JSON.parse(text); } catch { throw new Error(`Invalid ${harness} plugin list JSON`); }
  const rows = Array.isArray(data) ? data : harness === 'codex' ? data.installed : data.imports;
  if (!Array.isArray(rows)) throw new Error(`Unrecognized ${harness} plugin list`);
  return rows.filter(p => {
    const id = p.id || p.pluginId;
    if (id) return id === 'marc@nexaduo' || (harness === 'agy' && id === 'marc');
    return p.name === 'marc' && (harness === 'agy' || (p.marketplaceName || p.marketplace) === 'nexaduo');
  }).filter(p => p.installed !== false).map(p => ({
    version: p.version, scope: p.scope || '', projectPath: p.projectPath,
    enabled: p.enabled, source: p.source, installedFrom: p.installedFrom,
  }));
}

export function parseMarketplaces(harness, output) {
  let data;
  try { data = JSON.parse(stripAnsi(output)); } catch { throw new Error(`Invalid ${harness} marketplace list JSON`); }
  // Claude returns an array; Codex has a marketplaces array (also accept its
  // name-keyed map). Keep the source fields for legacy pin diagnostics.
  const rows = Array.isArray(data) ? data : data.marketplaces;
  if (Array.isArray(rows)) return rows.filter(p => (p.name || p.marketplaceName) === 'nexaduo');
  if (rows && typeof rows === 'object') return rows.nexaduo ? [rows.nexaduo] : [];
  if (data.nexaduo) return [data.nexaduo];
  throw new Error('Unrecognized marketplace list');
}

const REPOS = new Set(['NexaDuo/mARC', 'https://github.com/NexaDuo/mARC', 'https://github.com/NexaDuo/mARC.git']);

export function marketplaceMatches(harness, market) {
  const source = market.marketplaceSource ?? market.source;
  // Pins are registration drift even if the plugin catalog itself is current.
  const pinned = value => value && typeof value === 'object' && Object.entries(value).some(([key, v]) =>
    (['ref', 'branch', 'tag', 'rev', 'commit', 'sha', 'gitRef'].includes(key) && v != null && v !== '') ||
    (typeof v === 'object' && pinned(v)));
  if (pinned(market)) return false;
  if (typeof source === 'string') return harness !== 'codex' &&
    (source === 'NexaDuo/mARC' || source === 'GitHub: NexaDuo/mARC');
  if (!source || typeof source !== 'object') return false;
  if (harness === 'codex') return (source.sourceType ?? source.source) === 'git' &&
    REPOS.has(source.sourceType ? source.source : source.url);
  return source.source === 'github' && source.repo === 'NexaDuo/mARC';
}

export function marketplaceWarnings(marketplaces, harness) {
  const text = JSON.stringify(marketplaces);
  const pinned = /#[^"\s]+|"(?:ref|branch|tag)"\s*:\s*"[^"\s]+"/.test(text);
  if (pinned) return ['legacy-pinned marketplace; manual re-registration required'];
  return marketplaces.some(m => !marketplaceMatches(harness, m)) ? ['marketplace source drift; expected NexaDuo/mARC unpinned'] : [];
}

export function sourceHasPointer(source, tag, harness) {
  const folder = { claude: 'claude-code', copilot: 'copilot', codex: 'codex' }[harness];
  return source !== null && typeof source === 'object' && source.ref === tag &&
    source.path === `harnesses/${folder}/marc` && (harness === 'copilot' ?
      source.source === 'github' && source.repo === 'NexaDuo/mARC' :
      source.source === 'git-subdir' && source.url === 'NexaDuo/mARC');
}

// Native lists sometimes expose the catalog checkout root instead of plugin
// source details. Reading that catalog is safe; never write it or fetch a URL.
export async function readCatalogSource(harness, marketplaces, rows, env = process.env) {
  const { readFile } = await import('node:fs/promises');
  const { default: path } = await import('node:path');
  const catalog = { claude: '.claude-plugin/marketplace.json', copilot: '.github/plugin/marketplace.json', codex: '.agents/plugins/marketplace.json' }[harness];
  const roots = [];
  for (const market of marketplaces) {
    for (const key of ['root', 'path', 'installLocation', 'installedRoot']) if (typeof market[key] === 'string') roots.push(market[key]);
    if (typeof market.source === 'string' && market.source.startsWith('Local: ')) roots.push(market.source.slice(7));
  }
  for (const row of rows) if (row.installedFrom) roots.push(row.installedFrom);
  if (harness === 'copilot' && marketplaces.some(m => /^GitHub: NexaDuo\/mARC(?:#|$)/.test(m.source || ''))) {
    // Copilot 1.0.89 reports the GitHub slug, not its checkout path. This
    // native cache location was probed with a local Git URL redirect (#424).
    const { homedir } = await import('node:os');
    const home = env.HOME || env.USERPROFILE || homedir();
    const config = env.COPILOT_HOME || path.join(home, '.copilot');
    for (const base of [config, home]) roots.push(path.join(base, '.cache', 'copilot', 'marketplaces', 'NexaDuo-mARC'));
  }
  for (const root of roots.filter(p => path.isAbsolute(p))) {
    try {
      const data = JSON.parse(await readFile(path.join(root, catalog), 'utf8'));
      if (data.name !== 'nexaduo') continue;
      const entries = data.plugins.filter(p => p.name === 'marc');
      if (entries.length === 1) return entries[0].source;
    } catch { /* Absence is reported as unverified by doctor, never fetched. */ }
  }
  return null;
}
