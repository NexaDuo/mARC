import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, writeFile, readdir, rm, symlink, chmod } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { main, parseArgs, installAgy, HARNESSES } from '../lib/main.js';
import { tagToNpm, npmToTag } from '../lib/version.js';
import { safeValue, spawnSpec, resolveExecutable, createRunner } from '../lib/process.js';
import { parsePlugins, parseMarketplaces, marketplaceWarnings, readCatalogSource } from '../lib/lists.js';

const ROOT = fileURLToPath(new URL('../..', import.meta.url));
const release = { tag: 'v26.11.26', sha: 'a'.repeat(40), npm: '26.1126.0' };
const fixtures = {
  claude: JSON.stringify([{ id: 'marc@nexaduo', version: '26.11.26', scope: 'user', enabled: true, source: { ref: release.tag } },
    { id: 'marc@nexaduo', version: '26.11.26', scope: 'project', enabled: false, source: { ref: release.tag } }]),
  copilot: JSON.stringify([{ name: 'marc', marketplace: 'nexaduo', version: '26.11.26', enabled: true, source: { ref: release.tag } }]),
  codex: JSON.stringify({ installed: [{ pluginId: 'marc@nexaduo', version: '26.11.26', installed: true,
    enabled: true, source: { source: 'git-subdir', url: 'NexaDuo/mARC', path: 'harnesses/codex/marc', ref: release.tag } }] }),
  agy: JSON.stringify([{ name: 'marc', version: '26.11.26', enabled: true }]),
};
const markets = JSON.stringify([{ name: 'nexaduo', source: { source: 'github', repo: 'NexaDuo/mARC' } }]);
const silent = () => {};
function runner(calls, overrides = {}) {
  return async (h, args, options) => {
    calls.push([h, args, options]);
    return args[1] === 'marketplace' && args[2] === 'list' ? markets :
      args[1] === 'list' ? overrides[h] ?? fixtures[h] : '';
  };
}
function invoke(args, calls, extra = {}) {
  return main(args, { release, run: runner(calls), log: silent, isTTY: false, ...extra });
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
for (const tag of ['v0.9.0', 'v26.11.24', 'v26.01.1', 'v26.11.025', 'v26.11.26.01', 'v26.11.26.0', 'v27.2.29', 'v27.13.1', 'v26.11.26.1.2']) {
  test(`reject tag ${tag}`, () => assert.throws(() => tagToNpm(tag)));
}
for (const npm of ['26.1125.1.2', '26.01125.0', '26.1125.01', '26.1132.0', '26.1124.0']) {
  test(`reject npm ${npm}`, () => assert.throws(() => npmToTag(npm)));
}

const golden = {
  claude: {
    install: [['plugin', 'marketplace', 'add', 'NexaDuo/mARC'], ['plugin', 'install', 'marc@nexaduo', '--scope', 'user']],
    update: [['plugin', 'list', '--json'], ['plugin', 'marketplace', 'update', 'nexaduo'],
      ['plugin', 'update', 'marc@nexaduo', '--scope', 'user'], ['plugin', 'update', 'marc@nexaduo', '--scope', 'project']],
    doctor: [['plugin', 'list', '--json'], ['plugin', 'marketplace', 'list', '--json']],
    uninstall: [['plugin', 'uninstall', 'marc@nexaduo', '--scope', 'user']],
  },
  copilot: {
    install: [['plugin', 'marketplace', 'add', 'NexaDuo/mARC'], ['plugin', 'install', 'marc@nexaduo']],
    update: [['plugin', 'marketplace', 'update', 'nexaduo'], ['plugin', 'update', 'marc']],
    doctor: [['plugin', 'list', '--json'], ['plugin', 'marketplace', 'list', '--json']], uninstall: [['plugin', 'uninstall', 'marc@nexaduo']],
  },
  codex: {
    install: [['plugin', 'marketplace', 'add', 'NexaDuo/mARC'], ['plugin', 'add', 'marc@nexaduo']],
    update: [['plugin', 'marketplace', 'upgrade'], ['plugin', 'add', 'marc@nexaduo']],
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
for (const scope of ['user', 'project', 'local']) for (const command of ['install', 'update', 'uninstall']) {
  test(`Claude scope ${scope} ${command}`, async () => {
    const calls = [], logs = [];
    const rows = JSON.stringify([{ id: 'marc@nexaduo', scope, version: '26.11.26' }]);
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
    assert.deepEqual(calls, []);
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
test('interactive refusal has no child process', async () => {
  const calls = [];
  assert.equal(await invoke(['install', '--harness', 'claude'], calls, { isTTY: true, confirm: async () => false }), 1);
  assert.deepEqual(calls, []);
});

function agyRunner(calls, { mismatch = false, fail = false, enabled = true, fresh = false, tree = '', link = false, cloneFail = false, checkoutFail = false, wrongManifest = false } = {}) {
  return async (h, args, options) => {
    calls.push([h, args, options]);
    if (h === 'agy' && args[1] === 'list') return fresh ? '[]' : JSON.stringify([{ name: 'marc', enabled, version: '26.11.25' }]);
    if (h === 'git' && args[0] === 'clone') {
      if (cloneFail) throw Error('clone failed');
      const root = args.at(-1);
      await mkdir(path.join(root, 'harnesses/antigravity/marc'), { recursive: true });
      await writeFile(path.join(root, 'harnesses/antigravity/marc/plugin.json'), JSON.stringify({ version: wrongManifest ? '26.11.25' : '26.11.26' }));
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
    for (const [output, code] of [[fixtures[h], 0], [fixtures[h].replaceAll('26.11.26', '26.11.25'), 1], ['garbage', 1]]) {
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
  await writeFile(impl, `const fs = require('node:fs');\nconst [h,...a]=process.argv.slice(2);\nfs.appendFileSync(process.env.STUB_LOG,JSON.stringify([h,a])+'\\n');\nif(h==='git'&&a[0]==='clone'){const p=require('node:path').join(a.at(-1),'harnesses/antigravity/marc');fs.mkdirSync(p,{recursive:true});fs.writeFileSync(require('node:path').join(p,'plugin.json'),JSON.stringify({version:'26.11.26'}));}\nif(h==='git'&&a[0]==='rev-parse'){console.log('${release.sha}');process.exit(0);}\nconst f=JSON.parse(process.env.STUB_FIXTURES);\nconsole.log(a[1]==='marketplace'&&a[2]==='list'?process.env.STUB_MARKETS:a[1]==='list'?f[h]:'');\n`);
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
    clone) for dest do :; done; /bin/mkdir -p "$dest/harnesses/antigravity/marc"; printf '%s' '{"version":"26.11.26"}' > "$dest/harnesses/antigravity/marc/plugin.json" ;;
    rev-parse) printf '%s' '${release.sha}' ;;
  esac
  exit 0
fi
case "$1 $2 $3" in
  'plugin marketplace list') printf '%s\\n' '${markets}' ;;
  'plugin list'*) printf '%s\\n' '${fixtures[h]}' ;;
esac
`);
    }
    await chmod(file, 0o755);
  }
  const env = { ...process.env, PATH: bin, PATHEXT: '.CMD', HOME: home, USERPROFILE: home, STUB_LOG: logPath,
    STUB_FIXTURES: JSON.stringify(fixtures), STUB_MARKETS: markets };
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
    await main([cmd, '--harness', h, '--harness', h, '--yes', '--version', release.tag], { release, run: async (...a) => { calls.push(a); return run(...a); }, log: silent, cwd });
    expected.push(...golden[h][cmd].map(a => [h, a]));
  }
  for (const cmd of ['install', 'update']) {
    const calls = [];
    await main([cmd, '--harness', 'agy', '--yes'], { release, run: async (...a) => { calls.push(a); return run(...a); }, log: silent, cwd, tmpRoot: dir });
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
  assert.deepEqual(files, ['LICENSE', 'package.json', 'release.json', 'bin/marc.js', ...lib].sort());
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
    claude: [{ id: 'marc@nexaduo', version: '26.11.26', scope: 'user', enabled: true, readFromFolder: dir }],
    codex: { installed: [{ pluginId: 'marc@nexaduo', name: 'marc', marketplaceName: 'nexaduo', version: '26.11.26',
      installed: true, enabled: true, source: { source: 'local', path: dir } }] },
    copilot: [{ name: 'marc', marketplace: 'nexaduo', version: '26.11.26', enabled: true, source: 'live', installedFrom: dir }],
    agy: [{ name: 'marc', version: '26.11.26', enabled: false, source: 'antigravity', path: dir }],
  };
  for (const h of HARNESSES) assert.equal(parsePlugins(h, JSON.stringify(observed[h]))[0].version, '26.11.26');
  assert.equal(parsePlugins('copilot', 'Live Plugins (loaded from a local marketplace directory, never copied):\n  • marc@nexaduo (v26.11.26) (enabled)\n      from /tmp/catalog')[0].version, '26.11.26');
  for (const [h, location] of [['claude', '.claude-plugin'], ['copilot', '.github/plugin'], ['codex', '.agents/plugins']]) {
    await mkdir(path.join(dir, location), { recursive: true });
    const catalog = path.join(dir, location, 'marketplace.json');
    const source = { source: h === 'copilot' ? 'github' : 'git-subdir', ref: release.tag };
    await writeFile(catalog, JSON.stringify({ name: 'nexaduo', plugins: [{ name: 'marc', source }] }));
    const nativeMarkets = [{ name: 'nexaduo', root: dir }];
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
  assert.ok(logs.some(s => s.startsWith('claude\t26.11.26\tproject')));
  assert.ok(!logs.some(s => s.startsWith('claude\t26.11.26\tuser')));
  const all = [];
  assert.equal(await invoke(['install', '--harness', 'codex', '--harness', 'claude', '--scope', 'local', '--yes'], all), 0);
  assert.deepEqual(all[1][1], ['plugin', 'add', 'marc@nexaduo']);
  assert.deepEqual(all.at(-1)[1], ['plugin', 'install', 'marc@nexaduo', '--scope', 'local']);
});

test('allowlist rejects embedded and trailing metacharacters, including JS end-anchor newline edge', () => {
  for (const char of ['&', '|', '%', '^', '"', '!', '\n', '\r', ' ']) for (const value of [`x${char}`, `${char}x`, `x${char}x`]) {
    assert.throws(() => safeValue(value));
    assert.throws(() => spawnSpec('C:\\bin\\cli.cmd', [value], { platform: 'win32', env: { SystemRoot: 'C:\\Windows' } }));
    for (const flag of ['--harness', '--scope', '--version']) assert.throws(() => parseArgs(['install', flag, value]));
  }
  for (const tag of ['v26.11.26\n', 'v26.11.26\r\n']) assert.throws(() => tagToNpm(tag));
});
