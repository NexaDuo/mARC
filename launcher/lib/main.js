import { mkdtemp, rm, lstat, stat, readdir, readFile } from 'node:fs/promises';
import os from 'node:os';
import { realpathSync, statSync } from 'node:fs';
import path from 'node:path';
import { createInterface } from 'node:readline/promises';
import { createRunner, resolveExecutable, safeValue } from './process.js';
import { tagToNpm } from './version.js';
import { createStyle, stripControls } from './style.js';
import { safeVersion, parsePlugins, describeSource, parseMarketplaces, marketplaceWarnings, marketplaceMatches, sourceHasPointer, readCatalogSource } from './lists.js';

export const HARNESSES = ['claude', 'copilot', 'codex', 'agy'];
const SCOPES = ['user', 'project', 'local'];
const SLUG = 'NexaDuo/mARC';
const ID = 'marc@nexaduo';
const HELP = `Usage: marc install|update|doctor|uninstall [--yes] [--dry-run]
  [--harness claude|copilot|codex|agy]... [--scope user|project|local]
  [--version <embedded-tag>]
No --harness: use every harness found on PATH. Node >=20 required.
install reads the native plugin list (read-only) before consent, also in --dry-run, and again after installing.
install never upgrades: an existing install that differs from this release is reported STALE (run update).
--scope applies to Claude only. Non-TTY install/update/uninstall requires --yes
(doctor and --dry-run never change anything and need no --yes).
The package release selects the tag; another tag prints its npx command.`;

export function parseArgs(argv) {
  const opts = { command: argv[0], harnesses: [], yes: false, dryRun: false };
  if (argv.length === 1 && argv[0] === '--help') return { help: true };
  if (!['install', 'update', 'doctor', 'uninstall'].includes(opts.command)) throw new Error(HELP);
  for (let i = 1; i < argv.length; i++) {
    const flag = argv[i];
    if (flag === '--yes') opts.yes = true;
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
  const h = typeof home === 'string' ? stripControls(home) : '';
  if (h && P.isAbsolute(clean)) {
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
    stale ||= !match;
  }
  if (h !== 'agy') {
    const markets = parseMarketplaces(h, await run(h, ['plugin', 'marketplace', 'list', '--json']));
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
      out.line(h, 'warn', `${h}: main-tracking with no matching release pointer; upgrade the marketplace catalog`);
      stale = true;
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
  for (const p of plan(opts)) {
    for (const args of p.commands) out.line(p.harness, 'cmd', `${p.harness} ${args.join(' ')}`);
  }
  let status = 0, claudeRows, claudeError;
  if (opts.command === 'update' && opts.harnesses.includes('claude')) {
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
  } else if (opts.harnesses.includes('claude') && opts.command !== 'doctor') {
    const scope = opts.scope || 'user';
    if (scope !== 'user') out.line('claude', 'warn', `Claude may edit ${path.join(cwd, '.claude', scope === 'local' ? 'settings.local.json' : 'settings.json')} (${scope} scope)`);
  }
  const preRead = new Map();
  if (opts.command === 'install') for (const h of opts.harnesses.filter(x => x !== 'agy')) {
    // Read-only: installed rows decide whether install has anything to do.
    try { preRead.set(h, { rows: installedRows(h, parsePlugins(h, await run(h, listArgs(h))), opts.scope || 'user', cwd) }); }
    catch (e) { preRead.set(h, { error: e }); }
  }
  out.flush();
  if (opts.dryRun) return status;
  if (!opts.yes && opts.command !== 'doctor') {
    let accepted;
    if (confirm) accepted = await confirm();
    else {
      const rl = createInterface({ input: process.stdin, output: process.stdout });
      try { accepted = /^y(es)?$/i.test((await rl.question(`Run this plan? ${style.paint('dim', '[y/N]')} `)).trim()); } finally { rl.close(); }
    }
    if (!accepted) return 1;
  }
  const results = [];
  for (const h of opts.harnesses) {
    let failed = false, outcome, threw = false, detail;
    try {
      if (opts.command === 'doctor') {
        const d = await doctor(h, run, release, out, opts.scope, home, platform);
        outcome = d.state === 'FAILED' ? undefined : d.state; failed = d.state === 'FAILED'; detail = d.detail;
      } else if (h === 'agy' && ['install', 'update'].includes(opts.command)) {
        await installAgy({ run, release, tmpRoot });
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
        const markets = parseMarketplaces(h, await run(h, ['plugin', 'marketplace', 'list', '--json']));
        if (markets.length && !markets.every(m => marketplaceMatches(h, m))) {
          manualRemedy(h, out, opts.scope);
          const found = markets.map(describeSource).join('; ');
          throw new Error(`nexaduo is registered from a different or unverified source (found ${found}); registration preserved`);
        }
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
    results.push({ harness: h, state, detail: opts.command === 'doctor' && !threw ? detail : out.detail(h) });
    if (state !== 'OK') status = 1;
  }
  out.results(results);
  return status;
}
