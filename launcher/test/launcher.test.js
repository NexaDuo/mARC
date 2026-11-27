import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, writeFile, readdir, rm, symlink, chmod, lstat } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import os from 'node:os';
import { rmSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createStyle, stripControls } from '../lib/style.js';
import { main, displayProject, makeOut, parseArgs, installAgy, tempRoots, HARNESSES } from '../lib/main.js';
import { tagToNpm, npmToTag } from '../lib/version.js';
import { safeValue, spawnSpec, resolveExecutable, createRunner } from '../lib/process.js';
import { classifyMarketplace, enrichMarketplaces, describeSource, redactSource, safeVersion, parsePlugins, parseMarketplaces, marketplaceWarnings, marketplaceMatches, sourceHasPointer, readCatalogSource } from '../lib/lists.js';

const ROOT = fileURLToPath(new URL('../..', import.meta.url));
// The current release comes from the single VERSION source, never a literal.
const CUR_VERSION = readFileSync(new URL('../../VERSION', import.meta.url), 'utf8').trim();
const CUR_TAG = `v${CUR_VERSION}`;
const CUR_NPM = tagToNpm(CUR_TAG);
const release = { tag: 'v26.11.25.2', sha: 'a'.repeat(40), npm: '26.1125.2' };
const fixtures = {
  claude: JSON.stringify([{ id: 'marc@nexaduo', version: '26.11.25.2', scope: 'user', enabled: true, source: { source: 'git-subdir', url: 'NexaDuo/mARC', path: 'harnesses/claude-code/marc', ref: release.tag } },
    { id: 'marc@nexaduo', version: '26.11.25.2', scope: 'project', enabled: false, source: { source: 'git-subdir', url: 'NexaDuo/mARC', path: 'harnesses/claude-code/marc', ref: release.tag } }]),
  copilot: JSON.stringify([{ name: 'marc', marketplace: 'nexaduo', version: '26.11.25.2', enabled: true, source: { source: 'github', repo: 'NexaDuo/mARC', path: 'harnesses/copilot/marc', ref: release.tag } }]),
  codex: JSON.stringify({ installed: [{ pluginId: 'marc@nexaduo', version: '26.11.25.2', installed: true,
    enabled: true, source: { source: 'git-subdir', url: 'NexaDuo/mARC', path: 'harnesses/codex/marc', ref: release.tag } }] }),
  agy: JSON.stringify([{ name: 'marc', version: '26.11.25.2', enabled: true }]),
};
const marketFixtures = {
  // Operator captures, 2026-11-25: Claude 2.1.322, Copilot 1.0.89-1,
  // Codex 0.167.1. Copilot/Codex pinned and unpinned lists are identical.
  claude: [{ name: 'nexaduo', source: 'github', repo: 'NexaDuo/mARC', installLocation: '/home/<u>/.claude/plugins/marketplaces/nexaduo' }],
  copilot: [{ name: 'nexaduo', source: 'GitHub: NexaDuo/mARC', isDefault: false }],
  codex: { marketplaces: [{ name: 'nexaduo', root: '<CODEX_HOME>/.tmp/marketplaces/nexaduo',
    marketplaceSource: { sourceType: 'git', source: 'https://github.com/NexaDuo/mARC.git' } }] },
};
const marketRow = h => parseMarketplaces(h, JSON.stringify(marketFixtures[h]))[0];
const silent = () => {};
function runner(calls, overrides = {}) {
  return async (h, args, options) => {
    calls.push([h, args, options]);
    return args[1] === 'marketplace' && args[2] === 'list' ? JSON.stringify(marketFixtures[h]) :
      args[1] === 'list' ? overrides[h] ?? fixtures[h] : '';
  };
}
// Nothing is installed until the first plugin install/add for a harness; after
// that, `after[h]` (default: the matching fixture) is listed.
function freshRunner(calls, after = {}) {
  const done = new Set(), base = runner(calls, after);
  return async (h, args, options) => {
    if (args[1] === 'list' && !done.has(h)) {
      calls.push([h, args, options]);
      return h === 'codex' ? '{"installed":[]}' : '[]';
    }
    if (['install', 'add'].includes(args[1]) && args[2] === 'marc@nexaduo') done.add(h);
    return base(h, args, options);
  };
}
function invoke(args, calls, extra = {}) {
  return main(args, { release, run: runner(calls), log: silent, isTTY: false, env: {}, stdoutIsTTY: false, home: '/nonexistent/home', ...extra });
}
async function temp(t) {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'marc-test-'));
  t.after(() => rm(dir, { recursive: true, force: true }));
  return dir;
}

test('mapping: every real tag above floor round trips', () => {
  const result = spawnSync('git', ['tag', '--list'], { cwd: ROOT, encoding: 'utf8' });
  assert.equal(result.status, 0);
  const tags = result.stdout.trim().split('\n').filter(t => { try { tagToNpm(t); return true; } catch { return false; } });
  assert.ok(tags.includes('v26.11.25'));
  for (const tag of tags) assert.equal(npmToTag(tagToNpm(tag)), tag);
});
for (const [tag, npm] of [['v27.1.1', '27.101.0'], ['v27.12.31', '27.1231.0'], ['v27.9.30', '27.930.0'],
  ['v27.10.1', '27.1001.0'], ['v26.11.25.99', '26.1125.99']]) {
  test(`mapping edge ${tag}`, () => { assert.equal(tagToNpm(tag), npm); assert.equal(npmToTag(npm), tag); });
}
for (const tag of ['v0.9.0', 'v26.11.24', 'v26.01.1', 'v26.11.025', 'v26.11.25.2.01', 'v26.11.25.2.0', 'v27.2.29', 'v27.13.1', 'v26.11.25.2.1.2']) {
  test(`reject tag ${tag}`, () => assert.throws(() => tagToNpm(tag)));
}
for (const npm of ['26.1125.1.2', '26.01125.0', '26.1125.01', '26.1132.0', '26.1124.0']) {
  test(`reject npm ${npm}`, () => assert.throws(() => npmToTag(npm)));
}

const golden = {
  claude: {
    install: [['plugin', 'list', '--json'], ['plugin', 'marketplace', 'list', '--json']],
    update: [['plugin', 'list', '--json'], ['plugin', 'marketplace', 'list', '--json']],
    doctor: [['plugin', 'list', '--json'], ['plugin', 'marketplace', 'list', '--json']],
    uninstall: [['plugin', 'uninstall', 'marc@nexaduo', '--scope', 'user']],
  },
  copilot: {
    install: [['plugin', 'list', '--json'], ['plugin', 'marketplace', 'list', '--json']],
    update: [['plugin', 'list', '--json'], ['plugin', 'marketplace', 'list', '--json']],
    doctor: [['plugin', 'list', '--json'], ['plugin', 'marketplace', 'list', '--json']], uninstall: [['plugin', 'uninstall', 'marc@nexaduo']],
  },
  codex: {
    install: [['plugin', 'list', '--json'], ['plugin', 'marketplace', 'list', '--json']],
    update: [['plugin', 'list', '--json'], ['plugin', 'marketplace', 'list', '--json']],
    doctor: [['plugin', 'list', '--json'], ['plugin', 'marketplace', 'list', '--json']],
    uninstall: [['plugin', 'remove', 'marc@nexaduo']],
  },
  agy: { doctor: [['plugin', 'list', '--json']], uninstall: [['plugin', 'uninstall', 'marc']] },
};
for (const h of HARNESSES) for (const command of ['install', 'update', 'doctor', 'uninstall']) {
  test(`golden argv ${h} ${command}`, async t => {
    const calls = [];
    if (h === 'agy' && ['install', 'update'].includes(command)) {
      const dir = await temp(t);
      await invoke([command, '--harness', h, '--yes'], calls, { tmpRoot: dir, run: agyRunner(calls) });
      const argv = calls.map(c => c.slice(0, 2));
      const clonePath = calls.find(c => c[0] === 'git' && c[1][0] === 'clone')[1].at(-1);
      assert.deepEqual(argv, [
        ['agy', ['plugin', 'list', '--json']],
        ['git', ['clone', '--depth', '1', '--branch', release.tag, '--no-checkout', 'https://github.com/NexaDuo/mARC.git', clonePath]],
        ['git', ['rev-parse', 'HEAD']], ['git', ['ls-tree', '-r', 'HEAD']],
        ['git', ['checkout', '--detach', '--force', 'HEAD']],
        ['agy', ['plugin', 'install', `${clonePath}/harnesses/antigravity/marc`]], ['agy', ['plugin', 'enable', 'marc']],
      ]);
    } else {
      assert.equal(await invoke([command, '--harness', h, '--yes'], calls), 0);
      assert.deepEqual(calls.map(c => c[1]), golden[h][command]);
    }
    assert.ok(calls.every(([, a]) => !(a.includes('marketplace') && a.includes('remove'))));
  });
}
for (const scope of ['user', 'project', 'local']) for (const command of ['uninstall']) {
  test(`Claude scope ${scope} ${command}`, async () => {
    const calls = [], logs = [];
    const rows = JSON.stringify([{ id: 'marc@nexaduo', scope, version: '26.11.25.2' }]);
    assert.equal(await invoke([command, '--harness', 'claude', '--scope', scope, '--yes'], calls,
      { run: runner(calls, { claude: rows }), log: s => logs.push(s) }), 0);
    assert.deepEqual(calls.at(-1)[1].slice(-2), ['--scope', scope]);
    if (scope !== 'user') assert.ok(logs.some(s => s.includes(scope === 'local' ? 'settings.local.json' : 'settings.json')));
  });
}
for (const h of HARNESSES) for (const command of ['install', 'update', 'doctor', 'uninstall']) {
  test(`no processes: ${h} ${command} non-TTY and dry-run`, async () => {
    const calls = [];
    if (command !== 'doctor') await assert.rejects(invoke([command, '--harness', h], calls), /--yes/);
    assert.equal(await invoke([command, '--harness', h, '--yes', '--dry-run'], calls), 0);
    assert.equal(await invoke([command, '--harness', h, '--dry-run'], calls), 0);
    assert.deepEqual(calls.map(c => c[1]), h !== 'agy' && ['install', 'update'].includes(command) ?
      Array(2).fill([['plugin', 'list', '--json'], ['plugin', 'marketplace', 'list', '--json']]).flat() : []);
  });
}
for (const flag of ['--harness', '--scope', '--version']) for (const bad of ['&', '|', '%', '^', '"', '!', '\n', ' ', '-evil']) {
  test(`reject user value ${flag} ${JSON.stringify(bad)}`, () => {
    assert.throws(() => parseArgs(['install', '--yes', flag, bad]));
    assert.throws(() => safeValue(bad));
    assert.throws(() => spawnSpec('C:\\bin\\claude.cmd', [bad], { platform: 'win32', env: { SystemRoot: 'C:\\Windows' } }));
  });
}
test('doctor runs non-TTY without --yes and only reads', async () => {
  const calls = [];
  await invoke(['doctor', '--harness', 'codex'], calls, { run: runner(calls) });
  for (const [, args] of calls) assert.ok(!['add', 'install', 'update', 'upgrade', 'remove', 'uninstall', 'enable'].some(v => args.includes(v)), args.join(' '));
});
test('version override emits mapped npx command, never runs a CLI', async () => {
  const calls = [], log = [];
  assert.equal(await invoke(['install', '--version', 'v26.11.25.1'], calls, { log: s => log.push(s) }), 1);
  assert.match(log[0], /npx @nexaduo\/marc@26\.1125\.1 install/);
  assert.deepEqual(calls, []);
});
test('matching embedded version, repeated harnesses, discovery and invalid flags', async () => {
  const calls = [];
  assert.equal(await invoke(['install', '--harness', 'codex', '--harness', 'codex', '--yes', '--version', release.tag], calls), 0);
  assert.equal(calls.length, 2);
  calls.length = 0;
  assert.equal(await invoke(['update', '--yes'], calls, { discover: h => { if (h !== 'codex') throw Error(); } }), 0);
  assert.deepEqual(calls.map(c => c[1]), golden.codex.update);
  for (const args of [['install', '--scope', 'local', '--harness', 'codex'], ['install', '--source', 'evil'], ['install', '--scope', 'admin'], ['install', '--harness']]) assert.throws(() => parseArgs(args));
});
test('interactive refusal runs only the read-only plugin list', async () => {
  const calls = [];
  const run = runner(calls, { claude: JSON.stringify(stale.claude) });
  assert.equal(await invoke(['install', '--harness', 'claude'], calls, { run, isTTY: true, confirm: async () => false }), 1);
  // Only the read-only plugin and marketplace list pre-reads run before consent.
  assert.deepEqual(calls.map(c => c[1]), [['plugin', 'list', '--json'], ['plugin', 'marketplace', 'list', '--json']]);
});

function agyRunner(calls, { mismatch = false, fail = false, enabled = true, fresh = false, tree = '', link = false, cloneFail = false, checkoutFail = false, wrongManifest = false } = {}) {
  return async (h, args, options) => {
    calls.push([h, args, options]);
    if (h === 'agy' && args[1] === 'list') return fresh ? '[]' : JSON.stringify([{ name: 'marc', enabled, version: '26.11.25' }]);
    if (h === 'git' && args[0] === 'clone') {
      if (cloneFail) throw Error('clone failed');
      const root = args.at(-1);
      await mkdir(path.join(root, 'harnesses/antigravity/marc'), { recursive: true });
      await writeFile(path.join(root, 'harnesses/antigravity/marc/plugin.json'), JSON.stringify({ version: wrongManifest ? '26.11.25' : '26.11.25.2' }));
      if (link) await symlink(os.tmpdir(), path.join(root, 'harnesses/antigravity/marc/escape'), 'junction');
    }
    if (h === 'git' && args[0] === 'rev-parse') return mismatch ? 'b'.repeat(40) : release.sha;
    if (h === 'git' && args[0] === 'ls-tree') return tree;
    if (h === 'git' && args[0] === 'checkout' && checkoutFail) throw Error('checkout failed');
    if (h === 'agy' && args[1] === 'install' && fail) throw Error('install failed');
    return '';
  };
}
for (const [label, options] of [['success', {}], ['disabled', { enabled: false }], ['fresh', { fresh: true }],
  ['SHA mismatch', { mismatch: true }], ['install failure', { fail: true }], ['tracked symlink', { tree: '120000 blob 123\tlink' }],
  ['submodule', { tree: '160000 commit 123\tsub' }], ['filesystem symlink', { link: true }], ['clone failure', { cloneFail: true }], ['checkout failure', { checkoutFail: true }], ['wrong manifest', { wrongManifest: true }]]) {
  test(`Antigravity ${label} cleans up and preserves state`, async t => {
    const dir = await temp(t), calls = [];
    const action = installAgy({ run: agyRunner(calls, options), release, tmpRoot: dir });
    const failure = options.mismatch || options.fail || options.tree || options.link || options.cloneFail || options.checkoutFail || options.wrongManifest;
    if (failure) await assert.rejects(action); else await action;
    assert.deepEqual(await readdir(dir), []);
    if (options.mismatch || options.tree || options.link) assert.ok(!calls.some(([h, a]) => h === 'agy' && a[1] === 'install'));
    assert.equal(calls.some(([h, a]) => h === 'agy' && a[1] === 'enable'), !failure && options.enabled !== false);
  });
}
test('Antigravity missing SHA fails before commands or temp creation', async t => {
  const dir = await temp(t), calls = [];
  await assert.rejects(installAgy({ run: agyRunner(calls), release: { ...release, sha: null }, tmpRoot: dir }), /SHA/);
  assert.deepEqual(calls, []); assert.deepEqual(await readdir(dir), []);
});
for (const h of HARNESSES) {
  test(`doctor ${h} healthy, drift and malformed fixtures`, async () => {
    assert.ok(parsePlugins(h, fixtures[h]).length);
    for (const [output, code] of [[fixtures[h], 0], [fixtures[h].replaceAll('26.11.25.2', '26.11.25'), 1], ['garbage', 1]]) {
      assert.equal(await invoke(['doctor', '--harness', h, '--yes'], [], { run: runner([], { [h]: output }) }), code);
    }
  });
}
test('doctor legacy marketplace and main-tracking catalog fail without mutation', async () => {
  assert.equal(parseMarketplaces('codex', JSON.stringify({ marketplaces: [{ name: 'nexaduo' }] })).length, 1);
  assert.equal(marketplaceWarnings([{ source: 'NexaDuo/mARC#v26.11.25' }]).length, 1);
  assert.equal(marketplaceWarnings([{ source: { ref: 'v26.11.25' } }]).length, 1);
  const calls = [], logs = [];
  const run = async (h, a) => { calls.push(a); return a[1] === 'marketplace' ? JSON.stringify([{ name: 'nexaduo', source: { ref: 'v26.11.25' } }]) : fixtures.codex.replace(release.tag, 'main'); };
  assert.equal(await invoke(['doctor', '--harness', 'codex', '--yes'], [], { run, log: s => logs.push(s) }), 1);
  assert.ok(logs.some(s => s.includes('legacy-pinned'))); assert.ok(logs.some(s => s.includes('main-tracking')));
  assert.ok(calls.every(a => a.includes('list')));
});

test('Windows cmd.exe construction bypasses COMSPEC and constrains PATHEXT', () => {
  const spec = spawnSpec('C:\\Program Files\\bin\\claude.cmd', ['plugin', 'update', 'marc@nexaduo', '--scope', 'user'],
    { platform: 'win32', env: { SystemRoot: 'C:\\Windows', COMSPEC: 'evil.cmd' } });
  assert.equal(spec.file, 'C:\\Windows\\System32\\cmd.exe');
  assert.deepEqual(spec.args.slice(0, 3), ['/d', '/s', '/c']);
  assert.equal(spec.options.windowsVerbatimArguments, true);
  assert.throws(() => spawnSpec('C:\\evil%PATH%\\cli.cmd', [], { platform: 'win32' }));
});
test('PATH resolution excludes cwd, relative entries and symlinks into cwd', async t => {
  const dir = await temp(t), cwd = path.join(dir, 'cwd'), bin = path.join(dir, 'bin');
  await mkdir(cwd); await mkdir(bin);
  const name = process.platform === 'win32' ? 'cli.cmd' : 'cli';
  await writeFile(path.join(cwd, name), 'decoy'); await chmod(path.join(cwd, name), 0o755);
  const env = { PATH: [cwd, '.', bin].join(path.delimiter), PATHEXT: '.EVIL;.CMD' };
  assert.throws(() => resolveExecutable('cli', { env, cwd }));
  if (process.platform === 'win32') {
    const alias = path.join(dir, 'alias');
    await symlink(cwd, alias, 'junction');
    assert.throws(() => resolveExecutable('cli', { env: { ...env, PATH: alias }, cwd }));
  } else {
    await symlink(path.join(cwd, name), path.join(bin, name));
    assert.throws(() => resolveExecutable('cli', { env, cwd }));
    await rm(path.join(bin, name));
  }
  await writeFile(path.join(bin, name), 'real'); await chmod(path.join(bin, name), 0o755);
  assert.equal(resolveExecutable('cli', { env, cwd }), await import('node:fs').then(fs => fs.realpathSync(path.join(bin, name))));
  // A home directory as cwd must not exclude its normal .local/bin-like children.
  assert.equal(resolveExecutable('cli', { env: { ...env, PATH: bin }, cwd: dir }), await import('node:fs').then(fs => fs.realpathSync(path.join(bin, name))));
});

test('PATH stubs record exact argv for all native flows; HOME stays unchanged', async t => {
  const dir = await temp(t), bin = path.join(dir, 'bin'), home = path.join(dir, 'home'), cwd = path.join(dir, 'cwd');
  await mkdir(bin); await mkdir(home); await mkdir(cwd);
  const logPath = path.join(dir, 'argv.jsonl');
  const impl = path.join(bin, 'stub.cjs');
  await writeFile(impl, `const fs = require('node:fs');\nconst [h,...a]=process.argv.slice(2);\nfs.appendFileSync(process.env.STUB_LOG,JSON.stringify([h,a])+'\\n');\nif(h==='git'&&a[0]==='clone'){const p=require('node:path').join(a.at(-1),'harnesses/antigravity/marc');fs.mkdirSync(p,{recursive:true});fs.writeFileSync(require('node:path').join(p,'plugin.json'),JSON.stringify({version:'26.11.25.2'}));}\nif(h==='git'&&a[0]==='rev-parse'){console.log('${release.sha}');process.exit(0);}\nconst f=JSON.parse(process.env.STUB_FIXTURES);\nconsole.log(a[1]==='marketplace'&&a[2]==='list'?JSON.stringify(JSON.parse(process.env.STUB_MARKETS)[h]):a[1]==='list'?f[h]:'');\n`);
  for (const h of [...HARNESSES, 'git']) {
    const win = process.platform === 'win32';
    const file = path.join(bin, h + (win ? '.cmd' : ''));
    await writeFile(file, win ? `@echo off\r\n"${process.execPath}" "${impl}" ${h} %*\r\n` : `#!${process.execPath}\nimport('${new URL(`file://${impl}`).href}');\n`);
    if (!win) {
      // POSIX shell builtins keep the stub independent of a second Node process.
      await writeFile(file, `#!/bin/sh
printf '["${h}",[' >> "$STUB_LOG"
sep=''
for arg do printf '%s"%s"' "$sep" "$arg" >> "$STUB_LOG"; sep=','; done
printf ']]\\n' >> "$STUB_LOG"
if [ '${h}' = git ]; then
  case "$1" in
    clone) for dest do :; done; /bin/mkdir -p "$dest/harnesses/antigravity/marc"; printf '%s' '{"version":"26.11.25.2"}' > "$dest/harnesses/antigravity/marc/plugin.json" ;;
    rev-parse) printf '%s' '${release.sha}' ;;
  esac
  exit 0
fi
case "$1 $2 $3" in
  'plugin marketplace list') printf '%s\\n' '${JSON.stringify(marketFixtures[h])}' ;;
  'plugin list'*) printf '%s\\n' '${fixtures[h]}' ;;
esac
`);
    }
    await chmod(file, 0o755);
  }
  const env = { ...process.env, PATH: bin, PATHEXT: '.CMD', HOME: home, USERPROFILE: home, STUB_LOG: logPath,
    STUB_FIXTURES: JSON.stringify(fixtures), STUB_MARKETS: JSON.stringify(marketFixtures) };
  const run = createRunner({ env, cwd });
  const snapshot = async () => Promise.all((await readdir(home, { recursive: true })).sort().map(async f => {
    const p = path.join(home, f);
    try { return [f, await readFile(p, 'hex')]; } catch { return [f, '<directory>']; }
  }));
  await writeFile(path.join(home, 'settings.json'), '{"unchanged":true}');
  const before = await snapshot();
  const expected = [];
  for (const h of HARNESSES) for (const cmd of Object.keys(golden[h])) {
    const calls = [];
    assert.equal(await main([cmd, '--harness', h, '--harness', h, '--yes', '--version', release.tag], { release, run: async (...a) => { calls.push(a); return run(...a); }, log: silent, cwd, env: {}, stdoutIsTTY: false, home }), 0);
    expected.push(...golden[h][cmd].map(a => [h, a]));
  }
  for (const cmd of ['install', 'update']) {
    const calls = [];
    assert.equal(await main([cmd, '--harness', 'agy', '--yes'], { release, run: async (...a) => { calls.push(a); return run(...a); }, log: silent, cwd, tmpRoot: dir, env: {}, stdoutIsTTY: false }), 0);
    const clone = calls[1][1].at(-1);
    expected.push(['agy', ['plugin', 'list', '--json']], ['git', ['clone', '--depth', '1', '--branch', release.tag, '--no-checkout', 'https://github.com/NexaDuo/mARC.git', clone]],
      ['git', ['rev-parse', 'HEAD']], ['git', ['ls-tree', '-r', 'HEAD']], ['git', ['checkout', '--detach', '--force', 'HEAD']],
      ['agy', ['plugin', 'install', `${clone}/harnesses/antigravity/marc`]], ['agy', ['plugin', 'enable', 'marc']]);
  }
  const actual = (await readFile(logPath, 'utf8')).trim().split('\n').map(JSON.parse);
  assert.deepEqual(actual, expected);
  assert.deepEqual(await snapshot(), before);
});

test('package contract and exact npm pack file list', async () => {
  const pkg = JSON.parse(await readFile(path.join(ROOT, 'launcher/package.json'), 'utf8'));
  assert.equal(pkg.dependencies, undefined); assert.equal(pkg.devDependencies, undefined); assert.equal(pkg.scripts, undefined);
  assert.equal(pkg.engines.node, '>=20'); assert.equal(pkg.repository.url, 'git+https://github.com/NexaDuo/mARC.git');
  const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm';
  // npm.cmd itself requires the same batch wrapper on Windows.
  // A sandbox which cannot spawn Node may supply the output of the identical
  // npm command run by its tool host. CI runs npm directly with no fixture.
  const output = process.env.MARC_TEST_NPM_PACK_JSON ? await readFile(process.env.MARC_TEST_NPM_PACK_JSON, 'utf8') :
    await createRunner({ cwd: os.tmpdir(), env: { ...process.env, npm_config_cache: path.join(os.tmpdir(), 'marc-npm-cache') } })(npm.replace('.cmd', ''), ['pack', '--dry-run', '--json'], { cwd: path.join(ROOT, 'launcher') });
  const packed = JSON.parse(output);
  const files = Object.values(packed)[0].files.map(f => f.path).sort();
  const lib = (await readdir(path.join(ROOT, 'launcher/lib'))).map(f => `lib/${f}`);
  assert.deepEqual(files, ['LICENSE', 'README.md', 'package.json', 'release.json', 'bin/marc.js', ...lib].sort());
});
test('launcher source never requests a shell; the only marketplace remove is one constant argv', async () => {
  for (const f of await readdir(path.join(ROOT, 'launcher/lib'))) {
    const source = await readFile(path.join(ROOT, 'launcher/lib', f), 'utf8');
    assert.doesNotMatch(source, /shell\s*:\s*(?:true|['"])/);
    // --force (#440): the only remove argv is one constant, in main.js.
    const removes = source.match(/['"]marketplace['"]\s*,\s*['"]remove['"]/g) ?? [];
    assert.equal(removes.length, f === 'main.js' ? 1 : 0, f);
  }
  assert.match(await readFile(path.join(ROOT, 'launcher/lib/main.js'), 'utf8'), /const REMOVE = \['plugin', 'marketplace', 'remove', 'nexaduo'\];/);
});

test('observed native JSON list fixtures, text fallback and cached-catalog pointer reads', async t => {
  const dir = await temp(t);
  const observed = {
    claude: [{ id: 'marc@nexaduo', version: '26.11.25.2', scope: 'user', enabled: true, readFromFolder: dir }],
    codex: { installed: [{ pluginId: 'marc@nexaduo', name: 'marc', marketplaceName: 'nexaduo', version: '26.11.25.2',
      installed: true, enabled: true, source: { source: 'local', path: dir } }] },
    copilot: [{ name: 'marc', marketplace: 'nexaduo', version: '26.11.25.2', enabled: true, source: 'live', installedFrom: dir }],
    agy: [{ name: 'marc', version: '26.11.25.2', enabled: false, source: 'antigravity', path: dir }],
  };
  for (const h of HARNESSES) assert.equal(parsePlugins(h, JSON.stringify(observed[h]))[0].version, '26.11.25.2');
  assert.equal(parsePlugins('copilot', 'Live Plugins (loaded from a local marketplace directory, never copied):\n  • marc@nexaduo (v26.11.25.2) (enabled)\n      from /tmp/catalog')[0].version, '26.11.25.2');
  for (const [h, location] of [['claude', '.claude-plugin'], ['copilot', '.github/plugin'], ['codex', '.agents/plugins']]) {
    await mkdir(path.join(dir, location), { recursive: true });
    const catalog = path.join(dir, location, 'marketplace.json');
    const source = { source: h === 'copilot' ? 'github' : 'git-subdir', [h === 'copilot' ? 'repo' : 'url']: 'NexaDuo/mARC', path: `harnesses/${h === 'claude' ? 'claude-code' : h}/marc`, ref: release.tag };
    await writeFile(catalog, JSON.stringify({ name: 'nexaduo', plugins: [{ name: 'marc', source }] }));
    const nativeMarkets = [{ ...marketRow(h), root: dir }];
    const run = async (name, a) => a[1] === 'marketplace' ? JSON.stringify(nativeMarkets) : JSON.stringify(observed[h]);
    assert.equal(await invoke(['doctor', '--harness', h, '--yes'], [], { run }), 0);
    assert.deepEqual(await readCatalogSource(h, nativeMarkets, []), source);
    await writeFile(catalog, JSON.stringify({ name: 'nexaduo', plugins: [{ name: 'marc', source: './harnesses/example/marc' }] }));
    assert.equal(await invoke(['doctor', '--harness', h, '--yes'], [], { run }), 1);
  }
});

test('Git runner discards repo-selecting env and unsafe PATH entries', async t => {
  if (process.platform === 'win32') return; // batch argv is covered by Windows stubs.
  const dir = await temp(t), cwd = path.join(dir, 'cwd'), bin = path.join(dir, 'bin');
  await mkdir(cwd); await mkdir(bin);
  const file = path.join(bin, 'git');
  await writeFile(file, '#!/bin/sh\nprintf "%s|%s|%s|%s" "$GIT_DIR" "$GIT_CONFIG_COUNT" "$GIT_CONFIG_VALUE_0" "$PATH"\n');
  await chmod(file, 0o755);
  const run = createRunner({ cwd, env: { PATH: [cwd, '.', bin].join(path.delimiter), GIT_DIR: '/evil', GIT_CONFIG_COUNT: '99' } });
  const output = await run('git', ['rev-parse', 'HEAD'], { isolatedGit: true });
  assert.equal(output, `|1|/dev/null|${bin}`);
});

test('Copilot GitHub catalog uses its native cache without fetching or writing', async t => {
  const dir = await temp(t), root = path.join(dir, '.cache/copilot/marketplaces/NexaDuo-mARC');
  await mkdir(path.join(root, '.github/plugin'), { recursive: true });
  const source = { source: 'github', repo: 'NexaDuo/mARC', ref: release.tag, path: 'harnesses/copilot/marc' };
  await writeFile(path.join(root, '.github/plugin/marketplace.json'), JSON.stringify({ name: 'nexaduo', plugins: [{ name: 'marc', source }] }));
  const actual = await readCatalogSource('copilot', [{ name: 'nexaduo', source: 'GitHub: NexaDuo/mARC' }], [], { COPILOT_HOME: dir });
  assert.deepEqual(actual, source);
});

test('doctor Claude scope filters its table and all-harness scope remains Claude-only', async () => {
  const calls = [], logs = [];
  assert.equal(await invoke(['doctor', '--harness', 'claude', '--scope', 'project', '--yes'], calls, { log: s => logs.push(s) }), 0);
  assert.ok(logs.some(s => s.startsWith('claude\t26.11.25.2\tproject')));
  assert.ok(!logs.some(s => s.startsWith('claude\t26.11.25.2\tuser')));
  const all = [], cwd = path.resolve('/work/proj');
  const local = JSON.stringify([{ id: 'marc@nexaduo', version: '26.11.25.2', scope: 'local', projectPath: cwd }]);
  assert.equal(await invoke(['install', '--harness', 'codex', '--harness', 'claude', '--scope', 'local', '--yes'], all,
    { cwd, run: freshRunner(all, { claude: local }) }), 0);
  const argv = all.map(c => c[1]);
  assert.ok(argv.some(a => a.join(' ') === 'plugin add marc@nexaduo'));
  assert.ok(argv.some(a => a.join(' ') === 'plugin install marc@nexaduo --scope local'));
});

test('allowlist rejects embedded and trailing metacharacters, including JS end-anchor newline edge', () => {
  for (const char of ['&', '|', '%', '^', '"', '!', '\n', '\r', ' ']) for (const value of [`x${char}`, `${char}x`, `x${char}x`]) {
    assert.throws(() => safeValue(value));
    assert.throws(() => spawnSpec('C:\\bin\\cli.cmd', [value], { platform: 'win32', env: { SystemRoot: 'C:\\Windows' } }));
    for (const flag of ['--harness', '--scope', '--version']) assert.throws(() => parseArgs(['install', flag, value]));
  }
  for (const tag of ['v26.11.25.2\n', 'v26.11.25.2\r\n']) assert.throws(() => tagToNpm(tag));
});

for (const h of ['claude', 'copilot', 'codex']) {
  test(`captured ${h} registration matches and doctor has no false source drift`, async () => {
    assert.equal(marketplaceMatches(h, marketRow(h)), true);
    assert.deepEqual(marketplaceWarnings([marketRow(h)], h), []);
    const calls = [], logs = [];
    assert.equal(await invoke(['doctor', '--harness', h], calls, { log: s => logs.push(s) }), 0);
    assert.ok(!logs.some(s => /source drift|legacy-pinned|manual remedy/i.test(s)));
    assert.equal(logs.includes(`${h}: pin state unverifiable from native list`), h !== 'claude');
    assert.ok(calls.every(([, args]) => args.includes('list')));
  });
  test(`${h} rejects marketplace lookalikes without changing registration`, async () => {
    for (const repo of ['nexaduo/mARC', 'NexaDuo/marc', 'NexaDuo/mARC/', 'git@github.com:NexaDuo/mARC.git',
      'ssh://git@github.com/NexaDuo/mARC.git', 'https://github.com/NexaDuo/mARC.git/', 'NexaDuo/other', 'NexaDuo/mARC\n']) {
      const market = structuredClone(marketRow(h));
      if (h === 'claude') market.repo = repo;
      else if (h === 'copilot') market.source = `GitHub: ${repo}`;
      else market.marketplaceSource.source = /^(?:git@|ssh:|https:)/.test(repo) ? repo : `https://github.com/${repo}.git`;
      assert.equal(marketplaceMatches(h, market), false, repo);
      const calls = [], logs = [];
      const run = async (name, args) => { calls.push(args); return JSON.stringify([market]); };
      assert.equal(await invoke(['install', '--harness', h, '--yes'], [], { run, log: s => logs.push(s) }), 1);
      assert.deepEqual(calls, [['plugin', 'list', '--json'], ['plugin', 'marketplace', 'list', '--json']]);
      assert.ok(logs.includes(`${h} plugin marketplace remove nexaduo`));
    }
  });
  test(`install ${h} adds absent registration, skips expected existing registration`, async () => {
    const calls = [], base = freshRunner(calls);
    const run = async (name, args, options) => args[1] === 'marketplace' && args[2] === 'list' ?
      (calls.push([name, args, options]), '[]') : base(name, args, options);
    assert.equal(await invoke(['install', '--harness', h, '--yes'], [], { run }), 0);
    // Pre-read, then the same two lists again right before the first write (#451).
    assert.deepEqual(calls.map(c => c[1]), [['plugin', 'list', '--json'], ['plugin', 'marketplace', 'list', '--json'], ['plugin', 'list', '--json'], ['plugin', 'marketplace', 'list', '--json'],
      ['plugin', 'marketplace', 'add', 'NexaDuo/mARC'], h === 'codex' ? ['plugin', 'add', 'marc@nexaduo'] :
        h === 'claude' ? ['plugin', 'install', 'marc@nexaduo', '--scope', 'user'] : ['plugin', 'install', 'marc@nexaduo'], ['plugin', 'list', '--json']]);
    const existing = [], logs = [];
    assert.equal(await invoke(['install', '--harness', h, '--yes'], existing, { log: s => logs.push(s) }), 0);
    assert.deepEqual(existing.map(c => c[1]), golden[h].install);
    assert.ok(logs.includes(`${h}: already installed at 26.11.25.2`)); assert.ok(logs.includes(`${h}: OK`));
    const registered = [];
    assert.equal(await invoke(['install', '--harness', h, '--yes'], registered, { run: freshRunner(registered) }), 0);
    assert.ok(registered.some(c => c[1][1] === 'install' || c[1][1] === 'add'));
  });
  test(`install ${h} preserves conflicting sources, continues and reports every harness`, async () => {
    for (const conflict of [
      { source: 'Local: /legacy' }, { source: { source: 'github', repo: 'someone/mARC' } },
      { source: { source: 'github', repo: 'NexaDuo/mARC', ref: release.tag } },
      { marketplaceSource: { sourceType: 'local', source: '/legacy' } },
      { marketplaceSource: { sourceType: 'git', source: 'https://github.com/NexaDuo/mARC.git', ref: release.tag } },
    ]) {
      const calls = [], logs = [], next = h === 'copilot' ? 'codex' : 'copilot';
      const otherRun = freshRunner(calls);
      const run = async (name, args, options) => {
        if (name === h) { calls.push([name, args, options]); return JSON.stringify([{ name: 'nexaduo', ...conflict }]); }
        return otherRun(name, args, options);
      };
      assert.equal(await invoke(['install', '--harness', h, '--harness', next, '--yes'], [], { run, log: s => logs.push(s) }), 1);
      assert.equal(calls.filter(c => c[0] === h).length, 2);
      assert.ok(logs.some(s => s.includes('registration preserved; run npx @nexaduo/marc install --force') && /sourceType: |source: /.test(s)));
      assert.ok(calls.some(([name, args]) => name === next && args[1] === (next === 'codex' ? 'add' : 'install')));
      assert.ok(logs.includes(`${h} plugin marketplace remove nexaduo`));
      assert.ok(logs.includes(`${h} plugin marketplace add NexaDuo/mARC`));
      assert.ok(logs.includes(`${h}: FAILED`)); assert.ok(logs.includes(`${next}: OK`));
      assert.ok(!calls.some(([, args]) => args.includes('remove')));
    }
  });
}

test('Codex captured legacy local source is preserved with a manual remedy', async () => {
  const market = { name: 'nexaduo', root: '/path/to/clone', marketplaceSource: { sourceType: 'local', source: '/path/to/clone' } };
  assert.equal(marketplaceMatches('codex', market), false);
  for (const command of ['install', 'doctor']) {
    const calls = [], logs = [];
    const run = async (h, args) => {
      calls.push(args);
      return args[1] === 'marketplace' ? JSON.stringify({ marketplaces: [market] }) : fixtures.codex;
    };
    assert.equal(await invoke([command, '--harness', 'codex', '--yes'], [], { run, log: s => logs.push(s) }), 1);
    assert.ok(calls.every(args => args.includes('list')));
    assert.ok(logs.includes('codex plugin marketplace remove nexaduo'));
    assert.ok(logs.some(s => /different or unverified source|source drift/.test(s)));
    if (command === 'install') assert.ok(logs.some(s => s.includes('(sourceType: local, source: /path/to/clone)')), logs.join('\n'));
    assert.ok(!logs.some(s => s.includes('pin state unverifiable')));
  }
});

test('Claude flat ref and repo fragment pins are legacy conflicts', async () => {
  for (const extra of [{ ref: 'v26.11.25' }, { repo: 'NexaDuo/mARC#v26.11.25' }, { ref: 'main' }]) {
    const market = { ...marketRow('claude'), ...extra };
    assert.equal(marketplaceMatches('claude', market), false);
    assert.ok(marketplaceWarnings([market], 'claude').some(s => s.includes('legacy-pinned')));
    for (const command of ['install', 'doctor']) {
      const calls = [], logs = [];
      const run = async (h, args) => {
        calls.push(args);
        return args[1] === 'marketplace' ? JSON.stringify([market]) : fixtures.claude;
      };
      assert.equal(await invoke([command, '--harness', 'claude', '--yes'], [], { run, log: s => logs.push(s) }), 1);
      assert.ok(calls.every(args => args.includes('list')));
      assert.ok(logs.includes('claude plugin marketplace remove nexaduo'));
    }
  }
});

test('Copilot default marketplace does not interfere with captured nexaduo source', () => {
  const markets = parseMarketplaces('copilot', JSON.stringify([
    { name: 'copilot-plugins', source: 'GitHub: github/copilot-plugins', isDefault: true }, ...marketFixtures.copilot,
  ]));
  assert.deepEqual(markets, marketFixtures.copilot);
});

test('failed list or native command never aborts remaining harnesses', async () => {
  for (const command of ['install', 'update', 'doctor', 'uninstall']) {
    const calls = [], logs = [], native = runner(calls);
    const run = (h, args, options) => { if (h === 'claude') throw Error('fixture failure'); return native(h, args, options); };
    assert.equal(await invoke([command, '--harness', 'claude', '--harness', 'codex', '--yes'], [], { run, log: s => logs.push(s) }), 1);
    assert.ok(calls.some(([h]) => h === 'codex')); assert.ok(logs.includes('claude: FAILED')); assert.ok(logs.includes('codex: OK'));
  }
});

for (const h of ['claude', 'copilot', 'codex']) test(`doctor ${h} rejects matching tag from wrong repo, path, or registration`, async () => {
  const source = JSON.parse(fixtures[h])[h === 'codex' ? 'installed' : '0'];
  const good = (h === 'codex' ? source[0] : source).source;
  assert.ok(sourceHasPointer(good, release.tag, h));
  for (const bad of [{ ...good, [h === 'copilot' ? 'repo' : 'url']: 'fork/mARC' }, { ...good, path: 'other/plugin' }, `NexaDuo/mARC#${release.tag}`]) {
    assert.equal(sourceHasPointer(bad, release.tag, h), false);
  }
  const logs = [];
  const run = async (name, args) => args[1] === 'marketplace' ? JSON.stringify([{ name: 'nexaduo', source: 'Local: /legacy' }]) : fixtures[h];
  assert.equal(await invoke(['doctor', '--harness', h], [], { run, log: s => logs.push(s) }), 1);
  assert.ok(logs.some(s => s.includes('source drift')));
});

test('native Windows temp root expands RUNNER~1 and prefers a root without spaces', () => {
  const short = 'C:\\Users\\RUNNER~1\\Temp';
  const home = 'C:\\Users\\runneradmin', root = `${home}\\Temp`;
  const realpath = p => p === short ? root : p;
  assert.deepEqual(tempRoots(short, { platform: 'win32', home, realpath }), [root]);
  assert.deepEqual(tempRoots(undefined, { platform: 'win32', home, env: { TMP: short, TEMP: `${home}\\with space` }, realpath }), [root]);
});

test('Windows temp selection fails closed instead of falling back to shared system temp', () => {
  const home = 'C:\\Users\\with space', shared = 'C:\\Windows\\Temp';
  const options = { platform: 'win32', home, env: { SystemRoot: 'C:\\Windows', TMP: `${home}\\Temp`, TEMP: shared },
    realpath: p => [home, `${home}\\Temp`].includes(p) ? p : shared };
  assert.throws(() => tempRoots(undefined, options), /set TMP\/TEMP to a user-owned writable directory without spaces/);
  for (const root of [shared, 'C:\\Users\\Public\\Temp', 'C:\\Users\\with space-other\\Temp']) {
    assert.throws(() => tempRoots(root, { ...options, realpath: p => p }), /No private temp root/);
  }
  // Canonicalization must reject a profile-local junction into shared temp.
  assert.throws(() => tempRoots(`${home}\\alias`, options), /No private temp root/);
});

test('POSIX temp roots reject non-sticky shared roots and untrusted owners', () => {
  for (const info of [{ uid: 0, mode: 0o777 }, { uid: 1000, mode: 0o777 }, { uid: 1000, mode: 0o770 }, { uid: 2000, mode: 0o1777 }]) {
    assert.throws(() => tempRoots('/shared', { platform: 'linux', uid: 1000, realpath: p => p,
      stat: () => ({ ...info, isDirectory: () => true }) }), /No private temp root/);
  }
});

test('Windows generated paths allow short names and separators only on marked argv', () => {
  const options = { platform: 'win32', env: { SystemRoot: 'C:\\Windows' }, generatedPaths: [1] };
  for (const value of ['C:/Users/RUNNER~1/Temp/marc-123/release', 'C:\\Users\\RUNNER~1\\Temp\\marc-123\\release']) {
    assert.doesNotThrow(() => spawnSpec('C:\\bin\\agy.cmd', ['install', value], options));
    assert.throws(() => spawnSpec('C:\\bin\\agy.cmd', ['install', value], { ...options, generatedPaths: [] }));
  }
  for (const c of ['&', '|', '<', '>', '^', '%', '!', '"', '(', ')', '\r', '\n']) {
    assert.throws(() => spawnSpec('C:\\bin\\agy.cmd', ['install', `C:/temp/x${c}y`], options), /Unsafe generated/);
  }
  assert.throws(() => spawnSpec('C:\\bin\\agy.cmd', ['install', 'C:/with space/release'], options), /TMP\/TEMP/);
  assert.throws(() => spawnSpec('C:\\bin\\agy.cmd', ['install', '-evil'], options));
});

for (const [folder, error] of [['RUNNER~1', null], ['with space', /TMP\/TEMP/], ['x&y', /Unsafe generated/]]) {
  test(`Antigravity generated-path marking through batch runner: ${folder}`, async t => {
    const dir = await temp(t), root = path.join(dir, folder); await mkdir(root);
    const calls = [], native = agyRunner(calls);
    const run = async (h, args, options) => {
      spawnSpec(`C:\\bin\\${h}.cmd`, args, { platform: 'win32', env: { SystemRoot: 'C:\\Windows' }, generatedPaths: options?.generatedPaths });
      return native(h, args, options);
    };
    const action = installAgy({ run, release, tmpRoot: root });
    if (error) await assert.rejects(action, error); else await action;
    assert.deepEqual(await readdir(root), []);
    if (!error) {
      assert.deepEqual(calls.find(([h, a]) => h === 'git' && a[0] === 'clone')[2].generatedPaths, [7]);
      assert.deepEqual(calls.find(([h, a]) => h === 'agy' && a[1] === 'install')[2].generatedPaths, [2]);
    }
  });
}


test('POSIX temp roots accept stock root-owned sticky /tmp', () => {
  assert.deepEqual(tempRoots('/tmp', { platform: 'linux', uid: 1000, realpath: p => p,
    stat: () => ({ uid: 0, mode: 0o41777, isDirectory: () => true }) }), ['/tmp']);
});


test('Antigravity creates an owned 0700 child under the selected POSIX temp root', { skip: process.platform === 'win32' }, async t => {
  const calls = [], native = agyRunner(calls);
  let child;
  const run = async (h, args, options) => {
    if (h === 'git' && args[0] === 'clone') {
      child = options.cwd;
      const info = await lstat(child);
      assert.equal(info.uid, process.getuid());
      assert.equal(info.mode & 0o777, 0o700);
      assert.equal(info.isSymbolicLink(), false);
    }
    return native(h, args, options);
  };
  await installAgy({ run, release, tmpRoot: await temp(t) });
  assert.ok(child);
  await assert.rejects(lstat(child), { code: 'ENOENT' });
});

const stale = { claude: [{ id: 'marc@nexaduo', version: '26.11.14', scope: 'user' }],
  copilot: 'marc@nexaduo (v26.11.14)\n  Source: GitHub: NexaDuo/mARC\n', codex: { installed: [{ pluginId: 'marc@nexaduo', version: '26.11.14', installed: true }] } };
const mutating = calls => calls.filter(([, a]) => ['install', 'add', 'update', 'upgrade', 'remove', 'uninstall'].includes(a[1]) || a[2] === 'add');
for (const h of ['claude', 'copilot', 'codex']) {
  test(`install ${h} converges a stale install; a version that does not move is STALE`, async () => {
    const calls = [], logs = [];
    const rows = typeof stale[h] === 'string' ? stale[h] : JSON.stringify(stale[h]);
    assert.equal(await invoke(['install', '--harness', h, '--yes'], calls, { run: runner(calls, { [h]: rows }), log: s => logs.push(s) }), 1);
    const writes = calls.filter(([, a]) => !a.includes('list')).map(([, a]) => a);
    assert.deepEqual(writes, h === 'claude' ? [['plugin', 'marketplace', 'update', 'nexaduo'], ['plugin', 'update', 'marc@nexaduo', '--scope', 'user']] :
      h === 'codex' ? [['plugin', 'marketplace', 'upgrade', 'nexaduo'], ['plugin', 'add', 'marc@nexaduo']] : [['plugin', 'marketplace', 'update', 'nexaduo'], ['plugin', 'update', 'marc']]);
    assert.ok(logs.includes(`${h}: already installed at 26.11.14 (launcher release ${release.tag}); run \`npx @nexaduo/marc install\``), logs.join('\n'));
    assert.ok(logs.includes(`${h}: STALE`)); assert.ok(!logs.includes(`${h}: OK`));
    assert.ok(!logs.some(s => /older/i.test(s)));
  });
}
test('Claude install ignores rows from other scopes and other projects', async () => {
  const cwd = path.resolve('/work/here'), other = path.resolve('/work/other');
  const rows = JSON.stringify([{ id: 'marc@nexaduo', version: '26.11.14', scope: 'local', projectPath: other },
    { id: 'marc@nexaduo', version: '26.11.14', scope: 'project', projectPath: other }, { id: 'marc@nexaduo', version: '26.11.14', scope: 'user' }]);
  for (const scope of ['local', 'project']) {
    const calls = [];
    // Install runs (other-repo rows do not count); the post-install re-list still has no row for cwd, so not OK.
    assert.equal(await invoke(['install', '--harness', 'claude', '--scope', scope, '--yes'], calls, { cwd, run: runner(calls, { claude: rows }) }), 1);
    assert.deepEqual(mutating(calls).map(c => c[1]), [['plugin', 'install', 'marc@nexaduo', '--scope', scope]]);
  }
  const mine = JSON.stringify([{ id: 'marc@nexaduo', version: '26.11.14', scope: 'local', projectPath: cwd }]);
  const calls = [], logs = [];
  assert.equal(await invoke(['install', '--harness', 'claude', '--scope', 'local', '--yes'], calls, { cwd, run: runner(calls, { claude: mine }), log: s => logs.push(s) }), 1);
  assert.deepEqual(calls.filter(([, a]) => !a.includes('list')).map(([, a]) => a), [['plugin', 'marketplace', 'update', 'nexaduo'], ['plugin', 'update', 'marc@nexaduo', '--scope', 'local']]);
  assert.ok(logs.includes('claude: STALE'));
});
test('Claude user-scope install is not satisfied by a project row', async () => {
  const calls = [], logs = [];
  const rows = JSON.stringify([{ id: 'marc@nexaduo', version: '26.11.14', scope: 'project', projectPath: process.cwd() }]);
  assert.equal(await invoke(['install', '--harness', 'claude', '--yes'], calls, { run: runner(calls, { claude: rows }), log: s => logs.push(s) }), 1);
  assert.equal(mutating(calls).length, 1);
  assert.ok(logs.includes('claude: marc is not listed after install'));
});
test('Claude row without scope counts as user scope', async () => {
  const calls = [], logs = [];
  const rows = JSON.stringify([{ id: 'marc@nexaduo', version: '26.11.25.2' }]);
  assert.equal(await invoke(['install', '--harness', 'claude', '--yes'], calls, { run: runner(calls, { claude: rows }), log: s => logs.push(s) }), 0);
  assert.deepEqual(mutating(calls), []);
});
test('Claude project row matches cwd through a symlink, not a relative path', async t => {
  const dir = await temp(t), real = path.join(dir, 'real'), link = path.join(dir, 'link');
  await mkdir(real); await symlink(real, link);
  const row = projectPath => JSON.stringify([{ id: 'marc@nexaduo', version: '26.11.25.2', scope: 'project', projectPath }]);
  let calls = [];
  assert.equal(await invoke(['install', '--harness', 'claude', '--scope', 'project', '--yes'], calls, { cwd: link, run: runner(calls, { claude: row(real) }) }), 0);
  assert.deepEqual(mutating(calls), []);
  calls = [];
  await invoke(['install', '--harness', 'claude', '--scope', 'project', '--yes'], calls, { cwd: real, run: runner(calls, { claude: row('real') }) });
  assert.equal(mutating(calls).length, 1);
});
test('disabled installed row logs a hint; version is sanitized', async () => {
  const calls = [], logs = [];
  const rows = JSON.stringify([{ id: 'marc@nexaduo', version: '26.11.25.2\u202e\nevil', scope: 'user', enabled: false }]);
  assert.equal(await invoke(['install', '--harness', 'claude', '--yes'], calls, { run: runner(calls, { claude: rows }), log: s => logs.push(s) }), 1);
  assert.ok(logs.includes('claude: marc is installed but disabled'));
  assert.ok(logs.some(l => l.includes('already installed at unknown')), logs.join('|'));
  assert.equal(safeVersion('v26.11.25.2'), '26.11.25.2');
  assert.equal(safeVersion('1\n2'), 'unknown');
});
test('describeSource strips bidi/zero-width/line separators and shows flat path/url shapes', () => {
  assert.equal(describeSource({ marketplaceSource: { sourceType: 'x\u200b', source: 'a\u202eb\u2028c\u2029d' } }), 'sourceType: x, source: abcd');
  assert.equal(describeSource({ name: 'nexaduo', source: 'directory', path: '/dev/clone' }), 'sourceType: directory, source: /dev/clone');
  assert.equal(describeSource({ name: 'nexaduo', source: 'git', url: 'https://example.test/x.git' }), 'sourceType: git, source: https://example.test/x.git');
});
test('Copilot list with other plugins but not marc parses empty; fresh install runs', async () => {
  const text = 'Installed plugins:\n  other@market (v1.2.3)\n    Source: GitHub: a/b\n';
  assert.deepEqual(parsePlugins('copilot', text), []);
  assert.throws(() => parsePlugins('copilot', 'garbage with no headers'), /Unrecognized Copilot/);
  const calls = [], logs = [];
  let done = false;
  const run = async (h, args, options) => {
    calls.push([h, args, options]);
    if (args[1] === 'install') { done = true; return ''; }
    if (args[1] === 'list') return done ? fixtures.copilot : text;
    return args[1] === 'marketplace' ? JSON.stringify(marketFixtures[h]) : '';
  };
  assert.equal(await invoke(['install', '--harness', 'copilot', '--yes'], calls, { run, log: s => logs.push(s) }), 0);
  assert.equal(mutating(calls).length, 1);
  assert.ok(logs.includes('copilot: OK'));
});
test('install with a matching version is OK without install or add', async () => {
  for (const h of ['claude', 'copilot', 'codex']) {
    const calls = [], logs = [];
    assert.equal(await invoke(['install', '--harness', h, '--yes'], calls, { log: s => logs.push(s) }), 0);
    assert.deepEqual(mutating(calls), []);
    assert.ok(logs.includes(`${h}: already installed at 26.11.25.2`));
  }
});
test('install that leaves a differing version after the install command is STALE, not OK', async () => {
  const calls = [], logs = [];
  const run = freshRunner(calls, { claude: JSON.stringify(stale.claude) });
  assert.equal(await invoke(['install', '--harness', 'claude', '--yes'], calls, { run, log: s => logs.push(s) }), 1);
  assert.equal(mutating(calls).length, 1);
  assert.ok(logs.includes('claude: STALE')); assert.ok(!logs.includes('claude: OK'));
});
test('source-mismatch refusal names the found registration, strips control characters and preserves it', async () => {
  const market = { name: 'nexaduo', marketplaceSource: { sourceType: 'local', source: '/dev/clone\x1b[31m\nevil' } };
  const calls = [], logs = [];
  const run = async (h, args, options) => { calls.push([h, args, options]); return args[1] === 'list' ? fixtures.codex : JSON.stringify({ marketplaces: [market] }); };
  assert.equal(await invoke(['install', '--harness', 'codex', '--yes'], calls, { run, log: s => logs.push(s) }), 1);
  assert.ok(logs.some(s => s.includes('(sourceType: local, source: /dev/clone[31mevil)')), logs.join('|'));
  assert.deepEqual(mutating(calls), []);
  assert.ok(logs.includes('codex plugin marketplace remove nexaduo'));
});

// ---- console styling (#431) ----
const ESC = String.fromCharCode(27);
const SGR = new RegExp(ESC + '\\[[0-9;]*m', 'g');
const TTY = { isTTY: true, platform: 'linux' };
const style = (env, extra = {}) => createStyle({ env, ...TTY, ...extra });
const hasControl = s => [...s].some(c => { const n = c.charCodeAt(0); return n < 32 || (n >= 127 && n <= 159) || n === 0x202e; });

test('color gating: TTY, NO_COLOR, FORCE_COLOR, TERM=dumb and non-TTY', () => {
  assert.equal(style({}).enabled, true);
  assert.equal(style({}, { isTTY: false }).enabled, false);
  assert.equal(style({ TERM: 'dumb' }).enabled, false);
  assert.equal(style({ NO_COLOR: '1' }).enabled, false);
  assert.equal(style({ NO_COLOR: '' }).enabled, true, 'empty NO_COLOR is unset');
  assert.equal(style({ NO_COLOR: '1', FORCE_COLOR: '1' }).enabled, false, 'NO_COLOR beats FORCE_COLOR');
  for (const v of ['0', 'false']) assert.equal(style({ FORCE_COLOR: v }).enabled, false);
  for (const v of ['1', '2', '3', 'true']) {
    assert.equal(style({ FORCE_COLOR: v }, { isTTY: false }).enabled, true, `FORCE_COLOR=${v} beats non-TTY`);
    assert.equal(style({ FORCE_COLOR: v, TERM: 'dumb' }, { isTTY: false }).enabled, true);
  }
  assert.equal(style({ FORCE_COLOR: '' }, { isTTY: false }).enabled, false, 'empty FORCE_COLOR is unset');
  for (const s of [style({}, { isTTY: false }), style({ NO_COLOR: '1' }), style({ FORCE_COLOR: '0' })]) {
    for (const role of ['brand', 'ok', 'error', 'info']) assert.equal(s.paint(role, 'x'), 'x');
    assert.deepEqual(s.deco(['a']), []);
  }
});

test('color gating: Windows rule', () => {
  const win = (env, extra = {}) => style(env, { platform: 'win32', ...extra });
  assert.equal(win({}).enabled, false, 'legacy conhost stays plain');
  assert.equal(win({ WT_SESSION: 'x' }).enabled, true);
  assert.equal(win({ ConEMUANSI: 'ON' }).enabled, true);
  assert.equal(win({ ConEMUANSI: 'OFF' }).enabled, false);
  assert.equal(win({ ANSICON: '1' }).enabled, true);
  assert.equal(win({ TERM_PROGRAM: 'vscode' }).enabled, true);
  assert.equal(win({ WT_SESSION: 'x' }, { isTTY: false }).enabled, false, 'CI/non-TTY stays plain');
  const forced = win({ FORCE_COLOR: '1', COLORTERM: 'truecolor' }, { isTTY: false });
  assert.equal(forced.enabled, true);
  assert.equal(forced.depth, 4, 'forced legacy conhost is capped at 16 colors');
  assert.equal(win({ WT_SESSION: 'x', COLORTERM: 'truecolor' }).depth, 24);
});

test('color depth selection and role table', () => {
  assert.equal(style({ COLORTERM: 'truecolor' }).depth, 24);
  assert.equal(style({ COLORTERM: '24bit' }).depth, 24);
  assert.equal(style({ TERM: 'xterm-256color' }).depth, 8);
  assert.equal(style({ TERM: 'xterm' }).depth, 4);
  assert.equal(style({ FORCE_COLOR: '2' }).depth, 8);
  assert.equal(style({ FORCE_COLOR: '3' }).depth, 24);
  assert.equal(style({ FORCE_COLOR: '1', TERM: 'xterm' }).depth, 4);
  assert.equal(style({ COLORTERM: 'truecolor' }).paint('ok', 'a'), `${ESC}[1;38;2;124;255;178ma${ESC}[0m`);
  assert.equal(style({ TERM: 'xterm-256color' }).paint('stale', 'a'), `${ESC}[1;38;5;222ma${ESC}[0m`);
  assert.equal(style({}).paint('failed', 'a'), `${ESC}[1;95ma${ESC}[0m`);
  assert.equal(style({}).paint('warn', 'a'), `${ESC}[93ma${ESC}[0m`);
  assert.equal(style({}).paint('dim', 'a'), `${ESC}[2ma${ESC}[0m`);
  assert.equal(style({}).paint('info', 'a'), 'a');
  // Light backgrounds (COLORFGBG last field 7 or 15) use the normal-intensity 16-color table.
  for (const bg of ['0;15', '0;7']) {
    assert.equal(style({ COLORFGBG: bg, COLORTERM: 'truecolor' }).paint('ok', 'a'), `${ESC}[1;32ma${ESC}[0m`);
  }
  assert.equal(style({ COLORFGBG: '15;0', COLORTERM: 'truecolor' }).light, false);
  assert.throws(() => style({}).paint('not-a-role', 'a'), /Unknown style role/);
});

test('hostile native strings cannot inject SGR or controls', async () => {
  const bidi = String.fromCharCode(0x202e), c1 = String.fromCharCode(0x9b), nl = String.fromCharCode(10);
  const hostile = `${ESC}[31mred${ESC}[0m${c1}31m${bidi}evil${nl}next${String.fromCharCode(7)}`;
  const s = style({ COLORTERM: 'truecolor' });
  const painted = s.paint('error', hostile);
  assert.equal(stripControls(hostile), '[31mred[0m31mevilnext');
  assert.equal([...painted].filter(c => c === ESC).length, 2, 'only the two constant SGR sequences');
  assert.ok(!hasControl(painted.replace(SGR, '')), 'no control characters survive in the body');
  // End to end: a native CLI failure message carrying escapes, in color mode.
  const logs = [];
  const run = async (h, args) => { if (args[1] === 'list') throw new Error(hostile); return ''; };
  const code = await main(['doctor', '--harness', 'codex'], { release, run, log: l => logs.push(l), isTTY: false, style: s });
  assert.equal(code, 1);
  const all = logs.join(nl);
  assert.ok(all.includes(ESC), 'color mode is on');
  assert.ok(!hasControl(all.replace(SGR, '').split(nl).join('')), 'only our own SGR sequences contain controls');
  assert.ok(!all.includes(`${ESC}[31m`) && !all.includes(c1) && !all.includes(bidi), all);
});

test('plain install --dry-run output is pinned byte for byte, with no escapes', async () => {
  const rel = { tag: CUR_TAG, sha: 'a'.repeat(40), npm: CUR_NPM };
  const expected = [`mARC ${CUR_TAG} (${CUR_NPM}); install`,"claude plugin list --json","claude plugin marketplace list --json","claude plugin marketplace add NexaDuo/mARC","claude plugin install marc@nexaduo --scope user","claude plugin list --json","codex plugin list --json","codex plugin marketplace list --json","codex plugin marketplace add NexaDuo/mARC","codex plugin add marc@nexaduo","codex plugin list --json"];
  const argv = ['install', '--dry-run', '--harness', 'claude', '--harness', 'codex'];
  for (const [env, stdoutIsTTY] of [[{}, false], [{ TERM: 'dumb' }, true], [{ NO_COLOR: '1' }, true], [{ FORCE_COLOR: '0' }, true], [{ FORCE_COLOR: '3', NO_COLOR: '1' }, true]]) {
    const logs = [];
    assert.equal(await main(argv, { release: rel, run: async () => '[]', log: l => logs.push(l), isTTY: false, env, stdoutIsTTY, cwd: '/work/project' }), 0);
    assert.deepEqual(logs, expected);
    assert.ok(!logs.join('').includes(ESC));
  }
});

test('color mode re-lays out the same text: headings, prompt glyph, remedy block, result table', async () => {
  const rel = { tag: CUR_TAG, sha: 'a'.repeat(40), npm: CUR_NPM };
  const logs = [];
  const run = async (h, args) => args[1] === 'marketplace' && args[2] === 'list' ? JSON.stringify([{ name: 'nexaduo', source: 'github', repo: 'evil/x' }]) :
    args[1] === 'list' ? '[]' : '';
  const code = await main(['install', '--yes', '--harness', 'claude'],
    { release: rel, run, log: l => logs.push(l), isTTY: false, env: { FORCE_COLOR: '1' }, stdoutIsTTY: false });
  assert.equal(code, 1);
  const text = logs.map(l => l.replace(SGR, ''));
  assert.equal(text[0], `mARC ${CUR_TAG} (${CUR_NPM}); install`);
  assert.ok(text.includes('# claude'));
  assert.ok(text.some(l => l.startsWith('  ! claude: nexaduo is registered from a different') || l.includes('claude: nexaduo is registered from a different')));
  assert.ok(text.some(l => l.startsWith('  +- claude: Manual remedy (review first;')));
  assert.ok(text.includes('  |  $ claude plugin marketplace remove nexaduo'));
  assert.equal(text.filter(l => l.includes('claude: nexaduo is registered from a different')).length, 1, 'the refusal prints once (REV-5)');
  assert.ok(text.includes('* result'));
  assert.ok(text.some(l => /^ {2}claude {3}FAILED {2}not installed; nexaduo is registered/.test(l)), text.join('|'));
  assert.ok(text.every(l => [...l].every(c => c.charCodeAt(0) < 128)), 'ASCII only');
  assert.ok(logs.some(l => l.includes(`${ESC}[1;95mFAILED`)));
});

test('doctor rows paint every column: hostile scope and version cannot reach the terminal', async () => {
  const BEL = String.fromCharCode(7), nl = String.fromCharCode(10);
  const hostile = `pro${ESC}[2Jject${ESC}]0;pwned${BEL}`;
  const rel = { tag: CUR_TAG, sha: 'a'.repeat(40), npm: CUR_NPM };
  const run = async (h, args) => args[1] === 'list' ? JSON.stringify([{ id: 'marc@nexaduo', version: `1.${ESC}[2J${BEL}`, scope: hostile, enabled: true }]) : '[]';
  const logs = [];
  await main(['doctor', '--harness', 'claude'], { release: rel, run, log: l => logs.push(l), isTTY: false, env: { FORCE_COLOR: '1' }, stdoutIsTTY: false });
  const all = logs.join(nl);
  assert.ok(all.includes(ESC), 'color mode is on');
  assert.ok(!hasControl(all.replace(SGR, '').split(nl).join('')), all);
  assert.ok(!all.includes(`${ESC}[2J`) && !all.includes(`${ESC}]`), all);
  assert.ok(all.includes('pro[2Jject]0;pwned'), 'sanitized text is kept visible');
});

test('strip regex covers ALM, Mongolian vowel separator and word-joiner range', () => {
  for (const cp of [0x061c, 0x180e, 0x2060, 0x2061, 0x2062, 0x2063, 0x2064]) {
    assert.equal(stripControls(`a${String.fromCharCode(cp)}b`), 'ab', cp.toString(16));
  }
});

test('buffered color mode keeps a line for a harness not registered via begin()', () => {
  const logs = [];
  const out = makeOut(l => logs.push(l), createStyle({ env: { FORCE_COLOR: '1' }, isTTY: false }));
  out.begin(['claude']);
  out.line('codex', 'warn', 'codex: late');
  out.line('claude', 'warn', 'claude: early');
  out.flush();
  const text = logs.map(l => l.replace(SGR, ''));
  assert.deepEqual(text.filter(l => /late|early/.test(l)), ['  ! claude: early', '  ! codex: late']);
});

test('result detail reflects the outcome, not a pre-consent warning; doctor exceptions show the error', async () => {
  const rel = { tag: CUR_TAG, sha: 'a'.repeat(40), npm: CUR_NPM };
  const cwd = os.tmpdir();
  const rows = JSON.stringify([{ id: 'marc@nexaduo', version: CUR_VERSION, scope: 'project', projectPath: cwd }]);
  let logs = [];
  const opts = { release: rel, log: l => logs.push(l), isTTY: false, env: { FORCE_COLOR: '1' }, stdoutIsTTY: false, cwd };
  assert.equal(await main(['install', '--yes', '--harness', 'claude', '--scope', 'project'], { ...opts, run: async (h, a) => a[1] === 'list' ? rows : a[2] === 'list' ? JSON.stringify(marketFixtures.claude) : '' }), 0);
  let text = logs.map(l => l.replace(SGR, ''));
  assert.ok(text.some(l => l.startsWith('  ! Claude may edit')), 'pre-consent warning was shown');
  assert.ok(text.some(l => /^ {2}claude {3}OK {6}already installed at /.test(l)), text.join('|'));
  logs = [];
  assert.equal(await main(['doctor', '--harness', 'codex'], { ...opts, run: async () => { throw new Error('boom'); } }), 1);
  text = logs.map(l => l.replace(SGR, ''));
  assert.ok(text.some(l => /^ {2}codex {4}FAILED {2}boom$/.test(l)), text.join('|'));
});

// #435: doctor project column and STALE/FAILED classification.
const claudeRow = (scope, projectPath, version = '26.11.25.2') => ({ id: 'marc@nexaduo', version, scope, enabled: true, ...(projectPath === undefined ? {} : { projectPath }),
  source: { source: 'git-subdir', url: 'NexaDuo/mARC', path: 'harnesses/claude-code/marc', ref: release.tag } });
async function doctorRun(h, opts = {}, extra = {}) {
  const logs = [];
  const run = async (name, a) => a[1] === 'marketplace' ? JSON.stringify(opts.markets ?? marketFixtures[h]) : opts.list ?? fixtures[h];
  const code = await main(['doctor', '--harness', h], { release, run, log: l => logs.push(l), isTTY: false, env: {}, stdoutIsTTY: false, platform: 'linux', home: '/home/user', ...extra });
  return { code, logs, text: logs.map(l => l.replace(SGR, '')) };
}

test('doctor Claude project/local rows show ~-relative, absolute and sanitized project paths', async () => {
  const BEL = String.fromCharCode(7);
  const list = JSON.stringify([claudeRow('user'), claudeRow('project', '/home/user/repos/app'), claudeRow('local', '/home/user/repos/other'),
    claudeRow('project', '/srv/work/x'), claudeRow('project', `/home/user/${ESC}[2Jhost${BEL}`), claudeRow('project', '/home/username/evil')]);
  const { code, logs, text } = await doctorRun('claude', { list });
  assert.equal(code, 0);
  const rows = logs.filter(l => l.startsWith('claude\t'));
  assert.deepEqual(rows.map(r => r.split('\t')), [
    ['claude', '26.11.25.2', 'user', 'enabled'],
    ['claude', '26.11.25.2', 'project', 'enabled', '~/repos/app'],
    ['claude', '26.11.25.2', 'local', 'enabled', '~/repos/other'],
    ['claude', '26.11.25.2', 'project', 'enabled', '/srv/work/x'],
    ['claude', '26.11.25.2', 'project', 'enabled', '~/[2Jhost'],
    ['claude', '26.11.25.2', 'project', 'enabled', '/home/username/evil']]);
  assert.ok(!logs.join('').includes(ESC) && !logs.join('').includes(BEL));
  assert.deepEqual(text.slice(-1), ['claude: OK']);
  // Color mode keeps the same data, painted, with no stray controls.
  const color = await doctorRun('claude', { list }, { env: { FORCE_COLOR: '1' } });
  const ctext = color.text.filter(l => /^ {2}claude /.test(l));
  assert.ok(ctext.some(l => /project {1,}enabled {1,}~\/repos\/app$/.test(l)), ctext.join('|'));
  assert.ok(!hasControl(color.logs.join('\n').replace(SGR, '').split('\n').join('')));
  assert.ok(color.text.every(l => [...l].every(c => c.charCodeAt(0) < 128)));
});

test('doctor project column appends after DRIFT and the home option defaults from env', async () => {
  const list = JSON.stringify([claudeRow('project', '/home/me/repos/app', '26.11.25.1')]);
  const { logs } = await doctorRun('claude', { list }, { home: undefined, env: { HOME: '/home/me' } });
  assert.ok(logs.includes('claude\t26.11.25.1\tproject\tenabled\tDRIFT\t~/repos/app'), logs.join('|'));
});

test('doctor project column on win32 uses win32 path rules and ~/ output', async () => {
  const list = JSON.stringify([claudeRow('project', 'C:\\Users\\u\\repos\\app'), claudeRow('local', 'D:\\work\\app'), claudeRow('project', 'c:\\users\\U')]);
  const { logs } = await doctorRun('claude', { list }, { platform: 'win32', home: 'C:\\Users\\u' });
  const rows = logs.filter(l => l.startsWith('claude\t')).map(r => r.split('\t').pop());
  assert.deepEqual(rows, ['~/repos/app', 'D:\\work\\app', '~']);
  const env = await doctorRun('claude', { list }, { platform: 'win32', home: undefined, env: { USERPROFILE: 'C:\\Users\\u', HOME: '/ignored' } });
  assert.ok(env.logs.some(l => l.endsWith('\t~/repos/app')), env.logs.join('|'));
});

const staleHint = 'run `npx @nexaduo/marc install`';
for (const h of ['claude', 'codex', 'agy']) {
  test(`doctor ${h} version-only drift is STALE with the update hint and a non-zero exit`, async () => {
    const list = fixtures[h].replaceAll('26.11.25.2', '26.11.25.1');
    const { code, text } = await doctorRun(h, { list });
    assert.equal(code, 1);
    assert.ok(text.includes(`${h}: STALE`), text.join('|'));
    assert.ok(text.some(l => l.includes(staleHint)));
    assert.ok(!text.includes(`${h}: FAILED`));
    const color = await doctorRun(h, { list }, { env: { FORCE_COLOR: '1' } });
    assert.ok(color.logs.some(l => l.includes(`${ESC}[1;93mSTALE`)), 'STALE uses the amber role');
    assert.ok(color.text.some(l => new RegExp(`^ {2}${h} +STALE +out of date; run`).test(l)));
  });
}

test('doctor codex catalog behind (main-tracking, version matches) is STALE', async () => {
  const list = fixtures.codex.replace(release.tag, 'main');
  const { code, text } = await doctorRun('codex', { list });
  assert.equal(code, 1);
  assert.ok(text.includes('codex: STALE') && text.some(l => l.includes('main-tracking')), text.join('|'));
});

test('doctor source classification: only a ref-only difference is STALE, foreign sources are FAILED', async () => {
  const good = claudeRow('user').source;
  const cases = [[{ ...good, ref: 'main' }, 'STALE'], [{ source: 'git-subdir', url: 'NexaDuo/mARC', path: good.path }, 'STALE'],
    [{ ...good, url: 'Evil/mARC' }, 'FAILED'], [{ source: 'directory', path: '/tmp/x' }, 'FAILED'], ['NexaDuo/mARC', 'FAILED'],
    [{ ...good, path: 'harnesses/other/marc' }, 'FAILED'], [{ ...good, source: 'github', ref: 'main' }, 'FAILED']];
  for (const [source, want] of cases) {
    const { text } = await doctorRun('claude', { list: JSON.stringify([{ ...claudeRow('user'), source }]) });
    assert.ok(text.includes(`claude: ${want}`), `${JSON.stringify(source)}: ${text.join('|')}`);
    if (want === 'FAILED') assert.ok(text.some(l => l.includes('does not match the expected release source')), text.join('|'));
  }
});

test('doctor row with a missing or empty version is FAILED (version unreported)', async () => {
  for (const version of [undefined, '', '  ']) {
    const { code, text } = await doctorRun('claude', { list: JSON.stringify([{ ...claudeRow('user'), version }]) });
    assert.equal(code, 1);
    assert.ok(text.includes('claude: FAILED') && text.some(l => l.includes('claude: version unreported')), text.join('|'));
  }
});

test('displayProject: empty after stripping is "-", and a root home is never shortened', () => {
  assert.equal(displayProject(String.fromCharCode(7), '/home/u', 'linux'), '-');
  assert.equal(displayProject('/srv/app', '/', 'linux'), '/srv/app');
  assert.equal(displayProject('C:\\work\\app', 'C:\\', 'win32'), 'C:\\work\\app');
  assert.equal(displayProject('/home/u/app', '/home/u', 'linux'), '~/app');
});

test('doctor FAILED cases: codex local source, missing marketplace, missing plugin, unverifiable, errors', async () => {
  const local = [{ name: 'nexaduo', marketplaceSource: { sourceType: 'local', source: '/home/user/mARC' } }];
  let r = await doctorRun('codex', { markets: { marketplaces: local } });
  assert.equal(r.code, 1); assert.ok(r.text.includes('codex: FAILED'), r.text.join('|'));
  r = await doctorRun('codex', { markets: { marketplaces: [] } });
  assert.ok(r.text.includes('codex: FAILED') && r.text.some(l => l.includes('marketplace missing')));
  r = await doctorRun('codex', { list: '{"installed":[]}' });
  assert.equal(r.code, 1); assert.ok(r.text.includes('codex: FAILED'));
  assert.ok(r.text.some(l => l.includes('run `npx @nexaduo/marc install`')), r.text.join('|'));
  r = await doctorRun('agy', { list: '[]' });
  assert.ok(r.text.includes('agy: FAILED'));
  r = await doctorRun('codex', { list: JSON.stringify({ installed: [{ pluginId: 'marc@nexaduo', version: '26.11.25.2', installed: true, enabled: true }] }) });
  assert.ok(r.text.includes('codex: FAILED'), 'release source unverifiable: ' + r.text.join('|'));
  r = await doctorRun('codex', { list: 'garbage' });
  assert.ok(r.text.includes('codex: FAILED'));
});

test('doctor mixed STALE and FAILED reports FAILED, in the magenta role', async () => {
  const local = [{ name: 'nexaduo', marketplaceSource: { sourceType: 'local', source: '/home/user/mARC' } }];
  const list = fixtures.codex.replaceAll('26.11.25.2', '26.11.25.1');
  const r = await doctorRun('codex', { list, markets: { marketplaces: local } });
  assert.equal(r.code, 1);
  assert.ok(r.text.includes('codex: FAILED') && !r.text.includes('codex: STALE'), r.text.join('|'));
  assert.ok(r.logs.every(l => [...l].every(c => c.charCodeAt(0) < 128)));
  const c = await doctorRun('codex', { list, markets: { marketplaces: local } }, { env: { FORCE_COLOR: '1' } });
  assert.ok(c.logs.some(l => l.includes(`${ESC}[1;95mFAILED`)));
});

// ---- --force and the Claude git / Windows Codex shapes (#440) ----
const HOME = '/home/user';
const GOOD = { claude: { name: 'nexaduo', source: 'github', repo: 'NexaDuo/mARC' }, copilot: { name: 'nexaduo', source: 'GitHub: NexaDuo/mARC' },
  codex: { name: 'nexaduo', root: '<CODEX_HOME>/.tmp/marketplaces/nexaduo', marketplaceSource: { sourceType: 'git', source: 'https://github.com/NexaDuo/mARC.git' } } };
const FOREIGN = { claude: { name: 'nexaduo', source: 'github', repo: 'Fork/mARC' }, codex: { name: 'nexaduo', root: '/x', marketplaceSource: { sourceType: 'git', source: 'https://github.com/Fork/mARC.git' } },
  copilot: { name: 'nexaduo', source: 'GitHub: Fork/mARC' } };
const WIN_CODEX = { name: 'nexaduo', root: 'C:\\repos\\example\\mARC' };
const OLD = [{ id: 'marc@nexaduo', version: '26.11.14', scope: 'user', enabled: true }];
const writes = calls => calls.filter(([, a]) => !a.includes('list'));
const wargs = calls => writes(calls).map(([h, a]) => `${h} ${a.join(' ')}`);

// Stateful fake native CLIs: remove clears the marketplace AND its plugins.
function forceFake(calls, init, hooks = {}) {
  const st = Object.fromEntries(Object.entries(init).map(([h, v]) => [h, { markets: structuredClone(v.markets), plugins: structuredClone(v.plugins ?? []), reads: 0 }]));
  const fmtM = (h, m) => JSON.stringify(h === 'codex' ? { marketplaces: m } : m);
  const fmtP = (h, p) => JSON.stringify(h === 'codex' ? { installed: p, available: [] } : p);
  return async (h, args, options) => {
    calls.push([h, args, options]);
    const s = st[h], key = args.slice(1).join(' ');
    if (hooks.fail?.(h, args)) throw new Error('native failure');
    if (args[1] === 'list') return fmtP(h, s.plugins);
    if (key === 'marketplace list --json') { s.reads++; return fmtM(h, hooks.list?.(h, s.reads, s.markets) ?? s.markets); }
    if (key.startsWith('marketplace remove')) { if (!hooks.keep) s.markets = []; if (!hooks.survive) s.plugins = []; return ''; }
    if (key.startsWith('marketplace add')) { s.markets = hooks.add?.(h) ?? [GOOD[h]]; return ''; }
    if (args[1] === 'update' && args[2] !== 'nexaduo' && args[1] !== 'marketplace') s.plugins.forEach(p => { p.version = '26.11.25.2'; });
    if (['install', 'add'].includes(args[1]) && args[2] === 'marc@nexaduo') {
      s.plugins = s.plugins.filter(p => (p.scope ?? 'user') !== (args[4] ?? 'user') && h === 'claude');
      s.plugins.push(h === 'codex' ? { pluginId: 'marc@nexaduo', version: '26.11.25.2', installed: true, enabled: true } :
        h === 'copilot' ? { name: 'marc', marketplace: 'nexaduo', version: '26.11.25.2' } :
          { id: 'marc@nexaduo', version: '26.11.25.2', scope: args[4] ?? 'user', projectPath: options?.cwd, enabled: true });
    }
    return '';
  };
}
async function force(args, init, hooks = {}, extra = {}) {
  const calls = [], logs = [];
  const code = await main(args, { release, run: forceFake(calls, init, hooks), log: s => logs.push(s), isTTY: false, env: {}, stdoutIsTTY: false, home: HOME, platform: 'linux', ...extra });
  return { code, calls, logs };
}
const LIST_ONLY = c => c.calls.every(([, a]) => a.includes('list'));

test('classifyMarketplace: match, pinned, local, foreign-remote, unverifiable, in that order', () => {
  const kind = (h, row) => classifyMarketplace(h, row).kind;
  assert.equal(kind('claude', { name: 'nexaduo', source: 'git', url: 'https://github.com/NexaDuo/mARC.git' }), 'match');
  assert.equal(kind('claude', { name: 'nexaduo', source: 'git', url: 'NexaDuo/mARC' }), 'match');
  assert.equal(kind('claude', { name: 'nexaduo', source: 'git', url: 'https://github.com/NexaDuo/mARC.git#v1' }), 'pinned');
  assert.equal(kind('claude', { ...GOOD.claude, ref: 'v1' }), 'pinned');
  assert.equal(kind('claude', { name: 'nexaduo', source: 'git', url: 'https://github.com/Evil/mARC.git' }), 'foreign-remote');
  assert.equal(kind('claude', { name: 'nexaduo', source: 'git', url: 'git@github.com:Fork/mARC.git' }), 'foreign-remote');
  assert.equal(kind('claude', { name: 'nexaduo', source: 'git', url: 'ssh://git@host/Fork/mARC.git' }), 'foreign-remote');
  assert.equal(kind('claude', { name: 'nexaduo', source: 'git', url: 'github.com.evil/NexaDuo/mARC' }), 'unverifiable');
  // A GitHub row carrying cache paths is never local.
  assert.equal(kind('claude', { ...FOREIGN.claude, installLocation: '/home/u/.claude/m', root: '/r', path: '/p' }), 'foreign-remote');
  assert.equal(kind('claude', { ...GOOD.claude, installLocation: '/home/u/.claude/m', root: '/r', path: '/p' }), 'match');
  for (const source of ['\\\\host\\share\\mARC', 'C:\\repos\\example\\mARC', 'C:/repos/example/mARC', 'file:///home/user/mARC', 'Local: /home/user/mARC', './mARC', '../mARC', '~/mARC']) {
    assert.equal(kind('claude', { name: 'nexaduo', source: 'git', url: source }), 'local', source);
  }
  assert.equal(kind('claude', { name: 'nexaduo', source: 'directory', path: '/home/user/mARC' }), 'local');
  assert.equal(kind('codex', { name: 'nexaduo', root: '/x', marketplaceSource: { sourceType: 'local', source: '/home/user/mARC' } }), 'local');
  const win = classifyMarketplace('codex', WIN_CODEX);
  assert.equal(win.kind, 'unverifiable'); assert.equal(win.missing, 'marketplaceSource');
  assert.match(win.reason, /missing marketplaceSource/);
  assert.equal(classifyMarketplace('claude', { name: 'nexaduo', source: 'github' }).missing, 'repo');
  assert.equal(classifyMarketplace('claude', { name: 'nexaduo', source: 'git' }).missing, 'url');
});

test('Claude git-URL marketplace matches through REPOS but not pins or foreign URLs; install agrees', async () => {
  const git = url => ({ name: 'nexaduo', source: 'git', url });
  for (const url of ['NexaDuo/mARC', 'https://github.com/NexaDuo/mARC', 'https://github.com/NexaDuo/mARC.git']) assert.equal(marketplaceMatches('claude', git(url)), true, url);
  for (const url of ['https://github.com/NexaDuo/mARC.git#v1', 'https://github.com/Evil/mARC.git', 'github.com.evil/NexaDuo/mARC']) assert.equal(marketplaceMatches('claude', git(url)), false, url);
  for (const key of ['ref', 'branch', 'tag', 'sha']) assert.equal(marketplaceMatches('claude', { ...git('https://github.com/NexaDuo/mARC.git'), [key]: 'x' }), false, key);
  const run = async (h, args) => args[1] === 'marketplace' ? JSON.stringify([git('https://github.com/NexaDuo/mARC.git')]) : fixtures.claude;
  const logs = [];
  assert.equal(await invoke(['install', '--harness', 'claude', '--yes'], [], { run, log: s => logs.push(s) }), 0);
  assert.ok(!logs.some(s => /different or unverified/.test(s)));
});

test('Codex Windows shape (name + root only) is unverified, says so with the stripped root, and reports not installed', async () => {
  const root = 'C:\\repos\\example\\mARC\u001b[31m\u202e';
  const market = { ...WIN_CODEX, root };
  const calls = [], logs = [];
  const run = async (h, args) => { calls.push(args); return args[1] === 'marketplace' ? JSON.stringify({ marketplaces: [{ name: 'openai-curated', root: 'C:\\x' }, market] }) : '{"installed":[],"available":[]}'; };
  assert.equal(await invoke(['install', '--harness', 'codex', '--yes'], [], { run, log: s => logs.push(s) }), 1);
  assert.ok(logs.some(s => s.includes('(registered from local root C:\\repos\\example\\mARC[31m)')), logs.join('|'));
  assert.ok(!logs.some(s => /source: unknown/.test(s)));
  assert.ok(logs.includes('codex: marc not installed'));
  assert.ok(logs.includes('codex: FAILED'));
  assert.ok(calls.every(a => a.includes('list')));
  assert.equal(describeSource(market), 'registered from local root C:\\repos\\example\\mARC[31m');
  // Color mode: the result table says "not installed".
  const colored = [];
  await main(['install', '--harness', 'codex', '--yes'], { release, run, log: l => colored.push(l.replace(SGR, '')), isTTY: false, env: { FORCE_COLOR: '1' }, stdoutIsTTY: false });
  assert.ok(colored.some(l => /^ {2}codex {4}FAILED {2}not installed; /.test(l)), colored.join('|'));
});

test('Codex plugin list marketplaceSource enriches a root-only marketplace row', async () => {
  const installed = { installed: [{ pluginId: 'marc@nexaduo', version: '26.11.25.2', installed: true, enabled: true,
    marketplaceSource: { sourceType: 'git', source: 'https://github.com/NexaDuo/mARC.git' } }] };
  const run = async (h, args) => args[1] === 'marketplace' ? JSON.stringify({ marketplaces: [WIN_CODEX] }) : JSON.stringify(installed);
  const logs = [];
  assert.equal(await invoke(['install', '--harness', 'codex', '--yes'], [], { run, log: s => logs.push(s) }), 0);
  assert.ok(logs.includes('codex: already installed at 26.11.25.2'));
  const rows = parsePlugins('codex', JSON.stringify(installed));
  assert.equal(rows[0].marketplaceSource.sourceType, 'git');
  assert.equal(enrichMarketplaces('codex', [WIN_CODEX], rows)[0].marketplaceSource.sourceType, 'git');
  assert.equal(enrichMarketplaces('claude', [WIN_CODEX], rows)[0].marketplaceSource, undefined);
});

test('--force argument rules: --force is install (and the update alias) only; --force-local is a hidden alias of --force; agy has no marketplace', async () => {
  for (const c of ['doctor', 'uninstall']) assert.throws(() => parseArgs([c, '--force']), /install only/);
  const alias = parseArgs(['install', '--force-local']);
  assert.equal(alias.force, true); assert.equal(alias.deprecatedForceLocal, true);
  const upd = parseArgs(['update', '--force']);
  assert.equal(upd.command, 'install'); assert.equal(upd.deprecatedUpdate, true); assert.equal(upd.force, true);
  const logs = [];
  await main(['install', '--force', '--dry-run', '--harness', 'agy'], { release, run: runner([]), log: s => logs.push(s), isTTY: false, env: {}, stdoutIsTTY: false });
  assert.ok(logs.includes('agy: --force not applicable'));
});

const planCases = [
  ['(a) foreign remote', 'claude', FOREIGN.claude, [], 'claude: --force will REMOVE marketplace nexaduo (found sourceType: github, source: Fork/mARC; reason: foreign source)'],
  ['(b) pinned ref', 'claude', { ...GOOD.claude, ref: 'v1' }, [], 'claude: --force will REMOVE marketplace nexaduo (found sourceType: github, source: NexaDuo/mARC; reason: pinned ref)'],
  ['(c) local checkout', 'claude', { name: 'nexaduo', source: 'directory', path: '/home/user/mARC' }, [], 'claude: --force will REMOVE marketplace nexaduo (found sourceType: directory, source: ~/mARC; reason: local checkout)'],
  ['(d) unverifiable', 'codex', WIN_CODEX, [], 'codex: --force will REMOVE marketplace nexaduo (found registered from local root C:\\repos\\example\\mARC; reason: unverifiable source (missing marketplaceSource))'],
];
for (const [name, h, market, extra, line] of planCases) {
  test(`--force --dry-run plan ${name}: shown, nothing but list commands run`, async () => {
    const r = await force(['install', '--force', '--dry-run', '--harness', h, ...extra], { [h]: { markets: [market], plugins: [] } });
    assert.equal(r.code, 0);
    assert.ok(r.logs.includes(line), r.logs.join('\n'));
    const typed = name.includes('local') || name.includes('unverifiable') ? ' (needs typed confirmation; refused under --yes or without a terminal)' : '';
    assert.ok(r.logs.includes(`${h} plugin marketplace remove nexaduo${typed}`), r.logs.join('\n'));
    assert.ok(r.logs.includes(`${h} plugin marketplace add NexaDuo/mARC`));
    assert.ok(r.logs.includes(h === 'codex' ? 'codex plugin add marc@nexaduo' : 'claude plugin install marc@nexaduo --scope user'));
    assert.ok(LIST_ONLY(r), wargs(r.calls).join('|'));
  });
}

test('--force --dry-run plan (e) stale match: update lines only, nothing but list commands run', async () => {
  const r = await force(['install', '--force', '--dry-run', '--harness', 'claude'], { claude: { markets: [GOOD.claude], plugins: OLD } });
  assert.equal(r.code, 0); assert.ok(LIST_ONLY(r));
  assert.deepEqual(r.logs.slice(1), ['claude: --force: registration matches; updating the stale install',
    'claude plugin marketplace update nexaduo', 'claude plugin update marc@nexaduo --scope user', 'claude plugin list --json']);
});

test('without --force, mismatch output is pinned line for line and a stale match converges', async () => {
  const head = ['mARC v26.11.25.2 (26.1125.2); install', 'claude plugin list --json', 'claude plugin marketplace list --json', 'claude plugin marketplace add NexaDuo/mARC',
    'claude plugin install marc@nexaduo --scope user', 'claude plugin list --json'];
  let r = await force(['install', '--yes', '--harness', 'claude'], { claude: { markets: [FOREIGN.claude], plugins: OLD } });
  const refusal = 'claude: nexaduo is registered from a different or unverified source (sourceType: github, source: Fork/mARC); registration preserved; run npx @nexaduo/marc install --force';
  assert.deepEqual(r.logs, [head[0], refusal, 'claude: Manual remedy (review first; this removes the marketplace registration):', 'claude plugin marketplace remove nexaduo',
    'claude plugin marketplace add NexaDuo/mARC', 'claude plugin install marc@nexaduo --scope user', 'claude: FAILED']);
  assert.deepEqual(r.calls.map(([, a]) => a.join(' ')), ['plugin list --json', 'plugin marketplace list --json']);
  r = await force(['install', '--yes', '--harness', 'claude'], { claude: { markets: [GOOD.claude], plugins: OLD } });
  // install converges (#451): a stale matching install is updated, and the plan shows the update argv before consent.
  assert.deepEqual(r.logs, ['mARC v26.11.25.2 (26.1125.2); install', 'claude: registration matches; updating the stale install',
    'claude plugin marketplace update nexaduo', 'claude plugin update marc@nexaduo --scope user', 'claude plugin list --json',
    'claude: already installed at 26.11.25.2', 'claude: OK']);
  assert.deepEqual(r.calls.map(([, a]) => a.join(' ')), ['plugin list --json', 'plugin marketplace list --json', 'plugin list --json', 'plugin marketplace list --json', 'plugin marketplace update nexaduo', 'plugin update marc@nexaduo --scope user', 'plugin list --json']);
});

test('Doctor and install agree on Claude URL registrations through main(): right repo passes, a foreign one is FAILED in both', async () => {
  const urlRow = url => ({ name: 'nexaduo', source: 'git', url });
  const listRun = markets => async (h, a) => a[1] === 'marketplace' ? JSON.stringify(markets) : fixtures.claude;
  for (const [url, code] of [['https://github.com/NexaDuo/mARC.git', 0], ['https://github.com/Evil/mARC.git', 1]]) {
    for (const command of ['doctor', 'install']) {
      const logs = [];
      assert.equal(await invoke([command, '--harness', 'claude', '--yes'], [], { run: listRun([urlRow(url)]), log: s => logs.push(s) }), code, `${command} ${url}`);
      assert.equal(logs.some(s => /source drift|different or unverified/.test(s)), code === 1, `${command} ${url}`);
    }
  }
});

test('--force plan: Claude reinstall per pre-read scope, project cwd shown, disabled rows re-disabled, settings warning', async t => {
  const dir = await temp(t);
  const plugins = [{ id: 'marc@nexaduo', version: '26.11.14', scope: 'user', enabled: true }, { id: 'marc@nexaduo', version: '26.11.14', scope: 'project', projectPath: dir, enabled: false }];
  const r = await force(['install', '--force', '--dry-run', '--harness', 'claude'], { claude: { markets: [FOREIGN.claude], plugins } }, {}, { home: path.dirname(dir), platform: process.platform });
  const rel = displayProject(dir, path.dirname(dir));
  assert.deepEqual(r.logs.slice(r.logs.findIndex(s => s.includes('--force will REMOVE'))), [
    'claude: --force will REMOVE marketplace nexaduo (found sourceType: github, source: Fork/mARC; reason: foreign source)',
    'claude plugin marketplace remove nexaduo', 'claude plugin marketplace add NexaDuo/mARC',
    'claude plugin install marc@nexaduo --scope user',
    `claude plugin update marc@nexaduo --scope project   (cwd ${rel}; if marc is still installed after the add)`,
    `claude plugin install marc@nexaduo --scope project   (cwd ${rel}; instead, if marc is gone after the add)`,
    'claude plugin disable marc@nexaduo --scope project',
    `Claude may edit ${displayProject(path.join(dir, '.claude', 'settings.json'), path.dirname(dir))} (project scope)`,
    'claude plugin list --json']);
  assert.ok(LIST_ONLY(r));
});

test('--force on an install whose registration matches: current is a no-op, stale runs only the update argv', async () => {
  const current = [{ id: 'marc@nexaduo', version: '26.11.25.2', scope: 'user', enabled: true }];
  let r = await force(['install', '--force', '--yes', '--harness', 'claude'], { claude: { markets: [GOOD.claude], plugins: current } });
  assert.equal(r.code, 0); assert.deepEqual(wargs(r.calls), []);
  assert.ok(r.logs.includes('claude: --force: nothing to do'));
  r = await force(['install', '--force', '--yes', '--harness', 'claude'], { claude: { markets: [GOOD.claude], plugins: OLD } });
  assert.deepEqual(wargs(r.calls), ['claude plugin marketplace update nexaduo', 'claude plugin update marc@nexaduo --scope user']);
  r = await force(['update', '--force', '--yes', '--harness', 'claude'], { claude: { markets: [GOOD.claude], plugins: OLD } });
  assert.ok(!wargs(r.calls).some(s => /remove|marketplace add/.test(s)));
  assert.ok(r.logs.includes('claude: --force: registration matches; updating the stale install'));
});

test('--force consent: declined and non-TTY without --yes run no mutating argv; the prompt counts removals', async () => {
  const init = { claude: { markets: [FOREIGN.claude], plugins: OLD } };
  const calls = [], logs = [];
  let asked = 0, question;
  assert.equal(await main(['install', '--force', '--harness', 'claude'], { release, run: forceFake(calls, init), log: s => logs.push(s), isTTY: true, env: {}, stdoutIsTTY: false, home: HOME, confirm: async q => { asked++; question = q; return false; } }), 1);
  assert.equal(asked, 1); assert.equal(question, 'Run this plan? It removes 1 marketplace registration(s)'); assert.deepEqual(wargs(calls), []);
  assert.ok(logs.includes('claude plugin marketplace remove nexaduo'));
  const none = [];
  await assert.rejects(main(['install', '--force', '--harness', 'claude'], { release, run: forceFake(none, init), log: silent, isTTY: false, env: {} }), /Non-TTY execution requires --yes/);
  assert.deepEqual(none, []);
});

const LOCAL = { claude: { name: 'nexaduo', source: 'directory', path: '/home/user/mARC' }, codex: WIN_CODEX };
const REFUSED = (reason, desc) => `--force refused: ${reason} (found ${desc}); a local or unverifiable registration is only replaced after a typed confirmation in a terminal; registration preserved. Rerun npx @nexaduo/marc install --force interactively without --yes, or run the commands below.`;
const tty = (answers, extra = {}) => {
  const asked = [], yn = [];
  return { asked, yn, extra: { isTTY: true, confirm: async q => { yn.push(q); return true; }, confirmLocal: async q => { asked.push(q); return answers.shift(); }, ...extra } };
};

test('local and unverifiable --force under --yes (even on a TTY) or without a TTY is refused: zero mutating argv, D12b message, manual remedy', async () => {
  for (const [h, market] of Object.entries(LOCAL)) {
    const reason = h === 'claude' ? 'local checkout' : 'unverifiable source (missing marketplaceSource)';
    const desc = h === 'claude' ? 'sourceType: directory, source: ~/mARC' : 'registered from local root C:\\repos\\example\\mARC';
    for (const isTTY of [false, true]) {
      let asked = 0;
      const r = await force(['install', '--force', '--yes', '--harness', h], { [h]: { markets: [market], plugins: [] } }, {}, { isTTY, confirmLocal: async () => { asked++; return 'replace'; } });
      assert.equal(r.code, 1); assert.deepEqual(wargs(r.calls), []); assert.equal(asked, 0, 'a typed answer is never requested under --yes');
      assert.ok(r.logs.includes(`${h}: ${REFUSED(reason, desc)}`), r.logs.join('\n'));
      assert.ok(r.logs.includes(`${h} plugin marketplace remove nexaduo`), 'manual remedy');
      assert.ok(r.logs.indexOf(`${h}: ${REFUSED(reason, desc)}`) < r.logs.findIndex(l => l.includes('Manual remedy')), 'the message precedes the commands it points at');
      assert.ok(r.logs.includes(`${h}: FAILED`));
    }
    // The deprecated alias grants nothing extra.
    const r = await force(['install', '--yes', '--force', '--force-local', '--harness', h], { [h]: { markets: [market], plugins: [] } });
    assert.equal(r.code, 1); assert.deepEqual(wargs(r.calls), []);
    assert.ok(r.logs.includes('--force-local is deprecated and ignored; --force now covers local sources with a typed confirmation'));
  }
});

test('typed confirmation: after the plan y/N, the constant token replace runs the existing forceReplace path', async () => {
  for (const [h, market] of Object.entries(LOCAL)) {
    const t = tty(['  replace  ']);
    const r = await force(['install', '--force', '--harness', h], { [h]: { markets: [market], plugins: [] } }, {}, t.extra);
    assert.equal(r.code, 0, r.logs.join('\n'));
    assert.equal(t.yn.length, 1); assert.equal(t.asked.length, 1);
    assert.match(t.asked[0], new RegExp(`^${h}: nexaduo is registered from .+ \\(.+\\)\\. This may be a development clone\\. Type replace to remove it and register NexaDuo/mARC, or press Enter to keep it: $`));
    assert.ok(!t.asked[0].includes('/home/user') || t.asked[0].includes('~/mARC'));
    assert.deepEqual(wargs(r.calls).slice(0, 2), [`${h} plugin marketplace remove nexaduo`, `${h} plugin marketplace add NexaDuo/mARC`]);
    // Order: plan consent, then the typed prompt, then the TOCTOU re-list and the remove.
    const firstRemove = r.calls.findIndex(([, a]) => a[2] === 'remove');
    assert.ok(firstRemove > 1 && r.calls.slice(0, firstRemove).filter(([, a]) => a.join(' ') === 'plugin marketplace list --json').length >= 2);
  }
});

test('typed confirmation: a decline, wrong token or case mismatch removes nothing, exits non-zero, and other harnesses proceed', async () => {
  for (const answer of ['', 'y', 'yes', 'REPLACE', 'replace now', undefined]) {
    const t = tty([answer]);
    const init = { claude: { markets: [LOCAL.claude], plugins: [] }, codex: { markets: [FOREIGN.codex], plugins: [] } };
    const r = await force(['install', '--force', '--harness', 'claude', '--harness', 'codex'], init, {}, t.extra);
    assert.equal(r.code, 1);
    assert.equal(t.asked.length, 1, 'only the local row is asked about');
    assert.ok(!wargs(r.calls).some(c => c.startsWith('claude') && /remove/.test(c)), wargs(r.calls).join('|'));
    assert.ok(r.logs.includes('claude: kept sourceType: directory, source: ~/mARC; not confirmed; registration preserved'), r.logs.join('\n'));
    assert.ok(!r.logs.includes('claude plugin marketplace remove nexaduo'), 'no manual-remedy block on a decline');
    assert.ok(r.logs.includes('claude: FAILED'));
    assert.ok(wargs(r.calls).includes('codex plugin marketplace remove nexaduo') && r.logs.includes('codex: OK'), 'the foreign row keeps today consent');
  }
});

test('typed confirmation: plan y/N declined asks nothing; --dry-run asks nothing and labels the REMOVE line', async () => {
  let t = tty([], { confirm: async () => false });
  let r = await force(['install', '--force', '--harness', 'claude'], { claude: { markets: [LOCAL.claude], plugins: [] } }, {}, t.extra);
  assert.equal(r.code, 1); assert.equal(t.asked.length, 0); assert.deepEqual(wargs(r.calls), []);
  t = tty([]);
  r = await force(['install', '--force', '--dry-run', '--harness', 'claude'], { claude: { markets: [LOCAL.claude], plugins: [] } }, {}, t.extra);
  assert.equal(r.code, 0); assert.equal(t.asked.length, 0); assert.equal(t.yn.length, 0);
  assert.ok(r.logs.includes('claude plugin marketplace remove nexaduo (needs typed confirmation; refused under --yes or without a terminal)'));
});

test('--force-local is a hidden alias: warns once, grants nothing, and is absent from HELP', async () => {
  const logs = [];
  await main(['--help'], { release, log: s => logs.push(s) });
  assert.ok(!logs.join('\n').includes('--force-local'));
  const r = await force(['install', '--force-local', '--yes', '--harness', 'claude'], { claude: { markets: [FOREIGN.claude], plugins: [] } });
  assert.equal(r.logs.filter(s => s.startsWith('--force-local is deprecated and ignored')).length, 1);
  assert.equal(r.code, 0);
});

test('--force executes exactly the constant argv and never touches unrelated marketplaces or plugins', async () => {
  const other = { name: 'other-market', source: 'github', repo: 'Evil/other' };
  const plugins = [...OLD, { id: 'tool@other-market', version: '1.0.0', scope: 'user' }];
  const r = await force(['install', '--force', '--yes', '--harness', 'claude'], { claude: { markets: [other, FOREIGN.claude], plugins } });
  assert.equal(r.code, 0, r.logs.join('\n'));
  assert.deepEqual(wargs(r.calls), ['claude plugin marketplace remove nexaduo', 'claude plugin marketplace add NexaDuo/mARC', 'claude plugin install marc@nexaduo --scope user']);
  assert.ok(!r.calls.some(([, a]) => a.join(' ').includes('other')));
  assert.ok(r.logs.includes('claude: OK'));
});

test('--force refuses more than one nexaduo registration', async () => {
  for (const dry of [true, false]) {
    const r = await force(['install', '--force', dry ? '--dry-run' : '--yes', '--harness', 'codex'], { codex: { markets: [GOOD.codex, FOREIGN.codex], plugins: [] } });
    assert.equal(r.code, dry ? 0 : 1); assert.deepEqual(wargs(r.calls), []);
    assert.ok(r.logs.some(s => s.includes('2 nexaduo registrations')));
  }
});

test('--force TOCTOU: a registration that changed after the plan is not removed', async () => {
  const r = await force(['install', '--force', '--yes', '--harness', 'claude'], { claude: { markets: [FOREIGN.claude], plugins: OLD } },
    { list: (h, n, m) => n >= 2 ? [{ name: 'nexaduo', source: 'directory', path: '/home/user/dev' }] : m });
  assert.equal(r.code, 1); assert.deepEqual(wargs(r.calls), []);
  assert.ok(r.logs.includes('claude: registration changed since the plan was shown; nothing removed'));
});

test('--force: remove that exits 0 but leaves nexaduo listed does not add', async () => {
  const r = await force(['install', '--force', '--yes', '--harness', 'claude'], { claude: { markets: [FOREIGN.claude], plugins: OLD } }, { keep: true });
  assert.equal(r.code, 1);
  assert.deepEqual(wargs(r.calls), ['claude plugin marketplace remove nexaduo']);
  assert.ok(r.logs.some(s => s.includes('still listed after marketplace remove')));
});

test('--force: remove failure preserves the registration and adds nothing', async () => {
  const r = await force(['install', '--force', '--yes', '--harness', 'claude'], { claude: { markets: [FOREIGN.claude], plugins: OLD } }, { fail: (h, a) => a[2] === 'remove' });
  assert.equal(r.code, 1); assert.ok(!wargs(r.calls).some(s => /marketplace add/.test(s)));
  assert.ok(r.logs.some(s => s.includes('registration preserved')));
});

test('--force: a failed add aborts later removes and prints only constant retry commands', async () => {
  const init = { claude: { markets: [FOREIGN.claude], plugins: OLD }, codex: { markets: [FOREIGN.codex], plugins: [] } };
  const r = await force(['install', '--force', '--yes', '--harness', 'claude', '--harness', 'codex'], init, { fail: (h, a) => a[2] === 'add' && a[3] === 'NexaDuo/mARC' });
  assert.equal(r.code, 1);
  assert.deepEqual(wargs(r.calls).filter(s => s.startsWith('codex')), [], 'codex records no remove');
  assert.ok(r.logs.includes('claude: nexaduo is now unregistered: marketplace add failed'));
  assert.ok(r.logs.includes('claude plugin marketplace add NexaDuo/mARC') && r.logs.includes('claude plugin install marc@nexaduo --scope user'));
  assert.ok(r.logs.includes('claude: previous registration was: sourceType: github, source: Fork/mARC'));
  assert.ok(!r.logs.some(s => s.startsWith('claude plugin') && s.includes('Fork')));
  assert.ok(r.logs.includes('codex: skipped: earlier forced re-registration failed; nothing removed'));
  assert.ok(r.logs.includes('claude: FAILED') && r.logs.includes('codex: FAILED'));
});

test('--force: a failed reinstall after add prints the constant install retry', async () => {
  const r = await force(['install', '--force', '--yes', '--harness', 'claude'], { claude: { markets: [FOREIGN.claude], plugins: OLD } }, { fail: (h, a) => a[1] === 'install' });
  assert.equal(r.code, 1);
  assert.ok(r.logs.includes('claude plugin install marc@nexaduo --scope user'));
  assert.ok(r.logs.some(s => s.includes('the marketplace is registered')));
});

test('--force plan display: credentials redacted, controls stripped, query/fragment dropped, long values truncated', async () => {
  const row = url => ({ name: 'nexaduo', source: 'git', url });
  const plan = async url => (await force(['install', '--force', '--dry-run', '--harness', 'claude'], { claude: { markets: [row(url)], plugins: [] } })).logs.find(s => s.includes('--force will REMOVE'));
  const cred = await plan('https://user:s3cret@github.com/Fork/mARC.git?token=abc#frag');
  assert.ok(cred.includes('source: https://***@github.com/Fork/mARC.git?token=***#frag;'), cred);
  assert.ok(!/s3cret|abc/.test(cred));
  const ctl = await plan('https://github.com/Fork/m\u001b[31mA\u202eR\rC\n.git');
  assert.ok(ctl.includes('https://github.com/Fork/m'), ctl);
  assert.ok(!hasControl(ctl), ctl);
  const long = await plan(`https://github.com/Fork/${'a'.repeat(400)}`);
  assert.ok(long.includes(`${'a'.repeat(100)}${String.fromCharCode(0x2026)};`), long);
  assert.ok(long.length < 400);
});

test('--force Claude project scope reinstalls with cwd = the project; a relative or missing path is skipped with a warning', async t => {
  const dir = await temp(t);
  const plugins = [{ id: 'marc@nexaduo', version: '26.11.14', scope: 'project', projectPath: dir, enabled: true },
    { id: 'marc@nexaduo', version: '26.11.14', scope: 'local', projectPath: 'relative/dir', enabled: true },
    { id: 'marc@nexaduo', version: '26.11.14', scope: 'local', projectPath: path.join(dir, 'gone'), enabled: true }];
  const r = await force(['install', '--force', '--yes', '--harness', 'claude', '--scope', 'project'], { claude: { markets: [FOREIGN.claude], plugins } }, {}, { cwd: dir, platform: process.platform });
  const installs = r.calls.filter(([, a]) => a[1] === 'install');
  assert.equal(installs.length, 1);
  assert.deepEqual(installs[0][2], { cwd: dir });
  assert.ok(r.logs.some(s => s.includes('skipped local scope at relative/dir: invalid project path')), r.logs.join('\n'));
  assert.ok(r.logs.some(s => s.includes('gone: ENOENT')), r.logs.join('\n'));
  assert.equal(r.code, 1, 'a skipped scope is a failure');
});

test('--force Copilot reinstall uses plugin install', async () => {
  const r = await force(['install', '--force', '--yes', '--harness', 'copilot'], { copilot: { markets: [FOREIGN.copilot], plugins: [] } });
  assert.equal(r.code, 0, r.logs.join('\n'));
  assert.deepEqual(wargs(r.calls), ['copilot plugin marketplace remove nexaduo', 'copilot plugin marketplace add NexaDuo/mARC', 'copilot plugin install marc@nexaduo']);
});

test('without --force a mismatch is refused as before (no force lines, no mutating argv)', async () => {
  const r = await force(['install', '--yes', '--harness', 'claude'], { claude: { markets: [FOREIGN.claude], plugins: OLD } });
  assert.equal(r.code, 1); assert.deepEqual(wargs(r.calls), []);
  assert.ok(!r.logs.some(s => s.includes('--force will') || s.includes('--force:') || s.includes('--force refused')));
  assert.ok(r.logs.includes('claude: nexaduo is registered from a different or unverified source (sourceType: github, source: Fork/mARC); registration preserved; run npx @nexaduo/marc install --force'));
});

test('SEC-1: a failed verify re-list after remove aborts the run, prints retry commands and skips later removes', async () => {
  const init = { claude: { markets: [FOREIGN.claude], plugins: OLD }, codex: { markets: [FOREIGN.codex], plugins: [] } };
  const r = await force(['install', '--force', '--yes', '--harness', 'claude', '--harness', 'codex'], init,
    { list: (h, n, m) => { if (h === 'claude' && n >= 3) throw new Error('list down'); return m; } });
  assert.equal(r.code, 1);
  assert.deepEqual(wargs(r.calls), ['claude plugin marketplace remove nexaduo']);
  assert.ok(r.logs.includes('claude: previous registration was: sourceType: github, source: Fork/mARC'), r.logs.join('\n'));
  assert.ok(r.logs.includes('claude plugin marketplace add NexaDuo/mARC') && r.logs.includes('claude plugin install marc@nexaduo --scope user'));
  assert.ok(r.logs.includes('codex: skipped: earlier forced re-registration failed; nothing removed'));
});

for (const [name, hook, flag] of [['post-add re-list', (h, n, m) => { if (n >= 4) throw new Error('list down'); return m; }],
  ['reinstall', null, (h, a) => a[1] === 'install'], ['final plugin re-list', null, null]]) {
  test(`SEC-1: ${name} failing after a successful remove aborts later harnesses`, async () => {
    const init = { claude: { markets: [FOREIGN.claude], plugins: OLD }, codex: { markets: [FOREIGN.codex], plugins: [] } };
    let plist = 0;
    const hooks = hook ? { list: (h, n, m) => h === 'claude' ? hook(h, n, m) : m } :
      flag ? { fail: (h, a) => h === 'claude' && flag(h, a) } :
        { fail: (h, a) => h === 'claude' && a[1] === 'list' && ++plist >= 3 };
    const r = await force(['install', '--force', '--yes', '--harness', 'claude', '--harness', 'codex'], init, hooks);
    assert.equal(r.code, 1);
    assert.ok(!wargs(r.calls).some(s => s.startsWith('codex')), wargs(r.calls).join('|'));
    assert.ok(r.logs.some(s => s.startsWith('claude: previous registration was:')), r.logs.join('\n'));
    assert.ok(r.logs.includes('claude plugin install marc@nexaduo --scope user'), r.logs.join('\n'));
  });
}

for (const h of ['claude', 'codex']) for (const command of ['install']) {
  test(`REV-1: ${h} ${command} --force moves an install that survived the remove onto the new source`, async () => {
    const plugins = h === 'codex' ? [{ pluginId: 'marc@nexaduo', version: '26.11.14', installed: true, enabled: true }] : OLD;
    const r = await force([command, '--force', '--yes', '--harness', h], { [h]: { markets: [FOREIGN[h]], plugins } }, { survive: true });
    assert.equal(r.code, 0, r.logs.join('\n'));
    assert.ok(r.logs.some(s => /already installed at 26\.11\.25\.2/.test(s)) && r.logs.includes(`${h}: OK`), r.logs.join('\n'));
    const w = wargs(r.calls);
    assert.ok(w.indexOf(`${h} plugin marketplace add NexaDuo/mARC`) < w.findIndex(s => /plugin (install|update|add) marc/.test(s) && !/marketplace/.test(s)), w.join('|'));
    assert.ok(w.some(s => s.startsWith(`${h} plugin ${h === 'codex' ? 'add' : 'install'} marc@nexaduo`)), w.join('|'));
  });
}

test('SEC-2: one redaction covers describeSource, the non-force refusal, the result table and the force plan', async () => {
  const url = 'https://user:tok@github.com/x?token=abc';
  const row = { name: 'nexaduo', source: 'git', url };
  assert.equal(redactSource(`${url}#v1`), 'https://***@github.com/x?token=***#v1');
  assert.equal(describeSource(row), 'sourceType: git, source: https://***@github.com/x?token=***');
  let r = await force(['install', '--yes', '--harness', 'claude'], { claude: { markets: [row], plugins: OLD } });
  for (const s of [...r.logs]) assert.ok(!/:tok@|token=abc/.test(s), s);
  assert.ok(r.logs.some(s => s.includes('https://***@github.com/x')));
  r = await force(['install', '--force', '--force-local', '--yes', '--harness', 'claude'], { claude: { markets: [row], plugins: OLD } }, { fail: (h, a) => a[2] === 'add' });
  for (const s of r.logs) assert.ok(!/:tok@|token=abc/.test(s), s);
});

test('REV-2: the skip warning prints the project path home-relative', async t => {
  const dir = await temp(t);
  const plugins = [{ id: 'marc@nexaduo', version: '26.11.14', scope: 'project', projectPath: dir, enabled: true }];
  const r = await force(['install', '--force', '--yes', '--harness', 'claude', '--scope', 'project'], { claude: { markets: [FOREIGN.claude], plugins } },
    { survive: false, list: (h, n, m) => { if (n === 3) rmSync(dir, { recursive: true, force: true }); return m; } }, { cwd: dir, home: path.dirname(dir), platform: process.platform });
  assert.ok(r.logs.some(s => /skipped project scope at ~\//.test(s)), r.logs.join('\n'));
});

test('REV-4: a refused --force with marc absent reports not installed (Claude and Windows root-only Codex)', async () => {
  for (const [h, market] of [['claude', { name: 'nexaduo', source: 'directory', path: '/home/user/mARC' }], ['codex', WIN_CODEX]]) {
    const r = await force(['install', '--force', '--yes', '--harness', h], { [h]: { markets: [market], plugins: [] } });
    assert.equal(r.code, 1);
    assert.ok(r.logs.some(s => s.includes('--force refused:')));
    assert.ok(r.logs.some(s => /not installed; /.test(s)) || r.logs.includes(`${h}: FAILED`) && r.logs.some(s => s === `${h}: marc not installed`), r.logs.join('\n'));
    assert.deepEqual(wargs(r.calls), []);
  }
});

test('REV-5: two raw sources that sanitize identically still abort after consent', async () => {
  const a = { name: 'nexaduo', source: 'git', url: 'https://github.com/Fork/m?a=1' }, b = { ...a, url: 'https://github.com/Fork/m?a=2' };
  const r = await force(['install', '--force', '--yes', '--harness', 'claude'], { claude: { markets: [a], plugins: OLD } },
    { list: (h, n, m) => n >= 2 ? [b] : m });
  assert.equal(r.code, 1); assert.deepEqual(wargs(r.calls), []);
  assert.ok(r.logs.includes('claude: registration changed since the plan was shown; nothing removed'));
});

const EVIL = { name: 'nexaduo', source: 'github', repo: 'Evil/x' };
const noInstall = c => wargs(c).filter(s => !/marketplace (remove|add)/.test(s));

test('SEC-3: an add that leaves nexaduo on a foreign source aborts before any install or update', async () => {
  for (const command of ['install', 'update']) {
    const init = { claude: { markets: [FOREIGN.claude], plugins: OLD }, codex: { markets: [FOREIGN.codex], plugins: [] } };
    const r = await force([command, '--force', '--yes', '--harness', 'claude', '--harness', 'codex'], init, { add: () => [EVIL] });
    assert.equal(r.code, 1, r.logs.join('\n'));
    assert.deepEqual(wargs(r.calls), ['claude plugin marketplace remove nexaduo', 'claude plugin marketplace add NexaDuo/mARC']);
    assert.deepEqual(noInstall(r.calls), []);
    assert.ok(r.logs.includes('claude: FAILED') && r.logs.some(s => /not a verified NexaDuo\/mARC registration after add/.test(s)), r.logs.join('\n'));
    assert.ok(r.logs.includes('claude: previous registration was: sourceType: github, source: Fork/mARC'));
    assert.ok(r.logs.includes('claude plugin marketplace remove nexaduo') && r.logs.includes('claude plugin install marc@nexaduo --scope user'));
    assert.ok(r.logs.includes('codex: skipped: earlier forced re-registration failed; nothing removed'));
  }
});

test('SEC-3: nexaduo flipping to a foreign source after the reinstall is FAILED, not OK', async () => {
  const r = await force(['install', '--force', '--yes', '--harness', 'claude'], { claude: { markets: [FOREIGN.claude], plugins: OLD } },
    { list: (h, n, m) => n >= 5 ? [EVIL] : m });
  assert.equal(r.code, 1, r.logs.join('\n'));
  assert.ok(r.logs.includes('claude: FAILED') && !r.logs.includes('claude: OK'), r.logs.join('\n'));
  assert.ok(r.logs.some(s => /not a verified NexaDuo\/mARC registration after the reinstall/.test(s)), r.logs.join('\n'));
});

test('REV-6: redaction keeps #ref and query structure; only credentials and query values change', async () => {
  assert.equal(redactSource('GitHub: NexaDuo/mARC#v1'), 'GitHub: NexaDuo/mARC#v1');
  assert.equal(redactSource('https://github.com/NexaDuo/mARC.git#v1'), 'https://github.com/NexaDuo/mARC.git#v1');
  assert.equal(redactSource('https://user:tok@github.com/x?token=abc#v1'), 'https://***@github.com/x?token=***#v1');
  assert.equal(redactSource('https://github.com/x?a=1&b=2&flag#v1'), 'https://github.com/x?a=***&b=***&flag#v1');
  assert.equal(redactSource('/home/u/dir#1?x=y'), '/home/u/dir#1?x=y');
  const pinned = { name: 'nexaduo', source: 'GitHub: NexaDuo/mARC#v1' };
  assert.ok(describeSource(pinned).endsWith('source: GitHub: NexaDuo/mARC#v1'));
  const codexPinned = { name: 'nexaduo', root: '/x', marketplaceSource: { sourceType: 'git', source: 'https://github.com/NexaDuo/mARC.git#v1' } };
  assert.equal(describeSource(codexPinned), 'sourceType: git, source: https://github.com/NexaDuo/mARC.git#v1');
  for (const [h, row] of [['copilot', pinned], ['codex', codexPinned]]) {
    const r = await force(['install', '--yes', '--harness', h], { [h]: { markets: [row], plugins: [] } });
    assert.ok(r.logs.some(s => s.includes('#v1') && s.includes('registration preserved')), r.logs.join('\n'));
  }
});

test('redaction: scp-style userinfo is masked, the conventional git user is not', () => {
  assert.equal(redactSource('tok@github.com:Fork/m.git'), '***@github.com:Fork/m.git');
  assert.equal(redactSource('user:tok@github.com:Fork/m.git'.replace('user:tok', 'ghp_abc')), '***@github.com:Fork/m.git');
  assert.equal(redactSource('git@github.com:Fork/mARC.git'), 'git@github.com:Fork/mARC.git');
  assert.equal(redactSource('ssh://git@host/Fork/m.git'), 'ssh://git@host/Fork/m.git');
});

test('REV-7: a survivor at another scope is updated, and its failure prints the matching update command in the retry block', async t => {
  const dir = await temp(t);
  const plugins = [...OLD, { id: 'marc@nexaduo', version: '26.11.14', scope: 'project', projectPath: dir, enabled: true }];
  const r = await force(['install', '--force', '--yes', '--harness', 'claude'], { claude: { markets: [FOREIGN.claude], plugins } },
    { survive: true, fail: (h, a) => a[1] === 'update' && a[2] === 'marc@nexaduo' }, { cwd: dir });
  assert.equal(r.code, 1, r.logs.join('\n'));
  assert.ok(r.logs.includes('claude plugin update marc@nexaduo --scope project'), r.logs.join('\n'));
});

test('SEC-4: a skipped scope and a failed re-disable appear in the retry block without aborting later harnesses', async () => {
  const plugins = [{ id: 'marc@nexaduo', version: '26.11.14', scope: 'user', enabled: false }, { id: 'marc@nexaduo', version: '26.11.14', scope: 'project', projectPath: 'relative/dir', enabled: true }];
  const init = { claude: { markets: [FOREIGN.claude], plugins }, codex: { markets: [FOREIGN.codex], plugins: [] } };
  const r = await force(['install', '--force', '--yes', '--harness', 'claude', '--harness', 'codex'], init, { fail: (h, a) => a[1] === 'disable' });
  assert.equal(r.code, 1, r.logs.join('\n'));
  assert.ok(r.logs.includes('claude: FAILED'));
  assert.ok(r.logs.includes('claude plugin install marc@nexaduo --scope project'), r.logs.join('\n'));
  assert.ok(r.logs.includes('claude plugin disable marc@nexaduo --scope user'), r.logs.join('\n'));
  assert.ok(wargs(r.calls).includes('codex plugin marketplace remove nexaduo'), 'no global abort');
});

// ---- converging install, single --force, hints, README parity (#451) ----
const FORCE_HINT = 'registration preserved; run npx @nexaduo/marc install --force';
const hintCases = [
  ['foreign remote', 'claude', FOREIGN.claude, 'sourceType: github, source: Fork/mARC'],
  ['pinned ref', 'claude', { ...GOOD.claude, ref: 'v1' }, 'sourceType: github, source: NexaDuo/mARC'],
  ['local checkout', 'claude', LOCAL.claude, 'sourceType: directory, source: ~/mARC'],
  ['root-only Codex', 'codex', WIN_CODEX, 'registered from local root C:\\repos\\example\\mARC'],
  ['unverifiable (no source)', 'codex', { name: 'nexaduo', root: '/x' }, 'registered from local root /x'],
  ['copilot foreign', 'copilot', FOREIGN.copilot, 'source: GitHub: Fork/mARC'],
];
for (const [name, h, market, desc] of hintCases) {
  test(`hint (${name}): the refusal names the exact next step in the log and the result DETAIL, and runs no write`, async () => {
    for (const env of [{}, { FORCE_COLOR: '1' }]) {
      const r = await force(['install', '--yes', '--harness', h], { [h]: { markets: [market], plugins: [] } }, {}, { env });
      assert.equal(r.code, 1); assert.deepEqual(wargs(r.calls), []);
      const text = r.logs.map(l => l.replace(SGR, ''));
      assert.ok(text.some(l => l.endsWith(`(${desc}); ${FORCE_HINT}`)), text.join('\n'));
      if (env.FORCE_COLOR) assert.ok(text.some(l => /^ {2}\S+ +FAILED +/.test(l) && l.endsWith(FORCE_HINT)), text.join('\n'));
      assert.ok(text.some(l => l.includes(`${h} plugin marketplace remove nexaduo`)), 'manual remedy stays');
    }
  });
}

test('hint: several nexaduo registrations never suggest --force, with or without it', async () => {
  for (const flags of [[], ['--force']]) {
    const r = await force(['install', '--yes', '--harness', 'codex', ...flags], { codex: { markets: [GOOD.codex, FOREIGN.codex], plugins: [] } });
    assert.equal(r.code, 1); assert.deepEqual(wargs(r.calls), []);
    assert.ok(r.logs.some(s => s.includes('remove the extra nexaduo registrations manually')), r.logs.join('\n'));
    assert.ok(!r.logs.some(s => s.includes('install --force')));
  }
});

test('hint: a run that already has --force never prints the --force hint', async () => {
  const cases = [[LOCAL.claude, {}], [FOREIGN.claude, {}], [LOCAL.claude, { isTTY: true, confirmLocal: async () => '' }]];
  for (const [market, extra] of cases) {
    const r = await force(['install', '--force', ...(extra.isTTY ? [] : ['--yes']), '--harness', 'claude'], { claude: { markets: [market], plugins: [] } }, {}, { confirm: async () => true, ...extra });
    assert.ok(!r.logs.some(s => s.includes('; run npx @nexaduo/marc install --force')), r.logs.join('\n'));
  }
});

test('install converges: missing installs, stale updates (requested scope only), current is a no-op', async () => {
  for (const h of ['claude', 'copilot', 'codex']) {
    let r = await force(['install', '--yes', '--harness', h], { [h]: { markets: [GOOD[h]], plugins: [] } });
    assert.equal(r.code, 0); assert.deepEqual(wargs(r.calls).map(s => s.replace(/ --scope user$/, '')), [`${h} plugin ${h === 'codex' ? 'add' : 'install'} marc@nexaduo`]);
    r = await force(['install', '--yes', '--harness', h], { [h]: { markets: [], plugins: [] } });
    assert.equal(r.code, 0); assert.equal(wargs(r.calls)[0], `${h} plugin marketplace add NexaDuo/mARC`);
    const old = h === 'codex' ? [{ pluginId: 'marc@nexaduo', version: '26.11.14', installed: true, enabled: true }] : h === 'copilot' ? [{ name: 'marc', marketplace: 'nexaduo', version: '26.11.14' }] : OLD;
    r = await force(['install', '--yes', '--harness', h], { [h]: { markets: [GOOD[h]], plugins: old } });
    assert.equal(r.code, 0, r.logs.join('\n'));
    assert.deepEqual(wargs(r.calls).map(s => s.replace(' --scope user', '')), [`${h} plugin marketplace ${h === 'codex' ? 'upgrade' : 'update'} nexaduo`,
      h === 'codex' ? 'codex plugin add marc@nexaduo' : `${h} plugin update ${h === 'claude' ? 'marc@nexaduo' : 'marc'}`]);
    assert.ok(r.logs.includes(`${h}: already installed at 26.11.25.2`) && r.logs.includes(`${h}: OK`));
  }
  const current = [{ id: 'marc@nexaduo', version: '26.11.25.2', scope: 'user', enabled: true }];
  const r = await force(['install', '--yes', '--harness', 'claude'], { claude: { markets: [GOOD.claude], plugins: current } });
  assert.equal(r.code, 0); assert.deepEqual(wargs(r.calls), []); assert.ok(!r.logs.some(s => s.includes('--force')));
});

test('install converges: a stale row at another scope gets a hint and no write; the requested scope is updated with its cwd', async t => {
  const dir = await temp(t), other = path.join(dir, 'other'); await mkdir(other);
  const plugins = [{ id: 'marc@nexaduo', version: '26.11.25.2', scope: 'user', enabled: true },
    { id: 'marc@nexaduo', version: '26.11.14', scope: 'project', projectPath: other, enabled: true }];
  const r = await force(['install', '--yes', '--harness', 'claude'], { claude: { markets: [GOOD.claude], plugins } }, {}, { cwd: dir, home: dir, platform: process.platform });
  assert.equal(r.code, 0, r.logs.join('\n')); assert.deepEqual(wargs(r.calls), []);
  assert.ok(r.logs.includes('claude: marc is also out of date at project scope in ~/other; run npx @nexaduo/marc install --scope project from that directory'), r.logs.join('\n'));
  const own = [{ id: 'marc@nexaduo', version: '26.11.14', scope: 'project', projectPath: dir, enabled: true }];
  const u = await force(['install', '--yes', '--harness', 'claude', '--scope', 'project'], { claude: { markets: [GOOD.claude], plugins: own } }, {}, { cwd: dir });
  assert.deepEqual(wargs(u.calls), ['claude plugin marketplace update nexaduo', 'claude plugin update marc@nexaduo --scope project']);
  assert.equal(u.calls.find(([, a]) => a[1] === 'update')[2]?.cwd, dir);
});

test('install reads plugin and marketplace lists before the plan and consent; a failed read changes nothing', async () => {
  for (const failing of ['plugin list --json', 'plugin marketplace list --json']) {
    const calls = [], logs = [];
    const run = forceFake(calls, { claude: { markets: [GOOD.claude], plugins: OLD } }, { fail: (h, a) => a.join(' ') === failing });
    const code = await main(['install', '--yes', '--harness', 'claude'], { release, run, log: s => logs.push(s), isTTY: false, env: {}, stdoutIsTTY: false, home: HOME });
    assert.equal(code, 1); assert.deepEqual(wargs(calls), []); assert.ok(logs.includes('claude: FAILED'));
  }
  const calls = [], logs = [];
  await main(['install', '--dry-run', '--harness', 'claude'], { release, run: forceFake(calls, { claude: { markets: [GOOD.claude], plugins: OLD } }), log: s => logs.push(s), isTTY: false, env: {}, stdoutIsTTY: false, home: HOME });
  assert.ok(logs.includes('claude plugin marketplace update nexaduo') && logs.includes('claude plugin update marc@nexaduo --scope user'), 'the update argv is shown before consent');
  assert.equal(wargs(calls).length, 0);
});

test('update is a deprecated alias of install: same argv, a deprecation note, exit codes unchanged', async () => {
  for (const h of ['claude', 'codex']) {
    const old = h === 'codex' ? [{ pluginId: 'marc@nexaduo', version: '26.11.14', installed: true, enabled: true }] : OLD;
    const a = await force(['install', '--yes', '--harness', h], { [h]: { markets: [GOOD[h]], plugins: old } });
    const b = await force(['update', '--yes', '--harness', h], { [h]: { markets: [GOOD[h]], plugins: old } });
    assert.deepEqual(b.calls.map(([x, y]) => [x, y]), a.calls.map(([x, y]) => [x, y])); assert.equal(b.code, a.code);
    assert.ok(b.logs.some(s => /^update is deprecated: it is now an alias for install/.test(s)));
    assert.ok(!a.logs.some(s => /deprecated/.test(s)));
  }
});

test('update on a root-only Codex registration never runs marketplace upgrade (refuse, force without a TTY, typed replace)', async () => {
  const init = () => ({ codex: { markets: [WIN_CODEX], plugins: [] } });
  const anyUpgrade = r => r.calls.some(([, a]) => a.includes('upgrade'));
  let r = await force(['update', '--yes', '--harness', 'codex'], init());
  assert.equal(r.code, 1); assert.ok(!anyUpgrade(r)); assert.deepEqual(wargs(r.calls), []);
  assert.ok(r.logs.some(s => s.includes(`unverified source (registered from local root C:\\repos\\example\\mARC); ${FORCE_HINT}`)), r.logs.join('\n'));
  r = await force(['update', '--force', '--yes', '--harness', 'codex'], init());
  assert.equal(r.code, 1); assert.ok(!anyUpgrade(r)); assert.deepEqual(wargs(r.calls), []);
  const t = tty(['replace']);
  r = await force(['update', '--force', '--harness', 'codex'], init(), {}, t.extra);
  assert.equal(r.code, 0, r.logs.join('\n')); assert.ok(!anyUpgrade(r));
  assert.deepEqual(wargs(r.calls).slice(0, 2), ['codex plugin marketplace remove nexaduo', 'codex plugin marketplace add NexaDuo/mARC']);
});

// ---- fix round 1 (#451): re-read before write, refusal in the plan, pre-read failures, catalog convergence ----
const CURRENT = [{ id: 'marc@nexaduo', version: '26.11.25.2', scope: 'user', enabled: true }];
const afterConsent = (h, to) => ({ list: (x, reads, markets) => reads >= 2 ? to : markets });
const noWrite = r => assert.deepEqual(wargs(r.calls), [], r.logs.join('\n'));

test('SEC-1: the registration changing between consent and the first write blocks every non-force write', async () => {
  const cases = [
    ['fresh install, nothing registered', 'claude', { markets: [], plugins: [] }, [GOOD.claude]],
    ['registered but not installed', 'claude', { markets: [GOOD.claude], plugins: [] }, [FOREIGN.claude]],
    ['stale update', 'claude', { markets: [GOOD.claude], plugins: OLD }, [FOREIGN.claude]],
    ['stale update (codex)', 'codex', { markets: [GOOD.codex], plugins: [{ pluginId: 'marc@nexaduo', version: '26.11.14', installed: true, enabled: true }] }, [FOREIGN.codex]],
  ];
  for (const [name, h, init, to] of cases) {
    const r = await force(['install', '--yes', '--harness', h], { [h]: init }, afterConsent(h, h === 'codex' ? to : to));
    assert.equal(r.code, 1, name); noWrite(r);
    assert.ok(r.logs.some(l => l.includes('registration changed since the plan was shown; nothing was written; rerun')), `${name}: ${r.logs.join('|')}`);
    assert.ok(r.logs.includes(`${h}: FAILED`), name);
  }
});

test('SEC-1: a plugin-list change between consent and the first write also blocks the write', async () => {
  const calls = [], logs = [];
  const base = forceFake(calls, { claude: { markets: [GOOD.claude], plugins: OLD } });
  let lists = 0;
  const run = async (h, a, o) => (a.join(' ') === 'plugin list --json' && ++lists === 2) ? JSON.stringify([{ ...OLD[0], version: '26.11.20' }]) : base(h, a, o);
  const code = await main(['install', '--yes', '--harness', 'claude'], { release, run, log: s => logs.push(s), isTTY: false, env: {}, stdoutIsTTY: false, home: HOME });
  assert.equal(code, 1); noWrite({ calls, logs });
  assert.ok(logs.some(l => l.includes('registration changed since the plan')));
});

test('SEC-2: without --force a known refusal is shown in the plan instead of the add/install argv; all-refused skips the prompt', async () => {
  let asked = 0;
  const r = await force(['install', '--harness', 'claude'], { claude: { markets: [FOREIGN.claude], plugins: [] } }, {}, { isTTY: true, confirm: async () => { asked++; return true; } });
  assert.equal(asked, 0); assert.equal(r.code, 1); noWrite(r);
  const planned = r.logs.slice(0, r.logs.indexOf('claude: Manual remedy (review first; this removes the marketplace registration):'));
  assert.ok(planned.some(l => l.includes(FORCE_HINT)), planned.join('|'));
  assert.ok(!planned.some(l => l.includes('plugin install') || l.includes('marketplace add')), planned.join('|'));
  assert.ok(r.logs.includes('claude: FAILED'));
});

test('REV-2: a no-op run skips the prompt and exits 0; a mix with one harness to write still prompts', async () => {
  let asked = 0;
  const confirm = async () => { asked++; return false; };
  let r = await force(['install', '--harness', 'claude'], { claude: { markets: [GOOD.claude], plugins: CURRENT } }, {}, { isTTY: true, confirm });
  assert.equal(asked, 0); assert.equal(r.code, 0); noWrite(r);
  r = await force(['install', '--harness', 'claude', '--harness', 'codex'], { claude: { markets: [FOREIGN.claude], plugins: [] }, codex: { markets: [], plugins: [] } }, {}, { isTTY: true, confirm });
  assert.equal(asked, 1, 'a harness that would write still needs consent'); assert.equal(r.code, 1); noWrite(r);
  r = await force(['install', '--harness', 'claude', '--harness', 'codex'], { claude: { markets: [FOREIGN.claude], plugins: [] }, codex: { markets: [GOOD.codex], plugins: [] } }, {}, { isTTY: true, confirm });
  assert.equal(asked, 2, 'registered but not installed is a write');
});

test('REV-3: without --force a failed pre-read warns in the plan and --dry-run exits non-zero', async () => {
  for (const failing of ['plugin list --json', 'plugin marketplace list --json']) {
    const hooks = { fail: (h, a) => a.join(' ') === failing };
    const r = await force(['install', '--dry-run', '--harness', 'claude'], { claude: { markets: [GOOD.claude], plugins: OLD } }, hooks);
    assert.equal(r.code, 1, failing); noWrite(r);
    assert.ok(r.logs.some(l => l.startsWith('claude: cannot read registration state: native failure')), r.logs.join('|'));
    assert.ok(!r.logs.some(l => l.includes('marketplace add')), 'no static add argv for an unreadable harness');
  }
  const ok = await force(['install', '--dry-run', '--harness', 'claude'], { claude: { markets: [GOOD.claude], plugins: OLD } });
  assert.equal(ok.code, 0);
});

// Catalog behind: the version is current but the catalog pointer tracks main.
const behindSrc = (folder, ref) => ({ source: 'git-subdir', url: 'NexaDuo/mARC', path: `harnesses/${folder}/marc`, ref });
const behindRows = { claude: [{ ...CURRENT[0], source: behindSrc('claude-code', 'main') }],
  codex: [{ pluginId: 'marc@nexaduo', version: '26.11.25.2', installed: true, enabled: true, source: behindSrc('codex', 'main') }] };
const fixCatalog = (run, onWrite) => async (h, a, o) => { const out = await run(h, a, o);
  return a[1] === 'list' && onWrite.fixed ? out.replaceAll('"ref":"main"', `"ref":"${release.tag}"`) : out; };
for (const h of ['claude', 'codex']) {
  test(`REV-1 (${h}): current version with the catalog behind converges via marketplace update + plugin update, then verifies OK`, async () => {
    const calls = [], logs = [], gate = { fixed: false };
    const run = fixCatalog(forceFake(calls, { [h]: { markets: [GOOD[h]], plugins: behindRows[h] } }), gate);
    const dry = await main(['install', '--dry-run', '--harness', h], { release, run, log: s => logs.push(s), isTTY: false, env: {}, stdoutIsTTY: false, home: HOME });
    assert.equal(dry, 0); assert.equal(wargs(calls).length, 0);
    assert.ok(logs.includes(`${h} plugin marketplace ${h === 'codex' ? 'upgrade' : 'update'} nexaduo`), logs.join('|'));
    assert.ok(logs.some(l => l.includes('catalog is behind the release')));
    const live = [], out = [];
    const run2 = fixCatalog(async (x, a, o) => { if (a[1] === 'update' || a[1] === 'upgrade' || (a[1] === 'add' && a[2] === 'marc@nexaduo')) gate.fixed = true; return forceFake(live, { [h]: { markets: [GOOD[h]], plugins: behindRows[h] } })(x, a, o); }, gate);
    gate.fixed = false;
    const code = await main(['install', '--yes', '--harness', h], { release, run: run2, log: s => out.push(s), isTTY: false, env: {}, stdoutIsTTY: false, home: HOME });
    assert.equal(code, 0, out.join('\n'));
    assert.deepEqual(wargs(live).map(s => s.replace(' --scope user', '')), [`${h} plugin marketplace ${h === 'codex' ? 'upgrade' : 'update'} nexaduo`,
      h === 'codex' ? 'codex plugin add marc@nexaduo' : 'claude plugin update marc@nexaduo']);
    assert.ok(out.includes(`${h}: OK`), out.join('\n'));
  });
}

test('REV-1: when the catalog is still behind after the update, install fails with the manual remedy instead of reporting OK', async () => {
  const r = await force(['install', '--yes', '--harness', 'claude'], { claude: { markets: [GOOD.claude], plugins: behindRows.claude } });
  assert.equal(r.code, 1, r.logs.join('\n'));
  assert.ok(r.logs.some(l => l.includes('catalog still does not point at the release')), r.logs.join('\n'));
  assert.ok(r.logs.includes('claude plugin marketplace remove nexaduo') && r.logs.includes('claude: FAILED'));
  assert.deepEqual(wargs(r.calls), ['claude plugin marketplace update nexaduo', 'claude plugin update marc@nexaduo --scope user']);
});

// ---- #458: plan/hint polish + doctor env injection ----
test('SEC-3 (#458): an already-current harness shows `already current`, not its add/install argv', async () => {
  const r = await force(['install', '--dry-run', '--harness', 'claude'], { claude: { markets: [GOOD.claude], plugins: CURRENT } });
  assert.equal(r.code, 0); noWrite(r);
  assert.ok(r.logs.includes('claude: already current'), r.logs.join('|'));
  assert.ok(!r.logs.some(l => l.includes('plugin install') || l.includes('marketplace add')), r.logs.join('|'));
});

test('REV-5 (#458): an all-refused run prints the refusal sentence once, and the result detail keeps it', async () => {
  const r = await force(['install', '--yes', '--harness', 'claude'], { claude: { markets: [FOREIGN.claude], plugins: [] } });
  assert.equal(r.code, 1); noWrite(r);
  assert.equal(r.logs.filter(l => l.includes(FORCE_HINT)).length, 1, r.logs.join('\n'));
  const col = await force(['install', '--yes', '--harness', 'claude'], { claude: { markets: [FOREIGN.claude], plugins: [] } }, {}, { env: { FORCE_COLOR: '1' } });
  const text = col.logs.map(l => l.replace(SGR, ''));
  assert.equal(text.filter(l => l.includes('registered from a different') && !/FAILED/.test(l)).length, 1, text.join('\n'));
  assert.ok(text.some(l => /^ {2}\S+ +FAILED +/.test(l) && l.endsWith(FORCE_HINT)), text.join('\n'));
});

test('REV-6 (#458): the catalog-still-behind remedy is conditional and points at doctor', async () => {
  const r = await force(['install', '--yes', '--harness', 'claude'], { claude: { markets: [GOOD.claude], plugins: behindRows.claude } });
  assert.equal(r.code, 1);
  const line = r.logs.find(l => l.includes('catalog still does not point at the release'));
  assert.ok(line, r.logs.join('\n'));
  assert.ok(/only helps if the upstream catalog already carries the release pointer/.test(line), line);
  assert.ok(line.includes('npx @nexaduo/marc doctor'), line);
  assert.ok(!line.includes('re-registers it'), line);
});

test('doctor env (#458): readCatalogSource uses the injected home, not process.env', async t => {
  const dir = await temp(t), root = path.join(dir, '.copilot/.cache/copilot/marketplaces/NexaDuo-mARC');
  await mkdir(path.join(root, '.github/plugin'), { recursive: true });
  const source = { source: 'github', repo: 'NexaDuo/mARC', ref: release.tag, path: 'harnesses/copilot/marc' };
  await writeFile(path.join(root, '.github/plugin/marketplace.json'), JSON.stringify({ name: 'nexaduo', plugins: [{ name: 'marc', source }] }));
  const plugins = [{ name: 'marc', marketplace: 'nexaduo', version: release.tag.replace(/^v/, ''), enabled: true, source: 'live' }];
  const saved = { HOME: process.env.HOME, USERPROFILE: process.env.USERPROFILE, COPILOT_HOME: process.env.COPILOT_HOME };
  process.env.HOME = process.env.USERPROFILE = path.join(dir, 'elsewhere'); delete process.env.COPILOT_HOME;
  t.after(() => { for (const [k, v] of Object.entries(saved)) v === undefined ? delete process.env[k] : process.env[k] = v; });
  const r = await force(['doctor', '--harness', 'copilot'], { copilot: { markets: [GOOD.copilot], plugins } }, {}, { home: dir });
  assert.ok(!r.logs.some(l => l.includes('catalog pointer unavailable')), r.logs.join('\n'));
  assert.equal(r.code, 0, r.logs.join('\n'));
  assert.ok(r.logs.includes('copilot: OK'), r.logs.join('\n'));
});

test('README and HELP parity: every flag in --help appears in the root and launcher READMEs; hidden flags are not documented', async () => {
  const logs = [];
  assert.equal(await main(['--help'], { release, log: s => logs.push(s) }), 0);
  const flags = [...new Set(logs.join('\n').match(/--[a-z][a-z-]*/g))];
  for (const f of ['--yes', '--dry-run', '--harness', '--scope', '--version', '--force']) assert.ok(flags.includes(f), `HELP lists ${f}`);
  assert.ok(!flags.includes('--force-local'));
  for (const file of ['README.md', 'launcher/README.md']) {
    const text = await readFile(path.join(ROOT, file), 'utf8');
    for (const f of flags) assert.ok(text.includes(f), `${file} documents ${f}`);
    assert.ok(!text.includes('--force-local'), `${file} must not document the hidden alias`);
  }
  assert.ok((await readFile(path.join(ROOT, 'README.md'), 'utf8')).includes('npx @nexaduo/marc install --force'));
});

test('banner (#453): shown only for color + 40+ columns + unicode; plain output unchanged', () => {
  const html = readFileSync(new URL('../../docs/index.html', import.meta.url), 'utf8').split('\n');
  const s = html.findIndex(l => l.trimStart().startsWith('█████╗'));
  const rows = html.slice(s, s + 6);
  const ind = Math.min(...rows.map(r => r.length - r.trimStart().length));
  const expected = rows.map(r => r.slice(ind).trimEnd());
  const header = `mARC ${release.tag} (${release.npm}); install`;
  const capture = ({ env, columns, platform = 'linux', stdoutIsTTY = true }) => {
    const logs = [];
    makeOut(l => logs.push(l), createStyle({ env, isTTY: stdoutIsTTY, platform }), { columns }).header(release, 'install');
    return logs;
  };
  const strip = l => l.replace(/\x1b\[[0-9;]*m/g, '');
  const color = { FORCE_COLOR: '3', COLORTERM: 'truecolor' };
  const on = capture({ env: color, columns: 80 });
  assert.equal(on.length, 8);
  assert.deepEqual(on.slice(0, 6).map(strip), expected, 'banner art equals docs/index.html');
  assert.equal(on[6], '');
  assert.equal(strip(on[7]), header);
  assert.match(on[0], /\x1b\[38;2;94;241;255m█+\x1b\[0m\x1b\[1;38;2;184;146;255m╗/);
  assert.equal(capture({ env: color, columns: 40 }).length, 8);
  for (const c of [{ env: color, columns: 39 }, { env: color, columns: undefined }, { env: color, columns: 80, platform: 'win32' },
    { env: { ...color, ANSICON: '1' }, columns: 80, platform: 'win32' }, { env: { ...color, LANG: 'C' }, columns: 80 },
    { env: { ...color, LC_ALL: 'POSIX', LANG: 'en_US.UTF-8' }, columns: 80 }]) {
    const r = capture(c);
    assert.equal(r.length, 1, JSON.stringify(c));
    assert.equal(strip(r[0]), header);
  }
  assert.equal(capture({ env: { ...color, WT_SESSION: 'x' }, columns: 80, platform: 'win32' }).length, 8);
  assert.equal(capture({ env: { ...color, LANG: 'en_US.UTF-8' }, columns: 80 }).length, 8);
  for (const c of [{ env: { NO_COLOR: '1', FORCE_COLOR: '3' } }, { env: { TERM: 'dumb' } }, { env: {}, stdoutIsTTY: false }]) {
    assert.deepEqual(capture({ ...c, columns: 200 }), [header], 'plain mode is byte-identical');
  }
});
