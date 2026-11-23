"""Windows infrastructure entry point; frozen experiment files remain unchanged.

Usage: python runtime.py development <runner arguments>
       python runtime.py audit <audit_runner arguments>
The same UTF-8 settings must reach MCP processes spawned by the tool host,
which need not inherit the controller's environment.
"""
from __future__ import annotations

import os
import sys
import json
from pathlib import Path


def configure_environment():
    os.environ['PYTHONUTF8'] = '1'
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    for name in ('CODEX_APP_TOOLS_PIPE_PATH', 'CODEX_THREAD_ID', 'CODEX_SESSION_ID'):
        os.environ.pop(name, None)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')


def unity_environment():
    return {'mcp_servers.unity.env.PYTHONUTF8': '1',
            'mcp_servers.unity.env.PYTHONIOENCODING': 'utf-8'}


def patch_development(module):
    original = module.overrides

    def overrides(*args, **kwargs):
        return {**original(*args, **kwargs), **unity_environment()}

    module.overrides = overrides


def patch_audit(module):
    import json
    original = module.audit_command

    def command(*args, **kwargs):
        result = original(*args, **kwargs)
        if result[-1] != '-':
            raise RuntimeError('Expected stdin prompt marker in audit CLI command')
        extra = []
        for name, value in unity_environment().items():
            extra += ['-c', name + '=' + json.dumps(value)]
        return result[:-1] + extra + result[-1:]

    module.audit_command = command


def audit_operation_policy(runtime_directory=None):
    """Optional versioned user permission update; never alters the rubric."""
    directory = Path(runtime_directory or Path(__file__).parent).resolve()
    config = directory / 'operation-policy.json'
    if not config.exists():
        return None
    import runner
    runner.check_links(config)
    value = json.loads(config.read_text(encoding='utf-8'))
    if value.get('version') != 1 or not isinstance(value.get('path'), str):
        raise RuntimeError('Malformed versioned audit operation policy')
    policy_path = (directory / value['path']).resolve()
    if not policy_path.is_relative_to(directory):
        raise RuntimeError('Audit operation policy must remain inside its versioned runtime')
    return runner.operation_policy(policy_path, value.get('sha256'))


def patch_audit_policy(module, policy):
    if policy is None:
        return
    original = module.audit.spec.audit_prompt

    def prompt(*args, **kwargs):
        return (original(*args, **kwargs) +
                '\n\nUser operation constraint, applied equally to every auditor and descendant. '
                'The frozen 100 requirements remain unchanged. Policy SHA256: ' +
                policy['sha256'] + '\n' + policy['text'] + '\n')

    module.audit.spec.audit_prompt = prompt


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ('development', 'audit'):
        raise SystemExit('Usage: runtime.py {development|audit} <controller arguments>')
    mode = sys.argv.pop(1)
    configure_environment()
    if mode == 'development':
        import runner as controller
        patch_development(controller)
    else:
        import audit_runner as controller
        patch_audit(controller)
        patch_audit_policy(controller, audit_operation_policy())
    controller.main()


if __name__ == '__main__':
    main()
