"""Project-pinned stdio MCP relay around the installed MCP for Unity server.

The native server retains its real tool schemas and behavior. This relay checks
the live project before each operation and hides cross-project discovery. It is
an orchestration boundary, not an OS sandbox for arbitrary C# or shell code.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import queue
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time


def normalized(path):
    return str(Path(path).resolve()).replace('\\', '/').rstrip('/').casefold()


def exact(sock, size):
    result = bytearray()
    while len(result) < size:
        chunk = sock.recv(size - len(result))
        if not chunk:
            raise ConnectionError('Unity bridge disconnected')
        result.extend(chunk)
    return bytes(result)


def bridge_request(port, command, timeout=15):
    with socket.create_connection(('127.0.0.1', int(port)), timeout=timeout) as sock:
        sock.settimeout(timeout)
        greeting = bytearray()
        while not greeting.endswith(b'\n'):
            greeting.extend(exact(sock, 1))
            if len(greeting) > 256:
                raise ValueError('Invalid Unity bridge greeting')
        if not greeting.startswith(b'WELCOME UNITY-MCP 1 FRAMING=1'):
            raise ValueError('Unexpected Unity bridge protocol')
        payload = json.dumps(command).encode('utf-8')
        sock.sendall(struct.pack('>Q', len(payload)) + payload)
        length = struct.unpack('>Q', exact(sock, 8))[0]
        if length > 64 * 1024 * 1024:
            raise ValueError('Oversized Unity response')
        return json.loads(exact(sock, length))


def discover(project):
    assets = normalized(Path(project) / 'Assets')
    found = []
    for path in (Path.home() / '.unity-mcp').glob('unity-mcp-status-*.json'):
        try:
            entry = json.loads(path.read_text(encoding='utf-8-sig'))
            if normalized(entry.get('project_path', '.')) == assets:
                entry['hash'] = path.stem.removeprefix('unity-mcp-status-')
                found.append((path.stat().st_mtime, entry))
        except (OSError, ValueError):
            pass
    if not found:
        raise ConnectionError('No running Unity bridge for the assigned project')
    entry = max(found, key=lambda item: item[0])[1]
    response = bridge_request(entry['unity_port'], {'type': 'get_project_info', 'params': {}})
    info = response.get('result', {}).get('data', {})
    if not info or normalized(info.get('projectRoot', '.')) != normalized(project):
        raise PermissionError('Live Unity project does not match the assigned project')
    if info.get('unityVersion') != '2022.3.62f2':
        raise ValueError('Unity version differs from frozen B1 environment')
    entry['info'] = info
    entry['id'] = f"{entry.get('project_name', Path(project).name)}@{entry['hash']}"
    return entry


def drain(project, timeout=150):
    """Quiesce only the assigned editor, without closing/restarting it."""
    deadline = time.monotonic() + timeout
    live = discover(project)
    reply = bridge_request(live['unity_port'], {'type': 'manage_editor', 'params': {'action': 'stop'}})
    if not reply.get('result', {}).get('success'):
        raise RuntimeError('Could not stop assigned project play mode for sealing')
    consecutive_idle = 0
    last = None
    while time.monotonic() < deadline:
        live = discover(project)
        last = bridge_request(live['unity_port'], {'type': 'get_editor_state', 'params': {}}).get('result', {}).get('data', {})
        idle = (last.get('activity', {}).get('phase') == 'idle'
                and last.get('compilation', {}).get('is_compiling') is False
                and last.get('compilation', {}).get('is_domain_reload_pending') is False
                and last.get('assets', {}).get('is_updating') is False
                and last.get('tests', {}).get('is_running') is False
                and last.get('editor', {}).get('play_mode', {}).get('is_playing') is False)
        consecutive_idle = consecutive_idle + 1 if idle else 0
        if consecutive_idle >= 3:
            return {'project': str(project), 'quiescent': True, 'editor_restarted': False, 'state': last}
        time.sleep(1)
    raise TimeoutError('Assigned Unity editor did not become quiescent; sealing needs judge review')


class RpcPeer:
    """Small JSON-lines MCP client; never exposes the server's stderr to a model."""
    def __init__(self, command, stderr_path=None):
        self.stderr_file = open(stderr_path, 'ab') if stderr_path else subprocess.DEVNULL
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=self.stderr_file, text=True, encoding='utf-8',
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.pending = {}
        self.lock = threading.Lock()
        self.next_id = 1
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                with self.lock:
                    target = self.pending.get(message.get('id'))
                if target is not None:
                    target.put(message)
        finally:
            with self.lock:
                for target in self.pending.values():
                    target.put({'error': {'code': -32000, 'message': 'Native Unity MCP server stopped'}})

    def notify(self, method, params=None):
        with self.lock:
            self.process.stdin.write(json.dumps({'jsonrpc': '2.0', 'method': method, 'params': params or {}}) + '\n')
            self.process.stdin.flush()

    def call(self, method, params=None, timeout=300):
        target = queue.Queue()
        with self.lock:
            number = self.next_id
            self.next_id += 1
            self.pending[number] = target
            self.process.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': number, 'method': method, 'params': params or {}}, ensure_ascii=False) + '\n')
            self.process.stdin.flush()
        try:
            response = target.get(timeout=timeout)
            if 'error' in response:
                raise RuntimeError(response['error'].get('message', 'Native MCP request failed'))
            return response.get('result', {})
        finally:
            with self.lock:
                self.pending.pop(number, None)

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
        if hasattr(self.stderr_file, 'close'):
            self.stderr_file.close()


class UnityProxy:
    def __init__(self, project, log, native_command=None):
        self.project = Path(project).resolve()
        self.log_path = Path(log).resolve()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.operation_lock = threading.Lock()
        self.peer = None
        self.native_command = native_command
        self.tool_catalog = []

    def start(self):
        live = discover(self.project)
        command = self.native_command or [shutil.which('uvx') or 'uvx', '--offline', '--from',
                    'mcpforunityserver==10.3.0', 'mcp-for-unity', '--transport', 'stdio',
                    '--default-instance', live['id']]
        self.peer = RpcPeer(command, self.log_path.with_suffix('.native.stderr.log'))
        result = self.peer.call('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {},
                                  'clientInfo': {'name': 'b1-pinned-unity-relay', 'version': '1.0'}})
        self.peer.notify('notifications/initialized')
        self.tool_catalog = self.peer.call('tools/list').get('tools', [])
        self._select(live)
        return result

    def _select(self, live):
        if any(tool['name'] == 'set_active_instance' for tool in self.tool_catalog):
            result = self.peer.call('tools/call', {'name': 'set_active_instance', 'arguments': {'instance': live['id']}})
            if result.get('isError'):
                raise ConnectionError('Native MCP could not select the assigned Unity instance')

    def _log(self, method, params, result=None, error=None):
        def clean(value):
            if isinstance(value, dict):
                return {key: ('<redacted>' if any(word in key.lower() for word in ('api_key', 'authorization', 'secret', 'bearer'))
                              else '<binary omitted>' if key in ('data', 'b64_json') and isinstance(item, str) and len(item) > 4000
                              else clean(item)) for key, item in value.items()}
            if isinstance(value, list):
                return [clean(item) for item in value]
            return value
        record = {'utc': dt.datetime.now(dt.timezone.utc).isoformat(), 'project': str(self.project),
                  'method': method, 'params': clean(params), 'error': str(error) if error else None,
                  'result_sha256': hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest() if result is not None else None}
        with self.log_path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + '\n')

    def _check_scope(self, value, live):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ('unity_instance', 'instance', 'instance_id') and item not in (None, '', live['id'], str(live['unity_port']), self.project.name, str(self.project), self.project.as_posix()):
                    raise PermissionError('Cross-project Unity instance selection is forbidden')
                self._check_scope(item, live)
        elif isinstance(value, list):
            for item in value:
                self._check_scope(item, live)
        elif isinstance(value, str):
            text = value.replace('\\', '/').casefold()
            # Resolve complete path arguments as well as literal code strings:
            # Windows 8.3 aliases must not bypass the project path boundary.
            resolved_text = ''
            try:
                if Path(value).is_absolute():
                    resolved_text = normalized(value)
            except (OSError, ValueError):
                pass
            for parent in self.project.parent.parent.glob('Demo*'):
                other = parent / 'AITest'
                if other.is_dir() and normalized(other) != normalized(self.project):
                    foreign = normalized(other)
                    if foreign in text or resolved_text == foreign or resolved_text.startswith(foreign + '/'):
                        raise PermissionError('Cross-project path access is forbidden')

    def dispatch(self, method, params):
        if method == 'ping':
            return {}
        with self.operation_lock:
            live = discover(self.project)
            self._check_scope(params, live)
            self._select(live)
            if method == 'tools/list':
                result = self.peer.call(method, params)
                # A server-only selection helper is unnecessary and could be mistaken for a global selector.
                result['tools'] = [tool for tool in result.get('tools', []) if tool['name'] != 'set_active_instance']
            elif method == 'tools/call':
                if params.get('name') == 'set_active_instance':
                    result = {'content': [{'type': 'text', 'text': json.dumps({'selected': live['id'], 'project': str(self.project)})}]}
                else:
                    arguments = dict(params.get('arguments', {}))
                    arguments['unity_instance'] = live['id']
                    result = self.peer.call(method, {**params, 'arguments': arguments})
            elif method == 'resources/read' and params.get('uri', '').rstrip('/').endswith(('instances', 'unity/instances')):
                result = {'contents': [{'uri': params['uri'], 'mimeType': 'application/json',
                          'text': json.dumps({'instances': [{'id': live['id'], 'port': live['unity_port'],
                                                   'project': str(self.project), 'unity_version': '2022.3.62f2'}]})}]}
            elif method in ('resources/read', 'resources/list', 'resources/templates/list', 'prompts/list', 'prompts/get', 'completion/complete'):
                result = self.peer.call(method, params)
            else:
                raise ValueError(f'Unsupported MCP method: {method}')
            self._log(method, params, result=result)
            return result

    def serve(self):
        initialization = self.start()
        try:
            for line in sys.stdin:
                message = {}
                try:
                    message = json.loads(line)
                    if 'id' not in message:
                        continue
                    method = message.get('method', '')
                    if method == 'initialize':
                        result = {**initialization, 'serverInfo': {'name': 'B1 Project-Pinned Unity MCP', 'version': '1.0'},
                                  'instructions': 'All Unity operations are pinned to your assigned project. Native MCP for Unity tool schemas are preserved.'}
                    else:
                        result = self.dispatch(method, message.get('params', {}))
                    response = {'jsonrpc': '2.0', 'id': message['id'], 'result': result}
                except Exception as error:
                    self._log(message.get('method', '?'), message.get('params', {}), error=error)
                    response = {'jsonrpc': '2.0', 'id': message.get('id'), 'error': {'code': -32000, 'message': str(error)}}
                print(json.dumps(response, ensure_ascii=False), flush=True)
        finally:
            self.peer.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--log', type=Path, required=True)
    parser.add_argument('--preflight', action='store_true')
    parser.add_argument('--drain', action='store_true')
    args = parser.parse_args()
    proxy = UnityProxy(args.project, args.log)
    if args.drain:
        result = drain(args.project)
        proxy._log('terminal/drain', {}, result=result)
        print(json.dumps(result, ensure_ascii=False))
    elif args.preflight:
        try:
            proxy.start()
            state = proxy.dispatch('resources/read', {'uri': 'mcpforunity://editor/state'})
            tools = proxy.dispatch('tools/list', {})
            print(json.dumps({'project': str(args.project), 'tool_count': len(tools['tools']),
                              'editor_resource_responded': bool(state.get('contents'))}))
        finally:
            if proxy.peer:
                proxy.peer.close()
    else:
        proxy.serve()


if __name__ == '__main__':
    main()
