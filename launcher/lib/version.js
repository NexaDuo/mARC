// Calendar tags have three components and an optional positive release number.
export function tagToNpm(tag) {
  const m = /^v([1-9][0-9])\.([1-9][0-9]?)\.([1-9][0-9]?)(?:\.([1-9][0-9]*))?$/.exec(tag);
  if (!m || m[0] !== tag) throw new Error(`Invalid release tag: ${tag}`);
  const [y, month, day, n] = [Number(m[1]), Number(m[2]), Number(m[3]), Number(m[4] || 0)];
  const date = new Date(Date.UTC(2000 + y, month - 1, day));
  if (date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day ||
      y * 10000 + month * 100 + day < 261125 || !Number.isSafeInteger(n)) {
    throw new Error(`Unsupported release tag: ${tag}`);
  }
  return `${y}.${month * 100 + day}.${n}`;
}

export function npmToTag(version) {
  if (!/^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$/.test(version)) {
    throw new Error(`Invalid npm version: ${version}`);
  }
  const [y, md, n] = version.split('.').map(Number);
  const tag = `v${y}.${Math.floor(md / 100)}.${md % 100}${n ? `.${n}` : ''}`;
  if (tagToNpm(tag) !== version) throw new Error(`Invalid npm version: ${version}`);
  return tag;
}
