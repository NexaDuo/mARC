import { spawn } from 'node:child_process';
import { accessSync, constants, realpathSync, statSync } from 'node:fs';
import path from 'node:path';

export const SAFE_VALUE = /^[A-Za-z0-9@._/:#-]+$/;
const FLAGS = new Set(['--json', '--dry-run', '--scope', '--depth', '--branch', '--no-checkout', '--no-optional-locks', '--detach', '--force', '-r']);
export function safeValue(value) {
  if (typeof value !== 'string' || !SAFE_VALUE.test(value) || /[\r\n]/.test(value) || value.startsWith('-')) throw new Error(`Unsafe value: ${JSON.stringify(value)}`);
  return value;
}
function samePath(a, b, p = path) {
  return p.relative(a, b) === '';
}
function executableInCwd(file, cwd, p = path) {
  return samePath(p.dirname(file), cwd, p);
}

// PATH is the user's trust boundary. Never search cwd, relative PATH entries,
// or symlinks into cwd. PATHEXT can only select known executable extensions.
export function resolveExecutable(name, { env = process.env, cwd = process.cwd(), platform = process.platform } = {}) {
  safeValue(name);
  if (/[/:]/.test(name)) throw new Error('Executable names must be bare names');
  const win = platform === 'win32';
  const p = win ? path.win32 : path;
  const extensions = win ? (env.PATHEXT || '.COM;.EXE;.BAT;.CMD').split(';')
    .map(e => e.toLowerCase()).filter(e => ['.com', '.exe', '.bat', '.cmd'].includes(e)) : [''];
  let realCwd;
  try { realCwd = realpathSync(cwd); } catch { realCwd = cwd; }
  for (const dir of (env.PATH || env.Path || '').split(win ? ';' : ':')) {
    if (!p.isAbsolute(dir) || samePath(p.resolve(dir), cwd, p)) continue;
    for (const ext of extensions) {
      const file = p.join(dir, name + ext);
      try {
        const real = realpathSync(file);
        if (executableInCwd(real, realCwd, p) || !statSync(real).isFile()) continue;
        accessSync(real, win ? constants.F_OK : constants.X_OK);
        return real;
      } catch { /* Try the next executable, without invoking anything. */ }
    }
  }
  throw new Error(`${name} was not found on PATH outside the current directory`);
}

export function spawnSpec(file, args, { platform = process.platform, env = process.env } = {}) {
  if (platform !== 'win32' || !/\.(cmd|bat)$/i.test(file)) return { file, args, options: {} };
  for (const arg of args) {
    if (typeof arg !== 'string' || !SAFE_VALUE.test(arg) || /[\r\n]/.test(arg) || (arg.startsWith('-') && !FLAGS.has(arg))) {
      throw new Error(`Unsafe Windows batch argument: ${JSON.stringify(arg)}`);
    }
  }
  // Do not trust COMSPEC or resolve cmd.exe through PATH. Spaces are permitted
  // only in the quoted, absolute executable path; all shell metacharacters fail.
  if (!path.win32.isAbsolute(file) || /[&|%\^"!\r\n]/.test(file)) throw new Error('Unsafe batch path');
  const root = env.SystemRoot || env.SYSTEMROOT;
  if (!root || !path.win32.isAbsolute(root) || /[&|%\^"!\r\n]/.test(root)) throw new Error('Unsafe SystemRoot');
  const cmd = path.win32.join(root, 'System32', 'cmd.exe');
  const cwd = process.cwd();
  if (executableInCwd(cmd, cwd, path.win32)) throw new Error('cmd.exe cannot come from cwd');
  return { file: cmd, args: ['/d', '/s', '/c', `""${file}" ${args.join(' ')}"`],
    options: { windowsVerbatimArguments: true } };
}

function childEnvironment(env, cwd) {
  const win = process.platform === 'win32';
  const paths = (env.PATH || env.Path || '').split(path.delimiter).filter(dir => {
    try { return path.isAbsolute(dir) && !samePath(realpathSync(dir), realpathSync(cwd)); } catch { return false; }
  });
  const clean = Object.fromEntries(Object.entries(env).filter(([k]) => !['PATH', 'PATHEXT'].includes(k.toUpperCase())));
  clean.PATH = paths.join(path.delimiter);
  if (win) {
    clean.PATHEXT = (env.PATHEXT || '.COM;.EXE;.BAT;.CMD').split(';').filter(e => /^\.(COM|EXE|BAT|CMD)$/i.test(e)).join(';');
    clean.NoDefaultCurrentDirectoryInExePath = '1';
  }
  return clean;
}

export function createRunner({ env = process.env, cwd = process.cwd() } = {}) {
  // Resolve and launch the same absolute path (no second PATH lookup).
  return async (name, args, options = {}) => {
    const file = resolveExecutable(name, { env, cwd });
    if (options.cwd && executableInCwd(realpathSync(file), realpathSync(options.cwd))) throw new Error('Executable resolves inside command cwd');
    let childEnv = childEnvironment(env, options.cwd || cwd);
    if (options.isolatedGit) {
      childEnv = Object.fromEntries(Object.entries(childEnv).filter(([k]) => !k.toUpperCase().startsWith('GIT_')));
      Object.assign(childEnv, { GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: process.platform === 'win32' ? 'NUL' : '/dev/null',
        GIT_CONFIG_COUNT: '1', GIT_CONFIG_KEY_0: 'core.hooksPath',
        GIT_CONFIG_VALUE_0: process.platform === 'win32' ? 'NUL' : '/dev/null', GIT_TERMINAL_PROMPT: '0' });
    }
    const spec = spawnSpec(file, args, { env: childEnv });
    return new Promise((resolve, reject) => {
      const child = spawn(spec.file, spec.args, { ...spec.options, env: childEnv, cwd: options.cwd || cwd,
        stdio: ['ignore', 'pipe', 'pipe'] });
      child.stdout.setEncoding('utf8');
      child.stderr.setEncoding('utf8');
      let stdout = '', stderr = '';
      const timer = setTimeout(() => { child.kill(); reject(new Error(`${name} timed out`)); }, 120000);
      child.stdout.on('data', b => { stdout += b; if (stdout.length > 4 * 1024 * 1024) child.kill(); });
      child.stderr.on('data', b => { stderr += b; if (stderr.length > 4 * 1024 * 1024) child.kill(); });
      child.on('error', e => { clearTimeout(timer); reject(e); });
      child.on('close', code => {
        clearTimeout(timer);
        if (code !== 0) reject(new Error(`${name} ${args.join(' ')} failed (${code}): ${stderr.trim()}`));
        else resolve(stdout);
      });
    });
  };
}
