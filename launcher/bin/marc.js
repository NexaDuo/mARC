#!/usr/bin/env node
import { readFile } from 'node:fs/promises';
import { main } from '../lib/main.js';
const release = JSON.parse(await readFile(new URL('../release.json', import.meta.url), 'utf8'));
try {
  process.exitCode = await main(process.argv.slice(2), { release });
} catch (error) {
  console.error(`marc: ${error.message}`);
  process.exitCode = 1;
}
