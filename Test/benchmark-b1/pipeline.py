"""Watch the existing development controller, then run frozen blind audits.

Never restarts editors, resumes paused entrants, or repairs business code.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

import runner


def inspect_development(admin):
    manifest = runner.manifest(admin)
    states = {key: json.loads((admin / 'groups' / key / 'state.json').read_text(encoding='utf-8'))
              for key in manifest['order']}
    problems = {key: state['status'] for key, state in states.items()
                if state['status'] in ('paused', 'invalid', 'stopped') or state.get('needs_seal_review')}
    if problems:
        return 'attention_required', problems
    if not all(state['status'] in runner.TERMINAL for state in states.values()):
        return 'development_running', {key: state['status'] for key, state in states.items()}
    for key in manifest['order']:
        entry = admin / 'sealed' / key
        if not (entry / 'seal.json').is_file():
            # A terminal state can be observed while the controller is still sealing.
            return 'development_running', {'sealing': key}
        seal = json.loads((entry / 'seal.json').read_text(encoding='utf-8'))
        if runner.inventory(entry / 'project') != seal['source_files']:
            raise RuntimeError('Sealed project changed: ' + key)
    return 'ready_for_audit', {key: state['status'] for key, state in states.items()}


def verify_runtime(runtime):
    amendment = json.loads((runtime / 'amendment.json').read_text(encoding='utf-8'))
    for relative, expected in amendment['runtime_files'].items():
        if runner.digest(runtime / relative) != expected:
            raise RuntimeError('Versioned infrastructure runtime changed: ' + relative)


def audit_step(admin, runtime, stage, extra=()):
    verify_runtime(runtime)
    command = [sys.executable, '-X', 'utf8', str(runtime / 'runtime.py'), 'audit',
               '--admin', str(admin), stage, *extra]
    with (admin / ('pipeline-' + stage + '.log')).open('ab') as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError('Audit ' + stage + ' paused; inspect pipeline-' + stage + '.log')


def collect_ancillary(admin, runtime):
    verify_runtime(runtime)
    source = runtime / 'metrics.py'
    if not source.is_file():
        raise RuntimeError('Versioned ancillary metrics collector is missing')
    with (admin / 'pipeline-metrics.log').open('ab') as log:
        result = subprocess.run([sys.executable, '-X', 'utf8', str(source), '--admin', str(admin)],
                                stdout=log, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError('Ancillary evidence collection failed; inspect pipeline-metrics.log')


def watch(admin, runtime, editor, interval=600):
    state_file = admin / 'pipeline-state.json'
    try:
        verify_runtime(runtime)
        while True:
            stage, detail = inspect_development(admin)
            runner.write_json(state_file, {'utc': runner.utc(), 'stage': stage, 'detail': detail})
            if stage == 'attention_required':
                return 2
            if stage == 'ready_for_audit':
                break
            time.sleep(interval)
        runner.write_json(state_file, {'utc': runner.utc(), 'stage': 'ancillary_metrics_running'})
        collect_ancillary(admin, runtime)
        runner.write_json(state_file, {'utc': runner.utc(), 'stage': 'independent_audits_running'})
        if not (admin / 'audits/controller.json').is_file():
            audit_step(admin, runtime, 'init', ['--editor', str(editor)])
        audit_step(admin, runtime, 'run')
        runner.write_json(state_file, {'utc': runner.utc(), 'stage': 'complete',
                                      'results': str(admin / 'results')})
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        runner.write_json(state_file, {'utc': runner.utc(), 'stage': 'attention_required', 'error': str(exc)})
        return 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--admin', type=Path, required=True)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--editor', type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(watch(args.admin.resolve(), args.runtime.resolve(), args.editor.resolve()))


if __name__ == '__main__':
    main()
