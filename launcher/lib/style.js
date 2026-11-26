// Console styling for the launcher. SGR sequences are only ever built from the
// constants below and a closed role enum; text is never interpolated into them.
// Plain mode (color off) returns text unchanged and adds no decoration.

// role: [truecolor rgb, 256-color index, 16-color code, light-profile code, bold]
const ROLES = {
  brand: [[255, 106, 193], 205, '95', '35', true],
  tag: [[94, 241, 255], 87, '96', '36', false],
  dim: [[169, 169, 214], 146, '2', '2', false],
  harness: [[184, 146, 255], 141, '94', '34', true],
  cmd: [[94, 241, 255], 87, '96', '36', false],
  ok: [[124, 255, 178], 121, '92', '32', true],
  stale: [[255, 212, 121], 222, '93', '33', true],
  failed: [[255, 106, 193], 205, '95', '35', true],
  error: [[255, 106, 193], 205, '95', '35', true],
  warn: [[255, 212, 121], 222, '93', '33', false],
  remedy: [[255, 212, 121], 222, '93', '33', true],
  drift: [[255, 212, 121], 222, '93', '33', true],
  strong: null, // bold, default foreground
  info: null, // terminal default foreground and weight
};

// C0 (incl. ESC, TAB, CR, LF), DEL, C1, and bidi/invisible format controls.
// eslint-disable-next-line no-control-regex
const UNSAFE = /[\u0000-\u001f\u007f-\u009f\u061c\u180e\u200b-\u200f\u2028-\u202e\u2060-\u2064\u2066-\u2069\ufeff]/g;
export const stripControls = text => String(text ?? '').replace(UNSAFE, '');

const ON = v => v !== undefined && v !== '' && v !== '0' && v !== 'false';

export function colorDepth(env = {}) {
  const ct = String(env.COLORTERM || '').toLowerCase();
  if (ct === 'truecolor' || ct === '24bit') return 24;
  if (/256color/i.test(String(env.TERM || ''))) return 8;
  return 4;
}

export function createStyle({ env = process.env, isTTY = Boolean(process.stdout.isTTY), platform = process.platform } = {}) {
  const winVt = Boolean(env.WT_SESSION || env.ANSICON || env.ConEMUANSI === 'ON' || env.TERM_PROGRAM);
  const force = env.FORCE_COLOR;
  let enabled, depth = colorDepth(env);
  if (env.NO_COLOR) enabled = false; // any non-empty value
  else if (force !== undefined && force !== '') {
    enabled = ON(force);
    if (force === '2' && depth < 8) depth = 8;
    if (force === '3') depth = 24;
  } else enabled = Boolean(isTTY) && env.TERM !== 'dumb' && (platform !== 'win32' || winVt);
  // Legacy conhost (no VT hint) cannot be assumed to do more than 16 colors.
  if (enabled && platform === 'win32' && !winVt) depth = 4;
  if (enabled && platform === 'win32' && !env.WT_SESSION && depth === 24) depth = 8;
  const light = enabled && /(?:^|;)(?:7|15)$/.test(String(env.COLORFGBG || ''));
  const code = role => {
    if (!Object.hasOwn(ROLES, role)) throw new Error(`Unknown style role: ${role}`);
    const spec = ROLES[role];
    if (role === 'strong') return '1';
    if (!spec) return '';
    const [rgb, c256, c16, cLight, bold] = spec;
    if (light) return `${bold ? '1;' : ''}${cLight}`;
    const b = bold ? '1;' : '';
    if (depth >= 24) return `${b}38;2;${rgb.join(';')}`;
    if (depth >= 8) return `${b}38;5;${c256}`;
    return bold ? `1;${c16}` : c16;
  };
  const paint = (role, text) => {
    if (!enabled) { if (!Object.hasOwn(ROLES, role)) throw new Error(`Unknown style role: ${role}`); return text; }
    const clean = stripControls(text);
    const sgr = code(role);
    return sgr ? `\x1b[${sgr}m${clean}\x1b[0m` : clean;
  };
  // Decoration lines exist only in color mode.
  const deco = lines => enabled ? lines : [];
  const locale = env.LC_ALL || env.LC_CTYPE || env.LANG || '';
  const unicode = platform === 'win32' ? Boolean(env.WT_SESSION || env.TERM_PROGRAM) : !locale || /utf-?8/i.test(locale);
  return { enabled, unicode, depth: depth >= 24 ? 24 : depth >= 8 ? 8 : 4, light, paint, deco };
}
