import { mkdtemp, rm, lstat, readdir, readFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { createInterface } from 'node:readline/promises';
import { createRunner, resolveExecutable, safeValue } from './process.js';
import { tagToNpm } from './version.js';
import { parsePlugins, parseMarketplaces, marketplaceWarnings, sourceHasPointer, readCatalogSource } from './lists.js';

export const HARNESSES = ['claude', 'copilot', 'codex', 'agy'];
const SCOPES = ['user', 'project', 'local'];
const SLUG = 'NexaDuo/mARC';
const ID = 'marc@nexaduo';
const HELP = `Usage: marc install|update|doctor|uninstall [--yes] [--dry-run]
  [--harness claude|copilot|codex|agy]... [--scope user|project|local]
  [--version <embedded-tag>]
No --harness: use every harness found on PATH. Node >=20 required.
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

// A plan has no side effects. Dry-run exits before list commands; an actual
// Claude update resolves project paths before interactive consent.
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
      commands.push(['plugin', 'marketplace', 'add', SLUG]);
      commands.push(['plugin', h === 'codex' ? 'add' : 'install', ID, ...(h === 'claude' ? scopeArgs(opts.scope || 'user') : [])]);
    } else if (opts.command === 'update') {
      if (h === 'claude') commands.push(listArgs(h));
      commands.push(['plugin', 'marketplace', h === 'codex' ? 'upgrade' : 'update', ...(h === 'codex' ? [] : ['nexaduo'])]);
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

export async function installAgy({ run, release, tmpRoot = os.tmpdir() }) {
  if (typeof release.sha !== 'string' || release.sha.length !== 40 || !/^[a-f0-9]{40}$/.test(release.sha)) throw new Error('Release SHA is absent; use a published launcher');
  const installed = parsePlugins('agy', await run('agy', listArgs('agy')));
  if (installed.length > 1 || (installed.length && typeof installed[0].enabled !== 'boolean')) throw new Error('Cannot determine Antigravity enabled state');
  const enable = !installed.length || installed[0].enabled;
  const temp = await mkdtemp(path.join(tmpRoot, 'marc-'));
  const clone = path.join(temp, 'release');
  // Git config and git's cwd are isolated before clone/checkout: no workspace
  // hooks, URL rewrites, templates or environment-selected repositories.
  const gitOptions = { cwd: temp, isolatedGit: true };
  const cliPath = p => process.platform === 'win32' ? p.replaceAll('\\', '/') : p;
  try {
    await run('git', ['clone', '--depth', '1', '--branch', release.tag, '--no-checkout',
      'https://github.com/NexaDuo/mARC.git', cliPath(clone)], gitOptions);
    const options = { ...gitOptions, cwd: clone };
    const sha = (await run('git', ['rev-parse', 'HEAD'], options)).trim();
    if (sha !== release.sha) throw new Error(`Release SHA mismatch: expected ${release.sha}, got ${sha}`);
    const tree = await run('git', ['ls-tree', '-r', 'HEAD'], options);
    if (tree.split('\n').some(line => /^(120000|160000) /.test(line))) throw new Error('Symlink or submodule in release');
    await run('git', ['checkout', '--detach', '--force', 'HEAD'], options);
    await checkTree(clone);
    const manifest = JSON.parse(await readFile(path.join(clone, 'harnesses/antigravity/marc/plugin.json'), 'utf8'));
    if (`v${manifest.version}` !== release.tag) throw new Error('Antigravity manifest/tag mismatch');
    await run('agy', ['plugin', 'install', cliPath(path.join(clone, 'harnesses/antigravity/marc'))]);
    if (enable) await run('agy', ['plugin', 'enable', 'marc']);
  } finally {
    await rm(temp, { recursive: true, force: true });
  }
}

async function doctor(h, run, release, log, scope) {
  const rows = parsePlugins(h, await run(h, listArgs(h))).filter(p => h !== 'claude' || !scope || p.scope === scope);
  let drift = !rows.length;
  if (!rows.length) log(`${h}\tmissing\t-`);
  for (const row of rows) {
    const match = `v${String(row.version).replace(/^v/, '')}` === release.tag;
    log(`${h}\t${row.version || 'unknown'}\t${row.scope || '-'}\t${row.enabled === false ? 'disabled' : 'enabled'}${match ? '' : '\tDRIFT'}`);
    drift ||= !match;
  }
  if (h !== 'agy') {
    const markets = parseMarketplaces(h, await run(h, ['plugin', 'marketplace', 'list', '--json']));
    for (const warning of marketplaceWarnings(markets)) { log(`${h}: ${warning}`); drift = true; }
    if (marketplaceWarnings(markets).length && h === 'codex') {
      log('Manual remedy (review first; this removes the marketplace registration):');
      log('codex plugin marketplace remove nexaduo');
      log('codex plugin marketplace add NexaDuo/mARC');
      log('codex plugin add marc@nexaduo');
    }
    if (!markets.length) { log(`${h}: nexaduo marketplace missing`); drift = true; }
    const source = await readCatalogSource(h, markets, rows);
    const sources = source !== null ? [source] : rows.map(r => r.source).filter(Boolean);
    if (!sources.length) {
      log(`${h}: catalog pointer unavailable in native lists; cannot verify release source`);
      drift = true;
    } else if (!sources.every(s => sourceHasPointer(s, release.tag))) {
      log(`${h}: main-tracking with no matching release pointer; upgrade the marketplace catalog`);
      drift = true;
    }
  }
  return drift ? 1 : 0;
}

export async function main(argv, { release, run = createRunner(), log = console.log,
  isTTY = Boolean(process.stdin.isTTY), discover = h => resolveExecutable(h), confirm, cwd = process.cwd(), tmpRoot } = {}) {
  const opts = parseArgs(argv);
  if (opts.help) { log(HELP); return 0; }
  if (tagToNpm(release.tag) !== release.npm) throw new Error('Invalid embedded release version');
  if (opts.version && opts.version !== release.tag) {
    const mapped = tagToNpm(opts.version);
    const rest = argv.filter((v, i) => v !== '--version' && argv[i - 1] !== '--version');
    log(`Use npx @nexaduo/marc@${mapped} ${rest.join(' ')}`);
    return 1;
  }
  if (!opts.yes && !isTTY && !opts.dryRun && opts.command !== 'doctor') throw new Error('Non-TTY execution requires --yes');
  if (!opts.harnesses.length) opts.harnesses = HARNESSES.filter(h => { try { discover(h); return true; } catch { return false; } });
  if (!opts.harnesses.length) throw new Error('No supported harness found on PATH');
  if (opts.scope && !opts.harnesses.includes('claude')) throw new Error('--scope requires Claude');
  log(`mARC ${release.tag} (${release.npm}); ${opts.command}`);
  for (const p of plan(opts)) {
    for (const args of p.commands) log(`${p.harness} ${args.join(' ')}`);
  }
  if (opts.harnesses.includes('claude') && opts.command !== 'doctor') {
    const scopes = opts.scope ? [opts.scope] : opts.command === 'update' ? ['project', 'local'] : ['user'];
    for (const s of scopes) if (s !== 'user') log(`Claude may edit ${path.join(cwd, '.claude', s === 'local' ? 'settings.local.json' : 'settings.json')} (${s} scope)`);
  }
  if (opts.dryRun) return 0;
  let claudeRows;
  if (opts.command === 'update' && opts.harnesses.includes('claude')) {
    claudeRows = parsePlugins('claude', await run('claude', listArgs('claude')));
    for (const row of claudeRows) if (row.scope !== 'user') {
      const target = row.projectPath || cwd;
      if (!path.isAbsolute(target)) throw new Error('Claude: invalid project path');
      log(`Claude will edit ${path.join(target, '.claude', row.scope === 'local' ? 'settings.local.json' : 'settings.json')}`);
    }
  }
  if (!opts.yes && opts.command !== 'doctor') {
    let accepted;
    if (confirm) accepted = await confirm();
    else {
      const rl = createInterface({ input: process.stdin, output: process.stdout });
      try { accepted = /^y(es)?$/i.test((await rl.question('Run this plan? [y/N] ')).trim()); } finally { rl.close(); }
    }
    if (!accepted) return 1;
  }
  let status = 0;
  for (const h of opts.harnesses) {
    if (opts.command === 'doctor') {
      try { status = Math.max(status, await doctor(h, run, release, log, opts.scope)); }
      catch (e) { log(`${h}: ${e.message}`); status = 1; }
    } else if (h === 'agy' && ['install', 'update'].includes(opts.command)) {
      await installAgy({ run, release, tmpRoot });
    } else if (h === 'claude' && opts.command === 'update') {
      const rows = claudeRows;
      const scopes = [...new Set(rows.map(p => p.scope))];
      if (!scopes.length) throw new Error('Claude: no installed mARC scopes; run install first');
      if (scopes.some(s => !SCOPES.includes(s))) throw new Error('Claude: unmodifiable/unknown scope; update it manually');
      if (opts.scope && !scopes.includes(opts.scope)) throw new Error('Claude: requested scope is not installed');
      // Scope flags apply to every installed scope, including entries belonging
      // to another project. Run from that project so no install is skipped.
      const targets = new Map();
      for (const row of rows) {
        if (opts.scope && row.scope !== opts.scope) continue;
        const targetCwd = row.scope === 'user' ? cwd : row.projectPath || cwd;
        if (!path.isAbsolute(targetCwd)) throw new Error('Claude: invalid project path');
        targets.set(`${row.scope}\0${targetCwd}`, { scope: row.scope, cwd: targetCwd });
      }
      await run(h, ['plugin', 'marketplace', 'update', 'nexaduo']);
      for (const target of targets.values()) await run(h, ['plugin', 'update', ID, ...scopeArgs(target.scope)], { cwd: target.cwd });
    } else {
      for (const args of plan({ ...opts, harnesses: [h] })[0].commands) await run(h, args);
    }
  }
  return status;
}
