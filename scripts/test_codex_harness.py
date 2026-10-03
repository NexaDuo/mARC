#!/usr/bin/env python3
"""Codex structural/safety regressions; --cli adds actual isolated installation.

No model calls. Installation does not prove hook dispatch or native-agent discovery.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'core/scripts'))
import dispatch_agent as dispatch
import test_hooks_parity as hooks

CODEX = ROOT / 'harnesses/codex/marc'
SKILL_ENTRY_MAX_BYTES = 8000  # Repository budget, not a claimed Codex runtime limit.
READERS = ('sec', 'security', 'rev', 'review', 'research', 'bulk-reader')
TELEMETRY = ('token_sentinel.py', 'token_telemetry.py', 'token_telemetry_backfill.py', 'token_telemetry_report.py')


def validate_package(plugin):
    manifest = json.loads((plugin / 'plugin.json').read_text())
    expected = json.loads((ROOT / 'harnesses/claude-code/marc/.claude-plugin/plugin.json').read_text())
    assert manifest['name'] == 'marc' and manifest['version'] == expected['version']
    assert manifest['skills'] == './skills/'
    for role in ('engineer', 'sre', 'design', 'security', 'review', 'research', 'bulk-reader'):
        path = plugin / 'agents' / (role + '.toml')
        data = tomllib.loads(path.read_text())
        mode = 'read-only' if role in READERS else 'workspace-write'
        assert data['name'] == role and data['sandbox_mode'] == mode
        assert path.read_bytes() == (ROOT / 'core/agent-configs/codex' / path.name).read_bytes()
    for path in (plugin / 'skills').rglob('*.md'):
        if path.name == 'SKILL.md' and path.parent.name == 'tech-lead':
            assert path.stat().st_size <= SKILL_ENTRY_MAX_BYTES
        for target in re.findall(r'\]\(([^)#]+)(?:#[^)]*)?\)', path.read_text()):
            if '://' not in target and not target.startswith(('$', '<', '/')):
                assert (path.parent / target).is_file(), f'{path}: broken reference {target}'
    for path in plugin.rglob('*'):
        assert not path.is_symlink(), f'{path}: package must be self-contained'
    assert 'read-guard' not in json.loads((plugin / 'compile.json').read_text())['hook_ids']
    compiled = json.loads((plugin / 'hooks/hooks.json').read_text())
    assert 'read-guard' not in json.dumps(compiled)
    assert set(compiled['hooks']) == {'SessionStart'}


def telemetry_unavailable(scripts, env, cwd):
    for filename in TELEMETRY:
        result = subprocess.run([sys.executable, str(scripts / filename)], cwd=cwd, env=env, text=True, capture_output=True, check=False)
        assert result.returncode == 2, (filename, result.stderr)
        assert 'codex telemetry unavailable' in result.stderr
        for flag in (['--hook'] if filename in TELEMETRY[:2] else []):
            hook = subprocess.run([sys.executable, str(scripts / filename), flag], input='{}', cwd=cwd, env=env, text=True, capture_output=True, check=False)
            assert hook.returncode == 0 and 'codex telemetry unavailable' in hook.stderr
            assert hook.stdout == ''


class CodexRegression(unittest.TestCase):
    def test_package_and_marketplace(self):
        validate_package(CODEX)
        market = json.loads((ROOT / '.agents/plugins/marketplace.json').read_text())
        self.assertEqual(market['name'], 'nexaduo')
        marc = next(p for p in market['plugins'] if p['name'] == 'marc')
        self.assertEqual(marc['source']['source'], 'local')
        self.assertEqual((ROOT / marc['source']['path']).resolve(), CODEX)

    def test_entrypoint_budget_all_harnesses(self):
        for path in (ROOT / 'harnesses').glob('*/marc/skills/tech-lead/SKILL.md'):
            self.assertLessEqual(path.stat().st_size, SKILL_ENTRY_MAX_BYTES, str(path))
            for target in re.findall(r'\]\((references/[^)#]+)(?:#[^)]*)?\)', path.read_text()):
                self.assertTrue((path.parent / target).is_file(), f'{path}: {target}')

    def test_readonly_routes_never_reach_codex(self):
        for role in READERS + (' @REV ', ' SEC ', '@bulk-reader'):
            for opts in ({}, {'explicit_harness': 'codex'}, {'toml_mode': 'native'},
                         {'toml_routes': {dispatch.normalize_role(role): 'codex'}}):
                with self.subTest(role=role, opts=opts), contextlib.redirect_stderr(io.StringIO()):
                    route = dispatch.resolve_target_harness_detailed('codex', role, cli_checker=lambda b: b, **opts)
                    self.assertEqual(route['harness'], 'claude-code')
                    with self.assertRaises(dispatch.ReadOnlyRoutingError):
                        dispatch.resolve_target_harness_detailed('codex', role, cli_checker=lambda b: b if b == 'codex' else None, **opts)
            with self.assertRaises(dispatch.ReadOnlyRoutingError):
                dispatch.build_harness_command('codex', role, 'untrusted input')
        runner = Mock()
        with contextlib.redirect_stderr(io.StringIO()):
            result = dispatch.dispatch('bulk-reader', 'untrusted input', harness='codex', runner_fn=runner, which_fn=lambda b: b if b == 'codex' else None)
        self.assertFalse(result['success'])
        runner.assert_not_called()

    def test_validator_unknown_dialect_fails(self):
        before = len(hooks._failures)
        with contextlib.redirect_stdout(io.StringIO()):
            hooks.validate_hook_schema('test', 'unknown', {})
        self.assertGreater(len(hooks._failures), before)
        del hooks._failures[before:]

    def test_codex_env_validator_rejects_wrong_root(self):
        config = json.loads((CODEX / 'compile.json').read_text())
        compiled = json.loads((CODEX / 'hooks/hooks.json').read_text())
        damaged = json.loads(json.dumps(compiled).replace('${PLUGIN_ROOT:-$PWD}', '${WRONG_ROOT:-$PWD}'))
        before = len(hooks._failures)
        with contextlib.redirect_stdout(io.StringIO()):
            hooks.check_command_env_fallbacks('codex', str(CODEX), config, damaged)
        self.assertGreater(len(hooks._failures), before)
        del hooks._failures[before:]

    def test_telemetry_source_and_compiled_are_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, MARC_STATE_DIR=tmp, CODEX_PROJECT_DIR=tmp)
            telemetry_unavailable(ROOT / 'core/scripts', env, tmp)
            telemetry_unavailable(CODEX / 'scripts', env, tmp)
            self.assertEqual(list(Path(tmp).iterdir()), [])
            # Explicit Claude mode still processes a real Claude-format input.
            log = Path(tmp) / 'session.jsonl'
            log.write_text('')
            result = subprocess.run([sys.executable, str(CODEX / 'scripts/token_sentinel.py'), '--harness', 'claude-code', str(log)], env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0)
            self.assertIn('turns:   0', result.stdout)


def cli_smoke():
    with tempfile.TemporaryDirectory(prefix='marc-codex-consumer-') as tmp:
        base = Path(tmp)
        home, consumer, state = base / 'codex-home', base / 'consumer', base / 'state'
        home.mkdir(); consumer.mkdir(); state.mkdir()
        env = {k: v for k, v in os.environ.items() if not any(secret in k for secret in ('TOKEN', 'API_KEY'))}
        env.update(CODEX_HOME=str(home), MARC_STATE_DIR=str(state))
        def run(*args):
            result = subprocess.run(['codex', *args], cwd=consumer, env=env, text=True, capture_output=True, check=True)
            return json.loads(result.stdout)
        run('plugin', 'marketplace', 'add', str(ROOT), '--json')
        installed = run('plugin', 'add', 'marc@nexaduo', '--json')
        assert installed['pluginId'] == 'marc@nexaduo'
        path = Path(installed['installedPath'])
        assert path.is_relative_to(home)
        validate_package(path)
        assert any(p['pluginId'] == 'marc@nexaduo' and p['enabled'] and p['installed'] for p in run('plugin', 'list', '--json')['installed'])
        assert not (consumer / '.agents/team.toml').exists()
        result = subprocess.run([sys.executable, str(path / 'scripts/dispatch_agent.py'), '--role', 'dev', '--harness', 'codex', '--prompt', 'smoke', '--dry-run', '--json'], cwd=consumer, env=env, text=True, capture_output=True, check=True)
        command = json.loads(result.stdout)['command']
        assert command[command.index('--sandbox') + 1] == 'workspace-write'
        telemetry_unavailable(path / 'scripts', env, consumer)
        assert list(state.iterdir()) == []
        print('Codex real marketplace/add/list and installed-helper consumer smoke: PASS')
        print('Coverage: installation/helpers; hook dispatch and native-agent discovery NOT tested.')


if __name__ == '__main__':
    smoke = '--cli' in sys.argv
    if smoke:
        sys.argv.remove('--cli')
    result = unittest.main(exit=False)
    if not result.result.wasSuccessful():
        raise SystemExit(1)
    if smoke:
        cli_smoke()
