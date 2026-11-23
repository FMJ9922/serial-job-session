"""Judge status polling with a persistent minimum ten-minute interval.

An early invocation reads only its cadence policy, not participant state.
--wait stays in the same process until the next scheduled observation.
"""
from __future__ import annotations
import argparse
from collections import deque, Counter
import datetime as dt
import json
import math
import os
from pathlib import Path
import sys
import time

import runner

INTERVAL_SECONDS = 600


def process_alive(pid):
    if not pid:
        return None
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = api.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return False if ctypes.get_last_error() == 87 else None
        try:
            code = wintypes.DWORD()
            return code.value == 259 if api.GetExitCodeProcess(handle, ctypes.byref(code)) else None
        finally:
            api.CloseHandle(handle)
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return None


def poll(admin, now=None):
    admin = Path(admin)
    now = now or dt.datetime.now(dt.timezone.utc)
    policy_path = admin / 'monitor-policy.json'
    policy = json.loads(policy_path.read_text(encoding='utf-8')) if policy_path.exists() else {}
    last = dt.datetime.fromisoformat(policy['last_poll_utc']) if policy.get('last_poll_utc') else None
    if last and now < last + dt.timedelta(seconds=INTERVAL_SECONDS):
        due = last + dt.timedelta(seconds=INTERVAL_SECONDS)
        return {'status': 'not_due', 'next_poll_utc': due.isoformat(),
                'wait_seconds': math.ceil((due - now).total_seconds())}
    manifest = runner.manifest(admin)
    groups = {}
    for key in manifest['order']:
        own = admin / 'groups' / key
        state = json.loads((own / 'state.json').read_text(encoding='utf-8'))
        entry = {name: state.get(name) for name in ('status', 'pid', 'needs_seal_review',
                 'seal_error', 'needs_infrastructure_review', 'effective_seconds')}
        entry['sealed'] = (admin / 'sealed' / key / 'seal.json').is_file()
        if state['status'] == 'running':
            entry['process_alive'] = process_alive(state.get('pid'))
            path = own / f"attempt-{len(state.get('attempts', [])):03}.jsonl"
            messages = deque(maxlen=2)
            if path.exists():
                entry['log_bytes'] = path.stat().st_size
                with path.open(encoding='utf-8') as handle:
                    for line in handle:
                        try:
                            event = json.loads(line)
                        except ValueError:
                            continue
                        item = event.get('item', {})
                        if item.get('type') == 'agent_message':
                            messages.append(item.get('text', '')[:300])
            entry['self_reported_progress'] = list(messages)
        groups[key] = entry
    report = {'utc': now.isoformat(), 'status': 'observed', 'groups': groups,
              'interval_seconds': INTERVAL_SECONDS}
    audits = admin / 'audits/controller.json'
    if audits.exists():
        cases = json.loads(audits.read_text(encoding='utf-8'))['groups']
        report['audit_status_counts'] = dict(Counter(case['status'] for case in cases.values()))
    runner.write_json(admin / 'monitor-latest.json', report)
    runner.write_json(policy_path, {'interval_seconds': INTERVAL_SECONDS, 'last_poll_utc': now.isoformat(),
                                   'next_poll_utc': (now + dt.timedelta(seconds=INTERVAL_SECONDS)).isoformat()})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--admin', type=Path, default=runner.DEFAULT_ADMIN)
    parser.add_argument('--wait', action='store_true')
    args = parser.parse_args()
    while True:
        report = poll(args.admin)
        if report['status'] != 'not_due' or not args.wait:
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return
        # Each sleep is bounded; the foreground agent remains interruptible and
        # can work on other judge tasks while this process waits.
        time.sleep(min(60, report['wait_seconds']))


if __name__ == '__main__':
    main()
