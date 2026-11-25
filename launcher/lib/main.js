import { mkdtemp, rm, lstat, stat, readdir, readFile } from 'node:fs/promises';
import os from 'node:os';
import { realpathSync, statSync } from 'node:fs';
import path from 'node:path';
import { createInterface } from 'node:readline/promises';
import { createRunner, resolveExecutable, safeValue } from './process.js';
import { tagToNpm } from './version.js';
import { createStyle, stripControls } from './style.js';
import { safeVersion, parsePlugins, describeSource, parseMarketplaces, marketplaceWarnings, marketplaceMatches, sourceHasPointer, readCatalogSource,
  classifyMarketplace, sourceParts, looksLocal, enrichMarketplaces } from './lists.js';

export const HARNESSES = ['claude', 'copilot', 'codex', 'agy'];
const SCOPES = ['user', 'project', 'local'];
const SLUG = 'NexaDuo/mARC';
const ID = 'marc@nexaduo';
const HELP = `Usage: marc install|update|doctor|uninstall [--yes] [--dry-run]
  [--harness claude|copilot|codex|agy]... [--scope user|project|local]
  [--version <embedded-tag>] [--force [--force-local]]
No --harness: use every harness found on PATH. Node >=20 required.
install reads the native plugin list (read-only) before consent, also in --dry-run, and again after installing.
install never upgrades: an existing install that differs from this release is reported STALE (run update).
--scope applies to Claude only. Non-TTY install/update/uninstall requires --yes
(doctor and --dry-run never change anything and need no --yes).
--force (install/update): re-register a mismatched or stale nexaduo marketplace. The plan shows the
registration it removes, then runs: marketplace remove nexaduo, marketplace add NexaDuo/mARC,
reinstall marc@nexaduo. Only nexaduo is touched. A local or unverifiable source also needs
--force-local. Without --force a mismatch is refused with a manual remedy.
The package release selects the tag; another tag prints its npx command.`;

export function parseArgs(argv) {
  const opts = { command: argv[0], harnesses: [], yes: false, dryRun: false };
  if (argv.length === 1 && argv[0] === '--help') return { help: true };
  if (!['install', 'update', 'doctor', 'uninstall'].includes(opts.command)) throw new Error(HELP);
  for (let i = 1; i < argv.length; i++) {
    const flag = argv[i];
    if (flag === '--yes') opts.yes = true;
    else if (flag === '--force') opts.force = true;
    else if (flag === '--force-local') opts.forceLocal = true;
    else if (flag === '--dry-run') opts.dryRun = true;
    else if (['--harness', '--scope', '--version'].includes(flag)) {
      const value = argv[++i];
      if (typeof value !== 'string') throw new Error(`Missing value for ${flag}`);
      safeValue(value);
      if (flag === '--harness') {
        if (!HARNESSES.includes(value)) throw new Error(`Unknown harness: ${value}`);
        if (!opts.harnesses.includes(value)) opts.harnesses.push(value);
      } else {
        const key = flag.slice(2);
        if (opts[key]) throw new Error(`Repeated ${flag}`);
        opts[key] = value;
      }
    } else throw new Error(`Unknown option: ${flag}`);
  }
  if (opts.forceLocal && !opts.force) throw new Error('--force-local requires --force');
  if (opts.force && !['install', 'update'].includes(opts.command)) throw new Error('--force applies to install and update only');
  if (opts.scope && !SCOPES.includes(opts.scope)) throw new Error('Unknown Claude scope');
  if (opts.scope && opts.harnesses.length && !opts.harnesses.includes('claude')) throw new Error('--scope requires Claude');
  return opts;
}

const listArgs = () => ['plugin', 'list', '--json'];
const scopeArgs = scope => ['--scope', scope];

// A plan has no side effects. Claude update (including dry-run) reads its
// installed scopes and resolves project paths before interactive consent.
export function plan(opts) {
  const plans = [];
  for (const h of opts.harnesses) {
    const commands = [];
    if (opts.command === 'doctor') {
      commands.push(listArgs(h));
      if (h !== 'agy') commands.push(['plugin', 'marketplace', 'list', '--json']);
    }
    else if (h === 'agy' && opts.command !== 'uninstall') commands.push(['plugin', 'list', '--json'], ['<verified shallow tag clone and reinstall; preserve enablement>']);
    else if (opts.command === 'install') {
      commands.push(listArgs(h), ['plugin', 'marketplace', 'list', '--json'], ['plugin', 'marketplace', 'add', SLUG]);
      commands.push(['plugin', h === 'codex' ? 'add' : 'install', ID, ...(h === 'claude' ? scopeArgs(opts.scope || 'user') : [])]);
      commands.push(listArgs(h)); // re-list after install to confirm the version
    } else if (opts.command === 'update') {
      if (h === 'claude') commands.push(listArgs(h));
      commands.push(['plugin', 'marketplace', h === 'codex' ? 'upgrade' : 'update', 'nexaduo']);
      commands.push(h === 'claude' ? ['plugin', 'update', ID, '--scope', opts.scope || '<each installed scope>'] :
        ['plugin', h === 'codex' ? 'add' : 'update', h === 'codex' ? ID : 'marc']);
    } else commands.push(['plugin', h === 'codex' ? 'remove' : 'uninstall', h === 'agy' ? 'marc' : ID,
      ...(h === 'claude' ? scopeArgs(opts.scope || 'user') : [])]);
    plans.push({ harness: h, commands });
  }
  return plans;
}

async function checkTree(root, top = true) {
  // Fail on all symlinks, including parent components and nested plugin files.
  if (!(await lstat(root)).isDirectory() || (await lstat(root)).isSymbolicLink()) throw new Error('Unsafe clone directory');
  for (const entry of await readdir(root, { withFileTypes: true })) {
    const target = path.join(root, entry.name);
    const stat = await lstat(target);
    if (stat.isSymbolicLink()) throw new Error('Symlink in release clone');
    if (top && entry.name === '.git' && stat.isDirectory()) continue;
    if (stat.isDirectory()) await checkTree(target, false);
    else if (!stat.isFile()) throw new Error('Special file in release clone');
  }
}

export function tempRoots(tmpRoot, { platform = process.platform, env = process.env, realpath = realpathSync.native,
  stat = statSync, home = os.homedir(), uid = process.getuid?.() } = {}) {
  const candidates = tmpRoot ? [tmpRoot] : [os.tmpdir(), env.TMP, env.TEMP];
  const roots = [...new Set(candidates.filter(Boolean).flatMap(p => { try { return [realpath(p)]; } catch { return []; } }))];
  const safe = roots.filter(root => {
    try {
      if (platform === 'win32') {
        // Node cannot verify Windows ACL ownership. Only accept canonical
        // paths inside the user's profile, never a system/shared fallback.
        const relative = path.win32.relative(realpath(home), root);
        return !/\s/.test(root) && relative !== '' && relative !== '..' &&
          !relative.startsWith('..\\') && !path.win32.isAbsolute(relative);
      }
      const info = stat(root);
      // Shared POSIX temp is safe only under a root-owned sticky directory.
      // The mkdtemp child is private and checked separately before use.
      return info.isDirectory() && ((info.uid === uid && (info.mode & 0o022) === 0) ||
        (info.uid === 0 && (info.mode & 0o1002) === 0o1002));
    } catch { return false; }
  });
  if (!safe.length) throw new Error('No private temp root available; set TMP/TEMP to a user-owned writable directory without spaces');
  return safe;
}

export async function installAgy({ run, release, tmpRoot }) {
  if (typeof release.sha !== 'string' || release.sha.length !== 40 || !/^[a-f0-9]{40}$/.test(release.sha)) throw new Error('Release SHA is absent; use a published launcher');
  const installed = parsePlugins('agy', await run('agy', listArgs('agy')));
  if (installed.length > 1 || (installed.length && typeof installed[0].enabled !== 'boolean')) throw new Error('Cannot determine Antigravity enabled state');
  const enable = !installed.length || installed[0].enabled;
  let temp;
  for (const root of tempRoots(tmpRoot)) {
    try { temp = await mkdtemp(path.join(root, 'marc-')); break; } catch { /* Try the next existing root. */ }
  }
  if (!temp) throw new Error('Cannot create release directory; set TMP/TEMP to a writable directory without spaces');
  const clone = path.join(temp, 'release');
  // Git config and git's cwd are isolated before clone/checkout: no workspace
  // hooks, URL rewrites, templates or environment-selected repositories.
  const gitOptions = { cwd: temp, isolatedGit: true };
  const cliPath = p => process.platform === 'win32' ? p.replaceAll('\\', '/') : p;
  try {
    if (process.platform !== 'win32') {
      const info = await lstat(temp);
      if (!info.isDirectory() || info.isSymbolicLink() || info.uid !== process.getuid() ||
          (info.mode & 0o777) !== 0o700) throw new Error('Unsafe private release directory');
    }
    await run('git', ['clone', '--depth', '1', '--branch', release.tag, '--no-checkout',
      'https://github.com/NexaDuo/mARC.git', cliPath(clone)], { ...gitOptions, generatedPaths: [7] });
    const options = { ...gitOptions, cwd: clone };
    const sha = (await run('git', ['rev-parse', 'HEAD'], options)).trim();
    if (sha !== release.sha) throw new Error(`Release SHA mismatch: expected ${release.sha}, got ${sha}`);
    const tree = await run('git', ['ls-tree', '-r', 'HEAD'], options);
    if (tree.split('\n').some(line => /^(120000|160000) /.test(line))) throw new Error('Symlink or submodule in release');
    await run('git', ['checkout', '--detach', '--force', 'HEAD'], options);
    await checkTree(clone);
    const manifest = JSON.parse(await readFile(path.join(clone, 'harnesses/antigravity/marc/plugin.json'), 'utf8'));
    if (`v${manifest.version}` !== release.tag) throw new Error('Antigravity manifest/tag mismatch');
    await run('agy', ['plugin', 'install', cliPath(path.join(clone, 'harnesses/antigravity/marc'))], { generatedPaths: [2] });
    if (enable) await run('agy', ['plugin', 'enable', 'marc']);
  } finally {
    await rm(temp, { recursive: true, force: true });
  }
}

// Console output. Plain mode forwards every message to `log` unchanged and
// adds nothing; color mode re-lays the same text out with headings, prompts
// and a result table. Every colored fragment goes through style.paint.
export function makeOut(log, st) {
  const p = st.paint;
  const pending = new Map();
  const last = new Map();
  let buffering = false, group = null;
  const put = (h, text) => {
    if (buffering) { if (!pending.has(h)) pending.set(h, []); pending.get(h).push(text); return; }
    if (h !== group) { if (group !== null) log(''); log(p('harness', `# ${h}`)); group = h; }
    log(text);
  };
  const MARK = { info: '* ', dim: '* ', warn: '! ', error: 'x ' };
  const pad = (v, n) => String(v) + ' '.repeat(Math.max(0, n - String(v).length));
  return {
    header(release, command) {
      if (!st.enabled) return log(`mARC ${release.tag} (${release.npm}); ${command}`);
      log(`${p('brand', 'mARC')} ${p('tag', release.tag)} ${p('dim', `(${release.npm})`)}; ${p('strong', command)}`);
    },
    note(text) { if (st.enabled) { log(p('dim', `* ${text}`)); log(''); } },
    cmdLine(text) { log(st.enabled ? `${p('dim', '$ ')}${p('cmd', text)}` : text); },
    begin(harnesses) { if (st.enabled) { buffering = true; for (const h of harnesses) pending.set(h, []); } },
    flush() {
      last.clear(); // pre-consent lines must not become a result detail
      if (!buffering) return;
      buffering = false;
      for (const [h, lines] of pending) for (const l of lines) put(h, l);
      pending.clear();
    },
    // role: info | dim | warn | error | cmd
    line(h, role, text) {
      if (role !== 'cmd') last.set(h, text);
      if (!st.enabled) return log(text);
      put(h, role === 'cmd' ? `  ${p('dim', '$ ')}${p('cmd', text)}` : `  ${p(role, MARK[role] + text)}`);
    },
    alt(h, plain, role, text) { this.line(h, role, st.enabled ? text : plain); },
    remedy(h, title, cmds) {
      if (!st.enabled) { log(title); for (const c of cmds) log(c); return; }
      put(h, `  ${p('remedy', `+- ${title}`)}`);
      for (const c of cmds) put(h, `  ${p('remedy', '|')}  ${p('dim', '$ ')}${p('cmd', c)}`);
      put(h, `  ${p('remedy', '+-')}`);
    },
    row(h, cols, drift, project) {
      const extra = project === undefined ? '' : stripControls(project);
      if (!st.enabled) return log(cols.join('\t') + (drift ? '\tDRIFT' : '') + (extra ? `\t${extra}` : ''));
      const widths = [7, 10, 7, 8];
      // Every column is painted (so sanitized) and padded on its stripped length.
      const cell = (c, n, role) => p(role, pad(stripControls(c), n));
      const body = cols.slice(1).map((c, i) => cell(c, widths[i + 1], 'info')).join('  ');
      put(h, `  ${cell(cols[0], widths[0], 'harness')}  ${body}${drift ? `  ${p('drift', 'DRIFT')}` : extra ? `  ${' '.repeat(5)}` : ''}${extra ? `  ${p('info', extra)}` : ''}`.trimEnd());
    },
    detail(h) { const t = last.get(h) || 'done';
      return t.startsWith(`${h}: `) ? t.slice(h.length + 2) : t; },
    results(list) {
      if (!st.enabled) { for (const { harness, state } of list) log(`${harness}: ${state}`); return; }
      log('');
      log(p('dim', '* result'));
      log(p('dim', `  ${pad('HARNESS', 7)}  ${pad('STATUS', 6)}  DETAIL`));
      for (const { harness, state, detail } of list) {
        log(`  ${p('harness', pad(harness, 7))}  ${p(state === 'OK' ? 'ok' : state === 'STALE' ? 'stale' : 'failed', pad(state, 6))}  ${p('info', detail)}`);
      }
    },
  };
}

function manualRemedy(h, out, scope = 'user') {
  out.remedy(h, `${h}: Manual remedy (review first; this removes the marketplace registration):`, [
    `${h} plugin marketplace remove nexaduo`,
    `${h} plugin marketplace add NexaDuo/mARC`,
    `${h} plugin ${h === 'codex' ? 'add' : 'install'} marc@nexaduo${h === 'claude' ? ` --scope ${scope}` : ''}`,
  ]);
}

const sameVersion = (version, release) => `v${String(version).replace(/^v/, '')}` === release.tag;

// Rows that count as "already installed" for this request. Claude is
// scope-aware; project/local rows from other repos never count.
function samePath(a, b) {
  try { return realpathSync.native(a) === realpathSync.native(b); }
  catch { return path.resolve(a) === path.resolve(b); }
}
function installedRows(h, rows, scope, cwd) {
  if (h !== 'claude') return rows;
  // A Claude row with no scope field is a user-scope install.
  return rows.filter(r => (r.scope || 'user') === scope && (scope === 'user' ||
    (typeof r.projectPath === 'string' && path.isAbsolute(r.projectPath) && samePath(r.projectPath, cwd))));
}

// Compare installed rows with the launcher release. Returns the versions found
// and whether any of them differs.
function versionState(rows, release) {
  const found = [...new Set(rows.map(r => safeVersion(r.version)))];
  return { found: found.join(', '), stale: rows.some(r => !r.version || !sameVersion(r.version, release)) };
}

// Project column for Claude project/local rows: ~-relative under home, else
// absolute. Control characters are stripped. Path semantics follow the injected
// platform (path.win32 vs path.posix), not the host, and the ~-relative form
// always uses forward slashes ("~/repos/app") on every platform.
export function displayProject(projectPath, home, platform = process.platform) {
  if (typeof projectPath !== 'string' || !projectPath) return '-';
  const P = platform === 'win32' ? path.win32 : path.posix;
  const clean = stripControls(projectPath);
  if (!clean) return '-';
  const h = typeof home === 'string' ? stripControls(home) : '';
  // A filesystem-root home would turn every path into "~/..."; print absolute.
  if (h && P.dirname(h) !== h && P.isAbsolute(clean)) {
    const rel = P.relative(h, clean);
    if (rel === '') return '~';
    if (rel !== '..' && !rel.startsWith(`..${P.sep}`) && !P.isAbsolute(rel)) return `~/${rel.split(P.sep).join('/')}`;
  }
  return clean;
}

// Returns { state: OK|STALE|FAILED, detail }. STALE: the only problems are a
// version mismatch and/or a catalog that tracks main. Anything else is FAILED.
async function doctor(h, run, release, out, scope, home, platform) {
  const rows = parsePlugins(h, await run(h, listArgs(h))).filter(p => h !== 'claude' || !scope || p.scope === scope);
  let failed = !rows.length, stale = false;
  if (!rows.length) out.alt(h, `${h}\tmissing\t-`, 'error', `${h}: missing`);
  for (const row of rows) {
    const match = sameVersion(row.version, release);
    const project = h === 'claude' && ['project', 'local'].includes(row.scope) ? displayProject(row.projectPath, home, platform) : undefined;
    out.row(h, [h, safeVersion(row.version), row.scope || '-', row.enabled === false ? 'disabled' : 'enabled'], !match, project);
    if (!row.version || !String(row.version).trim()) { out.line(h, 'warn', `${h}: version unreported`); failed = true; }
    else stale ||= !match;
  }
  if (h !== 'agy') {
    const markets = enrichMarketplaces(h, parseMarketplaces(h, await run(h, MLIST)), rows);
    const warnings = marketplaceWarnings(markets, h);
    for (const warning of warnings) { out.line(h, 'warn', `${h}: ${warning}`); failed = true; }
    if (warnings.length) manualRemedy(h, out, scope);
    if (['codex', 'copilot'].includes(h) && markets.some(m => marketplaceMatches(h, m))) {
      out.line(h, 'dim', `${h}: pin state unverifiable from native list`);
    }
    if (!markets.length) { out.line(h, 'warn', `${h}: nexaduo marketplace missing`); failed = true; }
    const source = await readCatalogSource(h, markets, rows);
    const sources = source !== null ? [source] : rows.map(r => r.source).filter(Boolean);
    if (!sources.length) {
      out.line(h, 'warn', `${h}: catalog pointer unavailable in native lists; cannot verify release source`);
      failed = true;
    } else if (!sources.every(s => sourceHasPointer(s, release.tag, h))) {
      // STALE only when the source matches the expected release source in every
      // field except ref; anything else (foreign repo/type/path) is FAILED.
      if (sources.every(s => s !== null && typeof s === 'object' && sourceHasPointer({ ...s, ref: release.tag }, release.tag, h))) {
        out.line(h, 'warn', `${h}: main-tracking with no matching release pointer; upgrade the marketplace catalog`);
        stale = true;
      } else {
        out.line(h, 'warn', `${h}: marketplace source does not match the expected release source`);
        failed = true;
      }
    }
  }
  if (failed) {
    const detail = rows.length ? 'drift' : `plugin missing; run \`npx @nexaduo/marc install\``;
    if (!rows.length) out.line(h, 'warn', `${h}: run \`npx @nexaduo/marc install\``);
    return { state: 'FAILED', detail };
  }
  if (stale) {
    out.line(h, 'warn', `${h}: out of date; run \`npx @nexaduo/marc update\``);
    return { state: 'STALE', detail: 'out of date; run `npx @nexaduo/marc update`' };
  }
  return { state: 'OK', detail: 'in sync' };
}

// ---- --force: re-register a mismatched nexaduo marketplace (#440) ----
// Every argv below is a constant: nothing from native output reaches a command.
const MLIST = ['plugin', 'marketplace', 'list', '--json'];
const REMOVE = ['plugin', 'marketplace', 'remove', 'nexaduo'];
const ADD = ['plugin', 'marketplace', 'add', SLUG];
const installArgs = (h, scope) => ['plugin', h === 'codex' ? 'add' : 'install', ID, ...(h === 'claude' ? scopeArgs(scope) : [])];
const settingsFile = scope => scope === 'local' ? 'settings.local.json' : 'settings.json';
const canon = v => Array.isArray(v) ? `[${v.map(canon).join(',')}]` : v && typeof v === 'object' ?
  `{${Object.keys(v).sort().map(k => `${JSON.stringify(k)}:${canon(v[k])}`).join(',')}}` : JSON.stringify(v);
const clip = s => s.length > 200 ? `${s.slice(0, 200)}${String.fromCharCode(0x2026)}` : s;

// Found-source text for plans: controls stripped, URL credentials redacted,
// ?query/#fragment dropped, local paths via displayProject, visibly truncated.
export function describeForPlan(row, home, platform) {
  const { type, value, hasSource } = sourceParts(row);
  if (!hasSource) {
    return typeof row.root === 'string' ? clip(`registered from local root ${displayProject(row.root, home, platform)}`) :
      'source: unknown (missing marketplaceSource)';
  }
  let v = stripControls(value ?? (typeof row.path === 'string' ? row.path : ''));
  if (looksLocal(v)) v = displayProject(v.replace(/^Local: /, ''), home, platform);
  else v = v.replace(/^([a-z][a-z0-9+.-]*:\/\/)[^/\s]*@/i, '$1***@').replace(/[?#].*$/, '');
  return [type != null && `sourceType: ${clip(stripControls(type))}`, `source: ${clip(v) || 'unknown'}`].filter(Boolean).join(', ');
}

async function reinstallTargets(h, opts, rows, cwd, home, platform) {
  const targets = new Map(), skipped = [];
  const add = (scope, dir, disabled) => {
    const key = `${scope}\0${dir ? path.resolve(dir) : ''}`;
    const t = targets.get(key) ?? { scope, cwd: dir, disabled: false };
    t.disabled ||= disabled; targets.set(key, t);
  };
  if (h !== 'claude') return { targets: [{ disabled: rows.some(r => r.enabled === false) }], skipped };
  for (const row of rows) {
    const scope = row.scope || 'user', disabled = row.enabled === false;
    if (!SCOPES.includes(scope)) { skipped.push(`skipped unknown scope ${stripControls(scope)}`); continue; }
    if (scope === 'user') { add('user', undefined, disabled); continue; }
    try {
      if (typeof row.projectPath !== 'string' || !path.isAbsolute(row.projectPath)) throw new Error('invalid project path');
      if (!(await stat(row.projectPath)).isDirectory()) throw new Error('project path is not a directory');
      add(scope, row.projectPath, disabled);
    } catch (e) { skipped.push(`skipped ${scope} scope at ${displayProject(row.projectPath, home, platform)}: ${e.message}`); }
  }
  if (opts.command === 'install' || opts.scope || !targets.size) {
    const scope = opts.scope || 'user';
    add(scope, scope === 'user' ? undefined : cwd, false);
  }
  return { targets: [...targets.values()], skipped };
}

// Read-only, before consent (also --dry-run and update). Decides per harness.
async function forcePreRead(h, { opts, run, cwd, home, platform, release }) {
  let rows, markets;
  try {
    rows = parsePlugins(h, await run(h, listArgs(h)));
    markets = parseMarketplaces(h, await run(h, MLIST));
  } catch (error) { return { action: 'error', error }; }
  if (!markets.length) return { action: 'none', rows };
  if (markets.length > 1) return { action: 'refuse', rows, message: `--force refused: ${markets.length} nexaduo registrations are listed; remove them manually` };
  const row = enrichMarketplaces(h, markets, rows)[0];
  const cls = classifyMarketplace(h, row), desc = describeForPlan(row, home, platform);
  const base = { rows, desc, cls, rawCanon: canon(markets) };
  if (cls.kind === 'match') {
    if (opts.command !== 'install') return { ...base, action: 'none', note: 'registration matches; nothing to remove' };
    const mine = installedRows(h, rows, opts.scope || 'user', cwd);
    if (!mine.length) return { ...base, action: 'none' };
    return versionState(mine, release).stale ? { ...base, action: 'update', note: 'registration matches; updating the stale install' } :
      { ...base, action: 'none', note: 'nothing to do' };
  }
  if (['local', 'unverifiable'].includes(cls.kind) && !opts.forceLocal) {
    return { ...base, action: 'refuse', message: `--force refused: ${cls.reason}; add --force-local to replace it (found ${desc}); registration preserved` };
  }
  return { ...base, action: 'replace', ...(await reinstallTargets(h, opts, rows, cwd, home, platform)) };
}

const updateArgs = (h, scope) => [['plugin', 'marketplace', h === 'codex' ? 'upgrade' : 'update', 'nexaduo'],
  h === 'claude' ? ['plugin', 'update', ID, ...scopeArgs(scope)] : ['plugin', h === 'codex' ? 'add' : 'update', h === 'codex' ? ID : 'marc']];

// Plan lines that replace a harness's static plan() lines.
function forceLines(h, f, opts, home, platform) {
  const cmd = text => ({ role: 'cmd', text });
  if (f.action === 'refuse') return [{ role: 'warn', text: `${h}: ${f.message}` }];
  if (f.action === 'update') return [...updateArgs(h, opts.scope || 'user').map(a => cmd(`${h} ${a.join(' ')}`)), cmd(`${h} ${listArgs(h).join(' ')}`)];
  const L = [{ role: 'warn', text: `${h}: --force will REMOVE marketplace nexaduo (found ${f.desc}; reason: ${f.cls.reason})` },
    cmd(`${h} ${REMOVE.join(' ')}`), cmd(`${h} ${ADD.join(' ')}`)];
  for (const t of f.targets) L.push(cmd(`${h} ${installArgs(h, t.scope).join(' ')}${t.cwd ? `   (cwd ${displayProject(t.cwd, home, platform)}, if missing after add)` : ''}`));
  for (const t of f.targets) if (t.disabled && h === 'claude') L.push(cmd(`${h} plugin disable ${ID} ${scopeArgs(t.scope).join(' ')}`));
  for (const t of f.targets) if (t.cwd) L.push({ role: 'warn', text: `Claude may edit ${displayProject(path.join(t.cwd, '.claude', settingsFile(t.scope)), home, platform)} (${t.scope} scope)` });
  if (h !== 'claude' && f.targets.some(t => t.disabled)) L.push({ role: 'warn', text: `${h}: marc was disabled; re-disable it after the reinstall` });
  for (const s of f.skipped) L.push({ role: 'warn', text: `${h}: warning: ${s}` });
  L.push(cmd(`${h} ${listArgs(h).join(' ')}`));
  return L;
}

// remove -> verify gone -> add -> reinstall missing scopes. Never rolls back:
// restoring would re-add the old, possibly hostile, source.
async function forceReplace({ h, f, run, out, ctx, report, opts, cwd }) {
  if (ctx.aborted) throw new Error('skipped: earlier forced re-registration failed; nothing removed');
  const listed = async () => parseMarketplaces(h, await run(h, MLIST));
  if (canon(await listed()) !== f.rawCanon) throw new Error('registration changed since the plan was shown; nothing removed');
  try { await run(h, REMOVE); } catch (e) { throw new Error(`marketplace remove failed (${e.message}); registration preserved`); }
  if ((await listed()).length) throw new Error('nexaduo is still listed after marketplace remove; not adding');
  let added = true;
  try { await run(h, ADD); } catch { added = false; }
  if (added && !(await listed()).length) added = false;
  if (!added) {
    ctx.aborted = true;
    out.line(h, 'warn', `${h}: previous registration was: ${f.desc}`);
    out.remedy(h, `${h}: nexaduo is now unregistered; retry (review first):`,
      [`${h} ${ADD.join(' ')}`, ...f.targets.map(t => `${h} ${installArgs(h, t.scope).join(' ')}`)]);
    throw new Error('nexaduo is now unregistered: marketplace add failed');
  }
  let rows = parsePlugins(h, await run(h, listArgs(h)));
  const present = t => h !== 'claude' ? rows.length > 0 : rows.some(r => (r.scope || 'user') === t.scope &&
    (t.scope === 'user' || (typeof r.projectPath === 'string' && samePath(r.projectPath, t.cwd))));
  const missing = f.targets.filter(t => !present(t));
  let failed = f.skipped.length > 0;
  for (const [i, t] of missing.entries()) {
    const options = t.cwd ? { cwd: t.cwd } : undefined;
    try {
      if (t.cwd && !(await stat(t.cwd)).isDirectory()) throw new Error('project path is not a directory');
    } catch (e) { out.line(h, 'warn', `${h}: warning: skipped ${t.scope} scope at ${displayProject(t.cwd)}: ${e.message}`); failed = true; continue; }
    try { await run(h, installArgs(h, t.scope), options); } catch (e) {
      out.remedy(h, `${h}: reinstall failed after re-registration; retry (review first):`,
        missing.slice(i).map(x => `${h} ${installArgs(h, x.scope).join(' ')}`));
      throw new Error(`reinstall of ${ID} failed (${e.message}); the marketplace is registered`);
    }
    if (t.disabled && h === 'claude') {
      try { await run(h, ['plugin', 'disable', ID, ...scopeArgs(t.scope)], options); } catch (e) {
        out.line(h, 'warn', `${h}: warning: could not re-disable ${t.scope} scope: ${e.message}`); failed = true;
      }
    }
  }
  rows = parsePlugins(h, await run(h, listArgs(h)));
  const after = opts.command === 'install' || opts.scope ? installedRows(h, rows, opts.scope || 'user', cwd) : rows;
  if (!after.length) throw new Error('marc is not listed after install');
  return { outcome: report(after), failed };
}

export async function main(argv, { release, run = createRunner(), log = console.log,
  isTTY = Boolean(process.stdin.isTTY), discover = h => resolveExecutable(h), confirm, cwd = process.cwd(), tmpRoot,
  env = process.env, platform = process.platform,
  home = (platform === 'win32' ? env.USERPROFILE || env.HOME : env.HOME || env.USERPROFILE) || os.homedir(), stdoutIsTTY = Boolean(process.stdout.isTTY),
  style = createStyle({ env, isTTY: stdoutIsTTY, platform }) } = {}) {
  const opts = parseArgs(argv);
  if (opts.help) { log(HELP); return 0; }
  if (tagToNpm(release.tag) !== release.npm) throw new Error('Invalid embedded release version');
  if (opts.version && opts.version !== release.tag) {
    const mapped = tagToNpm(opts.version);
    const rest = argv.filter((v, i) => v !== '--version' && argv[i - 1] !== '--version');
    makeOut(log, style).cmdLine(`Use npx @nexaduo/marc@${mapped} ${rest.join(' ')}`);
    return 1;
  }
  if (!opts.yes && !isTTY && !opts.dryRun && opts.command !== 'doctor') throw new Error('Non-TTY execution requires --yes');
  if (!opts.harnesses.length) opts.harnesses = HARNESSES.filter(h => { try { discover(h); return true; } catch { return false; } });
  if (!opts.harnesses.length) throw new Error('No supported harness found on PATH');
  if (opts.scope && !opts.harnesses.includes('claude')) throw new Error('--scope requires Claude');
  const out = makeOut(log, style);
  out.header(release, opts.command);
  if (opts.dryRun) out.note('dry run: no changes will be made');
  else if (style.enabled) log('');
  out.begin(opts.harnesses);
  const fstate = new Map(), ctx = { aborted: false };
  if (opts.force) for (const h of opts.harnesses.filter(x => x !== 'agy')) {
    fstate.set(h, await forcePreRead(h, { opts, run, cwd, home, platform, release }));
  }
  for (const p of plan(opts)) {
    const f = fstate.get(p.harness);
    if (opts.force && p.harness === 'agy') out.line('agy', 'info', 'agy: --force not applicable');
    if (f?.note) out.line(p.harness, 'info', `${p.harness}: --force: ${f.note}`);
    if (f?.action === 'error') out.line(p.harness, 'warn', `${p.harness}: --force: cannot read registration state: ${f.error.message}`);
    if (['replace', 'refuse', 'update'].includes(f?.action)) {
      for (const l of forceLines(p.harness, f, opts, home, platform)) out.line(p.harness, l.role, l.text);
    } else for (const args of p.commands) out.line(p.harness, 'cmd', `${p.harness} ${args.join(' ')}`);
  }
  const replacing = h => fstate.get(h)?.action === 'replace';
  let status = 0, claudeRows, claudeError;
  if (opts.command === 'update' && opts.harnesses.includes('claude') && !replacing('claude')) {
    try {
      const installed = parsePlugins('claude', await run('claude', listArgs('claude')));
      claudeRows = installed.filter(row => !opts.scope || row.scope === opts.scope);
      if (opts.scope && !claudeRows.length) {
        const scopes = [...new Set(installed.map(row => row.scope || 'unknown'))];
        throw new Error(`marc is not installed at scope ${opts.scope}; installed scopes: ${scopes.join(', ') || 'none'}`);
      }
      for (const row of claudeRows) if (row.scope !== 'user') {
        const target = row.projectPath || cwd;
        if (!path.isAbsolute(target)) throw new Error('Claude: invalid project path');
        out.line('claude', 'warn', `Claude will edit ${path.join(target, '.claude', row.scope === 'local' ? 'settings.local.json' : 'settings.json')}`);
      }
    } catch (e) { claudeError = e; status = 1; out.line('claude', 'error', `claude: ${e.message}`); }
  } else if (opts.harnesses.includes('claude') && opts.command !== 'doctor' && !replacing('claude')) {
    const scope = opts.scope || 'user';
    if (scope !== 'user') out.line('claude', 'warn', `Claude may edit ${path.join(cwd, '.claude', scope === 'local' ? 'settings.local.json' : 'settings.json')} (${scope} scope)`);
  }
  const preRead = new Map();
  if (opts.command === 'install') for (const h of opts.harnesses.filter(x => x !== 'agy' && !replacing(x))) {
    // Read-only: installed rows decide whether install has anything to do.
    try { preRead.set(h, { rows: installedRows(h, parsePlugins(h, await run(h, listArgs(h))), opts.scope || 'user', cwd) }); }
    catch (e) { preRead.set(h, { error: e }); }
  }
  out.flush();
  if (opts.dryRun) return status;
  if (!opts.yes && opts.command !== 'doctor') {
    let accepted;
    const removes = [...fstate.values()].filter(f => f.action === 'replace').length;
    const question = removes ? `Run this plan? It removes ${removes} marketplace registration(s)` : 'Run this plan?';
    if (confirm) accepted = await confirm(question);
    else {
      const rl = createInterface({ input: process.stdin, output: process.stdout });
      try { accepted = /^y(es)?$/i.test((await rl.question(`${question} ${style.paint('dim', '[y/N]')} `)).trim()); } finally { rl.close(); }
    }
    if (!accepted) return 1;
  }
  const results = [];
  for (const h of opts.harnesses) {
    let failed = false, outcome, threw = false, detail, notInstalled = false;
    const f = fstate.get(h);
    const report = rows => {
      if (rows.some(r => r.enabled === false)) out.line(h, 'warn', `${h}: marc is installed but disabled`);
      const { found, stale } = versionState(rows, release);
      if (stale) {
        out.line(h, 'warn', `${h}: already installed at ${found} (launcher release ${release.tag}); run \`npx @nexaduo/marc update\``);
        return 'STALE';
      }
      out.line(h, 'info', `${h}: already installed at ${found}`);
      return 'OK';
    };
    try {
      if (opts.command === 'doctor') {
        const d = await doctor(h, run, release, out, opts.scope, home, platform);
        outcome = d.state === 'FAILED' ? undefined : d.state; failed = d.state === 'FAILED'; detail = d.detail;
      } else if (h === 'agy' && ['install', 'update'].includes(opts.command)) {
        await installAgy({ run, release, tmpRoot });
      } else if (f?.action === 'error') {
        throw f.error;
      } else if (f?.action === 'refuse') {
        manualRemedy(h, out, opts.scope);
        throw new Error(f.message);
      } else if (f?.action === 'replace') {
        const r = await forceReplace({ h, f, run, out, ctx, report, opts, cwd });
        outcome = r.outcome; failed = r.failed;
      } else if (f?.action === 'update') {
        const scope = opts.scope || 'user';
        const [market, plugin] = updateArgs(h, scope);
        await run(h, market);
        await run(h, plugin, scope === 'user' || h !== 'claude' ? undefined : { cwd });
        const after = installedRows(h, parsePlugins(h, await run(h, listArgs(h))), scope, cwd);
        if (!after.length) throw new Error('marc is not listed after update');
        outcome = report(after);
      } else if (h === 'claude' && opts.command === 'update') {
        if (claudeError) throw claudeError;
        const scopes = [...new Set(claudeRows.map(p => p.scope))];
        if (!scopes.length) throw new Error('Claude: no installed mARC scopes; run install first');
        if (scopes.some(s => !SCOPES.includes(s))) throw new Error('Claude: unmodifiable/unknown scope; update it manually');
        const targets = new Map();
        for (const row of claudeRows) {
          const targetCwd = row.scope === 'user' ? cwd : row.projectPath || cwd;
          if (!path.isAbsolute(targetCwd)) throw new Error('Claude: invalid project path');
          targets.set(`${row.scope}\0${targetCwd}`, { scope: row.scope, cwd: targetCwd });
        }
        await run(h, ['plugin', 'marketplace', 'update', 'nexaduo']);
        for (const target of targets.values()) {
          try {
            if (!(await stat(target.cwd)).isDirectory()) throw new Error('project path is not a directory');
            await run(h, ['plugin', 'update', ID, ...scopeArgs(target.scope)], { cwd: target.cwd });
          } catch (e) {
            out.line('claude', 'warn', `claude: warning: skipped ${target.scope} scope at ${target.cwd}: ${e.message}`);
            failed = true;
          }
        }
      } else if (opts.command === 'install') {
        const pre = preRead.get(h);
        if (pre.error) throw pre.error;
        const scope = opts.scope || 'user';
        const markets = enrichMarketplaces(h, parseMarketplaces(h, await run(h, MLIST)), pre.rows);
        if (markets.length && !markets.every(m => marketplaceMatches(h, m))) {
          manualRemedy(h, out, opts.scope);
          if (!pre.rows.length) { notInstalled = true; out.line(h, 'info', `${h}: marc not installed`); }
          const found = markets.map(describeSource).join('; ');
          throw new Error(`nexaduo is registered from a different or unverified source (${found.startsWith('registered ') ? '' : 'found '}${found}); registration preserved`);
        }
        if (pre.rows.length) outcome = report(pre.rows);
        else {
          if (!markets.length) await run(h, ['plugin', 'marketplace', 'add', SLUG]);
          else out.line(h, 'dim', `${h}: nexaduo already registered from ${SLUG}; skipping add`);
          await run(h, ['plugin', h === 'codex' ? 'add' : 'install', ID, ...(h === 'claude' ? scopeArgs(scope) : [])]);
          const after = installedRows(h, parsePlugins(h, await run(h, listArgs(h))), scope, cwd);
          if (!after.length) throw new Error('marc is not listed after install');
          outcome = report(after);
        }
      } else {
        for (const args of plan({ ...opts, harnesses: [h] })[0].commands) await run(h, args);
      }
    } catch (e) { out.line(h, 'error', `${h}: ${e.message}`); failed = true; threw = true; }
    const state = failed ? 'FAILED' : outcome || 'OK';
    results.push({ harness: h, state, detail: notInstalled ? `not installed; ${out.detail(h)}` : opts.command === 'doctor' && !threw ? detail : out.detail(h) });
    if (state !== 'OK') status = 1;
  }
  out.results(results);
  return status;
}
