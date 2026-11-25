import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, writeFile, readdir, rm, symlink, chmod, lstat } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createStyle, stripControls } from '../lib/style.js';
import { main, displayProject, makeOut, parseArgs, installAgy, tempRoots, HARNESSES } from '../lib/main.js';
import { tagToNpm, npmToTag } from '../lib/version.js';
import { safeValue, spawnSpec, resolveExecutable, createRunner } from '../lib/process.js';
import { describeSource, safeVersion, parsePlugins, parseMarketplaces, marketplaceWarnings, marketplaceMatches, sourceHasPointer, readCatalogSource } from '../lib/lists.js';

const ROOT = fileURLToPath(new URL('../..', import.meta.url));
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
  return main(args, { release, run: runner(calls), log: silent, isTTY: false, env: {}, stdoutIsTTY: false, ...extra });
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
    update: [['plugin', 'list', '--json'], ['plugin', 'marketplace', 'update', 'nexaduo'],
      ['plugin', 'update', 'marc@nexaduo', '--scope', 'user'], ['plugin', 'update', 'marc@nexaduo', '--scope', 'project']],
    doctor: [['plugin', 'list', '--json'], ['plugin', 'marketplace', 'list', '--json']],
    uninstall: [['plugin', 'uninstall', 'marc@nexaduo', '--scope', 'user']],
  },
  copilot: {
    install: [['plugin', 'list', '--json'], ['plugin', 'marketplace', 'list', '--json']],
    update: [['plugin', 'marketplace', 'update', 'nexaduo'], ['plugin', 'update', 'marc']],
    doctor: [['plugin', 'list', '--json'], ['plugin', 'marketplace', 'list', '--json']], uninstall: [['plugin', 'uninstall', 'marc@nexaduo']],
  },
  codex: {
    install: [['plugin', 'list', '--json'], ['plugin', 'marketplace', 'list', '--json']],
    update: [['plugin', 'marketplace', 'upgrade', 'nexaduo'], ['plugin', 'add', 'marc@nexaduo']],
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
for (const scope of ['user', 'project', 'local']) for (const command of ['update', 'uninstall']) {
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
    assert.deepEqual(calls.map(c => c[1]), h !== 'agy' && ['install', 'update'].includes(command) && (h === 'claude' || command === 'install') ?
      [['plugin', 'list', '--json'], ['plugin', 'list', '--json']] : []);
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
  assert.equal(await invoke(['install', '--harness', 'claude'], calls, { isTTY: true, confirm: async () => false }), 1);
  // Only the read-only installed-list pre-read runs before consent.
  assert.deepEqual(calls.map(c => c[1]), [['plugin', 'list', '--json']]);
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
    assert.equal(await main([cmd, '--harness', h, '--harness', h, '--yes', '--version', release.tag], { release, run: async (...a) => { calls.push(a); return run(...a); }, log: silent, cwd, env: {}, stdoutIsTTY: false }), 0);
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
test('launcher source never requests a shell or removes a marketplace', async () => {
  for (const f of await readdir(path.join(ROOT, 'launcher/lib'))) {
    const source = await readFile(path.join(ROOT, 'launcher/lib', f), 'utf8');
    assert.doesNotMatch(source, /shell\s*:\s*(?:true|['"])/);
    assert.doesNotMatch(source, /['"]marketplace['"]\s*,\s*['"]remove['"]/);
  }
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

test('Claude update runs once per scope in each installed project', async t => {
  const dir = await temp(t), other = path.join(dir, 'other');
  await mkdir(other);
  const rows = [{ id: 'marc@nexaduo', scope: 'user' }, { id: 'marc@nexaduo', scope: 'local', projectPath: other },
    { id: 'marc@nexaduo', scope: 'local', projectPath: other }, { id: 'marc@nexaduo', scope: 'project', projectPath: other }];
  const calls = [];
  await invoke(['update', '--harness', 'claude', '--yes'], calls, { run: runner(calls, { claude: JSON.stringify(rows) }) });
  assert.equal(calls.length, 5);
  assert.equal(calls[3][2].cwd, other);
  assert.equal(calls[4][2].cwd, other);
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
    assert.deepEqual(calls.map(c => c[1]), [['plugin', 'list', '--json'], ['plugin', 'marketplace', 'list', '--json'],
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
      assert.ok(logs.some(s => s.includes('found ') && /sourceType: |source: /.test(s)));
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
    if (command === 'install') assert.ok(logs.some(s => s.includes('found sourceType: local, source: /path/to/clone')), logs.join('\n'));
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

test('absent requested Claude scope lists installed scopes, including during dry-run', async () => {
  for (const installed of [JSON.parse(fixtures.claude), []]) for (const dry of [true, false]) {
    const calls = [], logs = [];
    assert.equal(await invoke(['update', '--harness', 'claude', '--scope', 'local', ...(dry ? ['--dry-run'] : ['--yes'])], [],
      { run: runner(calls, { claude: JSON.stringify(installed) }), log: s => logs.push(s) }), 1);
    assert.ok(logs.includes(`claude: marc is not installed at scope local; installed scopes: ${installed.length ? 'user, project' : 'none'}`));
    assert.ok(!logs.some(s => s.includes('run install first')));
    assert.deepEqual(calls.map(c => c[1]), [['plugin', 'list', '--json']]);
  }
});

test('failed list or native command never aborts remaining harnesses', async () => {
  for (const command of ['install', 'update', 'doctor', 'uninstall']) {
    const calls = [], logs = [], native = runner(calls);
    const run = (h, args, options) => { if (h === 'claude') throw Error('fixture failure'); return native(h, args, options); };
    assert.equal(await invoke([command, '--harness', 'claude', '--harness', 'codex', '--yes'], [], { run, log: s => logs.push(s) }), 1);
    assert.ok(calls.some(([h]) => h === 'codex')); assert.ok(logs.includes('claude: FAILED')); assert.ok(logs.includes('codex: OK'));
  }
});

test('dry-run lists exactly the real update settings paths, respecting scope', async t => {
  const dir = await temp(t), other = path.join(dir, 'other'); await mkdir(other);
  const rows = JSON.stringify([{ id: 'marc@nexaduo', scope: 'local', projectPath: other }, { id: 'marc@nexaduo', scope: 'project', projectPath: dir }]);
  const paths = [];
  for (const dry of [true, false]) {
    const calls = [], logs = [];
    assert.equal(await invoke(['update', '--harness', 'claude', '--scope', 'local', ...(dry ? ['--dry-run'] : ['--yes'])], [],
      { run: runner(calls, { claude: rows }), log: s => logs.push(s) }), 0);
    paths.push(logs.filter(s => s.startsWith('Claude will edit')));
    if (dry) assert.deepEqual(calls.map(c => c[1]), [['plugin', 'list', '--json']]);
  }
  assert.deepEqual(paths[0], paths[1]); assert.deepEqual(paths[0], [`Claude will edit ${path.join(other, '.claude/settings.local.json')}`]);
});

test('deleted Claude project is skipped while valid scopes and other harnesses finish', async t => {
  const dir = await temp(t), missing = path.join(dir, 'deleted');
  const rows = JSON.stringify([{ id: 'marc@nexaduo', scope: 'project', projectPath: missing },
    { id: 'marc@nexaduo', scope: 'user' }, { id: 'marc@nexaduo', scope: 'local', projectPath: dir }]);
  const calls = [], logs = [];
  assert.equal(await invoke(['update', '--harness', 'claude', '--harness', 'codex', '--yes'], [],
    { run: runner(calls, { claude: rows }), log: s => logs.push(s) }), 1);
  assert.ok(logs.some(s => s.includes('warning: skipped project') && s.includes(missing)));
  assert.deepEqual(calls.filter(([h, a]) => h === 'claude' && a[1] === 'update').map(c => c[1].at(-1)), ['user', 'local']);
  assert.ok(logs.includes('codex: OK'));
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
  test(`install ${h} reports STALE for an existing install that differs, with no mutation`, async () => {
    const calls = [], logs = [];
    const rows = typeof stale[h] === 'string' ? stale[h] : JSON.stringify(stale[h]);
    assert.equal(await invoke(['install', '--harness', h, '--yes'], calls, { run: runner(calls, { [h]: rows }), log: s => logs.push(s) }), 1);
    assert.deepEqual(mutating(calls), []);
    assert.ok(logs.includes(`${h}: already installed at 26.11.14 (launcher release ${release.tag}); run \`npx @nexaduo/marc update\``), logs.join('\n'));
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
  assert.deepEqual(mutating(calls), []);
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
  assert.ok(logs.some(s => s.includes('found sourceType: local, source: /dev/clone[31mevil')), logs.join('|'));
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
  const rel = { tag: 'v26.11.25.5', sha: 'a'.repeat(40), npm: '26.1125.5' };
  const expected = ["mARC v26.11.25.5 (26.1125.5); install","claude plugin list --json","claude plugin marketplace list --json","claude plugin marketplace add NexaDuo/mARC","claude plugin install marc@nexaduo --scope user","claude plugin list --json","codex plugin list --json","codex plugin marketplace list --json","codex plugin marketplace add NexaDuo/mARC","codex plugin add marc@nexaduo","codex plugin list --json"];
  const argv = ['install', '--dry-run', '--harness', 'claude', '--harness', 'codex'];
  for (const [env, stdoutIsTTY] of [[{}, false], [{ TERM: 'dumb' }, true], [{ NO_COLOR: '1' }, true], [{ FORCE_COLOR: '0' }, true], [{ FORCE_COLOR: '3', NO_COLOR: '1' }, true]]) {
    const logs = [];
    assert.equal(await main(argv, { release: rel, run: async () => '[]', log: l => logs.push(l), isTTY: false, env, stdoutIsTTY, cwd: '/work/project' }), 0);
    assert.deepEqual(logs, expected);
    assert.ok(!logs.join('').includes(ESC));
  }
});

test('color mode re-lays out the same text: headings, prompt glyph, remedy block, result table', async () => {
  const rel = { tag: 'v26.11.25.5', sha: 'a'.repeat(40), npm: '26.1125.5' };
  const logs = [];
  const run = async (h, args) => args[1] === 'marketplace' && args[2] === 'list' ? JSON.stringify([{ name: 'nexaduo', source: 'github', repo: 'evil/x' }]) :
    args[1] === 'list' ? '[]' : '';
  const code = await main(['install', '--yes', '--harness', 'claude'],
    { release: rel, run, log: l => logs.push(l), isTTY: false, env: { FORCE_COLOR: '1' }, stdoutIsTTY: false });
  assert.equal(code, 1);
  const text = logs.map(l => l.replace(SGR, ''));
  assert.equal(text[0], 'mARC v26.11.25.5 (26.1125.5); install');
  assert.ok(text.includes('# claude'));
  assert.ok(text.includes('  $ claude plugin list --json'));
  assert.ok(text.some(l => l.startsWith('  +- claude: Manual remedy (review first;')));
  assert.ok(text.includes('  |  $ claude plugin marketplace remove nexaduo'));
  assert.ok(text.some(l => l.startsWith('  x claude: nexaduo is registered from a different')));
  assert.ok(text.includes('* result'));
  assert.ok(text.some(l => /^ {2}claude {3}FAILED {2}nexaduo is registered/.test(l)), text.join('|'));
  assert.ok(text.every(l => [...l].every(c => c.charCodeAt(0) < 128)), 'ASCII only');
  assert.ok(logs.some(l => l.includes(`${ESC}[1;95mFAILED`)));
});

test('doctor rows paint every column: hostile scope and version cannot reach the terminal', async () => {
  const BEL = String.fromCharCode(7), nl = String.fromCharCode(10);
  const hostile = `pro${ESC}[2Jject${ESC}]0;pwned${BEL}`;
  const rel = { tag: 'v26.11.25.5', sha: 'a'.repeat(40), npm: '26.1125.5' };
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
  const rel = { tag: 'v26.11.25.5', sha: 'a'.repeat(40), npm: '26.1125.5' };
  const cwd = os.tmpdir();
  const rows = JSON.stringify([{ id: 'marc@nexaduo', version: '26.11.25.5', scope: 'project', projectPath: cwd }]);
  let logs = [];
  const opts = { release: rel, log: l => logs.push(l), isTTY: false, env: { FORCE_COLOR: '1' }, stdoutIsTTY: false, cwd };
  assert.equal(await main(['update', '--yes', '--harness', 'claude'], { ...opts, run: async (h, a) => a[1] === 'list' ? rows : '' }), 0);
  let text = logs.map(l => l.replace(SGR, ''));
  assert.ok(text.some(l => l.startsWith('  ! Claude will edit')), 'pre-consent warning was shown');
  assert.ok(text.some(l => /^ {2}claude {3}OK {6}done$/.test(l)), text.join('|'));
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

const staleHint = 'run `npx @nexaduo/marc update`';
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
