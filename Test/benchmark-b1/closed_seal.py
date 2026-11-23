"""Judge-only terminal sealing when a Unity editor is positively verified closed.

No Unity launch, restart, bridge call, business repair or model continuation.
This establishes no *known* writers, not an OS security/isolation guarantee.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid

import runner

COLLECTOR = 'B1_CLOSED_SEAL_CIM_V1'

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def json_events(path, allow_diagnostics=False, with_line=False):
    yield from runner.native_json_events(path, allow_diagnostics, with_line)


def canonical_item_sha256(item):
    import hashlib
    return hashlib.sha256(json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()

def native_pairs(files, cancellations=None):
    count = 0
    all_pending = {}
    if not files:
        raise RuntimeError('Native attempt logs are required')
    for path in files:
        pending = {}
        for line_number, event in json_events(path, allow_diagnostics=True, with_line=True):
            kind = event['type']
            if kind in ('item.started', 'item.completed'):
                item = event.get('item')
                if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not isinstance(item.get('type'), str):
                    raise RuntimeError('Unrecognized native item envelope')
                key = item['id']
                if kind == 'item.started':
                    if key in pending:
                        raise RuntimeError('Duplicate pending native item')
                    pending[key] = {'item': item, 'path': str(path), 'line': line_number}
                else:
                    if item.get('status') in ('running', 'in_progress'):
                        raise RuntimeError('Native completion still reports running')
                    pending.pop(key, None)
                    count += 1
            elif kind not in ('thread.started', 'turn.started', 'turn.completed', 'turn.failed', 'error'):
                raise RuntimeError(f'Unknown native event type: {kind}')
        if set(pending) & set(all_pending):
            raise RuntimeError('Ambiguous repeated pending native IDs across attempts')
        all_pending.update(pending)
    cancelled = []
    if cancellations is not None:
        entries = cancellations['items']
        if {entry['id'] for entry in entries} != set(all_pending) or len(entries) != len(all_pending):
            raise RuntimeError('Judge cancellations must exactly match all actual pending native items')
        for entry in entries:
            record = all_pending[entry['id']]
            item = record['item']
            if item.get('type') != 'mcp_tool_call' or item.get('server') != 'unity':
                raise RuntimeError('Only exact Unity native RPC requests may be adjudicated cancelled')
            if canonical_item_sha256(item) != entry['item_sha256']:
                raise RuntimeError('Pending native request canonical hash changed')
            if cancellations.get('version') == 2:
                if (normalized(record['path']) != normalized(entry.get('native_source_log', ''))
                        or record['line'] != entry.get('native_source_line')):
                    raise RuntimeError('Exit cancellation native original log/line binding changed')
                validate_exit0_request(item, entry)
            cancelled.append({**record, 'item_sha256': entry['item_sha256'], 'judge_decision': entry})
    elif all_pending:
        raise RuntimeError('Pending native item requests: ' + ', '.join(sorted(all_pending)))
    return {'attempt_logs': len(files), 'completed_items': count, 'pending': sorted(all_pending),
            'cancelled_by_target_termination': cancelled}

def readonly_background_command(call, project):
    """Parse a deliberately tiny, non-executed subset of JS and PowerShell."""
    if call.get('type') != 'custom_tool_call' or call.get('name') != 'exec':
        raise RuntimeError('Nonwriting exception only permits the reviewed exec wrapper')
    literal = r'("(?:[^"\\]|\\.)*")'
    pattern = (r'\s*const r=await tools\.exec_command\(\{cmd:' + literal +
               r',workdir:' + literal + r',yield_time_ms:([0-9]+)\}\);text\(r\);\s*')
    match = re.fullmatch(pattern, call.get('input', ''))
    if not match:
        raise RuntimeError('Unknown exec wrapper; cannot establish nonwriting command')
    command, workdir = json.loads(match[1]), json.loads(match[2])
    if normalized(workdir) != normalized(project):
        raise RuntimeError('Nonwriting command working directory differs from assigned project')
    lines = command.splitlines()
    if len(lines) not in (1, 2) or not re.fullmatch(r'Start-Sleep -Seconds (?:[1-9]|[1-5][0-9]|60)', lines[0]):
        raise RuntimeError('Only finite literal Start-Sleep <=60 seconds is permitted')
    if len(lines) == 2:
        read = re.fullmatch(r'Get-Content (?:-LiteralPath )?(?:"([A-Za-z0-9_./\\: -]+)"|([A-Za-z0-9_./\\:-]+)) -Tail ([1-9][0-9]*)', lines[1])
        if not read:
            raise RuntimeError('Only one literal log-file Get-Content -Tail is permitted')
        spelling = read[1] or read[2]
        candidate = Path(spelling)
        if not candidate.is_absolute():
            candidate = Path(project) / candidate
        candidate = runner.check_links(candidate)
        logs = Path(project) / 'Logs'
        if (candidate.parent.resolve() != logs.resolve() or candidate.suffix.casefold() != '.log'
                or ':' in candidate.name
                or candidate.name.split('.')[0].casefold() in {'con', 'prn', 'aux', 'nul',
                    *('com' + str(i) for i in range(1, 10)), *('lpt' + str(i) for i in range(1, 10))}):
            raise RuntimeError('Read target must be one assigned-project Logs/*.log file')
    return {'classification': 'finite_sleep_optional_single_log_tail', 'command': command, 'workdir': workdir}


def rollout_own_streams(files):
    """Attribute persisted events before interpreting any tool/terminal state.

    The first session_meta.id owns a physical rollout, even when session_id is
    the root's ID. Full-history context is a known projection of parent history,
    not a raw persisted prefix; it is never child execution activity.
    Unknown boundaries cannot be repaired by guessing from a terminal event.
    """
    if not files:
        raise RuntimeError('Persisted session logs are required')
    accepted = {'session_meta', 'turn_context', 'response_item', 'event_msg', 'compacted',
                'world_state', 'token_usage_record', 'inter_agent_communication_metadata'}
    nodes = {}
    for path in files:
        rows = list(json_events(path, with_line=True))
        if not rows or rows[0][1]['type'] != 'session_meta':
            raise RuntimeError('Rollout lacks first-session identity anchor')
        first = rows[0][1].get('payload')
        if not isinstance(first, dict):
            raise RuntimeError('Malformed first-session metadata')
        sid = first.get('id', first.get('session_id'))
        if not isinstance(sid, str) or not sid:
            raise RuntimeError('Session has no attributable first identity')
        if sid in nodes:
            raise RuntimeError('Ambiguous duplicate physical rollout owner')
        source = first.get('source')
        if source is not None and not isinstance(source, (str, dict)):
            raise RuntimeError('Malformed first-session source')
        if isinstance(source, dict):
            agent = source.get('subagent', {})
            if not isinstance(agent, dict) or not isinstance(agent.get('thread_spawn', {}), dict):
                raise RuntimeError('Malformed first-session subagent source')
            spawn = agent.get('thread_spawn', {})
        else:
            spawn = {}
        parents = [x for x in (first.get('parent_thread_id'), first.get('forked_from_id'), spawn.get('parent_thread_id')) if x is not None]
        if any(not isinstance(x, str) or not x or x == sid for x in parents) or len(set(parents)) > 1:
            raise RuntimeError('Conflicting first-session parent identity')
        parent = parents[0] if parents else None
        boundary = first.get('subagent_history_start_ordinal')
        if boundary is not None and (type(boundary) is not int or boundary < 1 or not parent):
            raise RuntimeError('Invalid full-history execution boundary')
        if first.get('forked_from_id') is not None and boundary is None:
            raise RuntimeError('Forked history lacks exact execution boundary')
        for line_number, event in rows:
            kind = event['type']
            payload = event.get('payload', {})
            if kind not in accepted or not isinstance(payload, dict):
                raise RuntimeError(f'Unrecognized rollout format: {kind}')
            if kind == 'world_state' and not {'full', 'state'} <= payload.keys():
                raise RuntimeError('Unrecognized world_state metadata envelope')
            if kind == 'session_meta':
                identity = payload.get('id', payload.get('session_id'))
                if not isinstance(identity, str) or not identity:
                    raise RuntimeError('Malformed session metadata identity')
        if boundary is not None:
            # An ordinal is the persisted execution boundary, not a turn count
            # or a physical line number. Gaps/duplicates leave unknown activity.
            ordinals = [event.get('ordinal') for _, event in rows]
            if any(type(x) is not int for x in ordinals) or ordinals != list(range(len(rows))) or boundary > len(rows):
                raise RuntimeError('Unknown or discontinuous full-history ordinal boundary')
            inherited = rows[1:boundary]
            own = rows[boundary:]
        else:
            inherited, own = [], rows[1:]
            if any(event['type'] == 'session_meta' and event['payload'].get('id', event['payload'].get('session_id')) != sid for _, event in own):
                raise RuntimeError('Inherited metadata without an exact full-history boundary')
        for _, event in own:
            if event['type'] == 'session_meta':
                payload = event['payload']
                if payload.get('id', payload.get('session_id')) != sid:
                    raise RuntimeError('Different session identity inside actual own stream')
                if payload.get('subagent_history_start_ordinal', boundary) != boundary:
                    raise RuntimeError('Conflicting repeated owner boundary metadata')
                if payload.get('parent_thread_id', parent) != parent or payload.get('forked_from_id', parent) != parent:
                    raise RuntimeError('Conflicting repeated owner parent metadata')
        nodes[sid] = {'path': path, 'rows': rows, 'parent': parent, 'boundary': boundary,
                      'inherited': inherited, 'own': own,
                      'attribution': {'file': str(path), 'first_metadata_line': rows[0][0],
                          'first_metadata_id': sid, 'first_metadata_session_id': first.get('session_id'),
                          'parent': parent, 'own_start_ordinal': boundary,
                          'projection_policy': 'ordered_parent_history_subset_v1' if boundary is not None else 'no_inherited_history',
                          'inherited_records_excluded': len(inherited), 'own_records': len(own),
                          'matched_parent_projection_records': 0, 'unmatched_dispatch_contexts': []}}
    resolved, visiting = set(), set()
    def canonical_inherited(event):
        result = copy.deepcopy({k: v for k, v in event.items() if k not in ('ordinal', 'timestamp')})
        payload = result.get('payload', {})
        if result.get('type') == 'response_item' and payload.get('type') == 'message':
            metadata = result.get('metadata')
            if metadata is not None:
                if not isinstance(metadata, dict):
                    raise RuntimeError('Unknown inherited message metadata projection')
                # Confirmed CLI context projection only. Preserve source IDs,
                # attribution and complete payload, especially tool arguments.
                for key in ('client_authored', 'inherited_user_message', 'user_input_order'):
                    metadata.pop(key, None)
                if not metadata:
                    result.pop('metadata', None)
        if result.get('type') == 'world_state':
            state = payload.get('state')
            if isinstance(state, dict):
                state.pop('multi_agent_usage_hint', None)
        return canonical_item_sha256(result)
    def dispatch_context(event):
        payload = event.get('payload', {})
        content = payload.get('content')
        passthrough = payload.get('internal_chat_message_metadata_passthrough', {})
        return (event.get('type') == 'response_item' and payload.get('type') == 'message'
                and payload.get('role') == 'developer' and isinstance(content, list) and bool(content)
                and all(isinstance(x, dict) and isinstance(x.get('type'), str) and isinstance(x.get('text'), str) for x in content)
                and isinstance(passthrough, dict) and not passthrough.get('turn_id')
                and set(payload) <= {'type', 'id', 'role', 'content', 'internal_chat_message_metadata_passthrough'})
    def resolve(sid):
        if sid in resolved:
            return
        if sid in visiting:
            raise RuntimeError('Cyclic rollout ancestry')
        visiting.add(sid)
        node = nodes[sid]
        parent = node['parent']
        if parent:
            if parent not in nodes:
                raise RuntimeError('Missing parent rollout evidence')
            resolve(parent)
        if node['inherited']:
            parent_rows = nodes[parent]['rows']
            first_inherited = node['inherited'][0][1]
            if (first_inherited['type'] != 'session_meta'
                    or first_inherited['payload'].get('id', first_inherited['payload'].get('session_id')) != parent):
                raise RuntimeError('Inherited projection does not start with direct parent identity')
            originals = [canonical_inherited(event) for _, event in parent_rows]
            cursor = 0
            for index, (line, event) in enumerate(node['inherited']):
                digest = canonical_inherited(event)
                try:
                    position = originals.index(digest, cursor)
                except ValueError:
                    if index == len(node['inherited']) - 1 and dispatch_context(event):
                        node['attribution']['unmatched_dispatch_contexts'].append({
                            'line': line, 'ordinal': event['ordinal'], 'type': 'developer_message',
                            'event_sha256': canonical_item_sha256(event),
                            'classification': 'unmatched_dispatch_context_excluded_from_execution_proof'})
                        continue
                    raise RuntimeError('Inherited projection does not match ordered original parent history') from None
                cursor = position + 1
                node['attribution']['matched_parent_projection_records'] += 1
        ancestors, cursor = set(), parent
        while cursor:
            ancestors.add(cursor)
            cursor = nodes[cursor]['parent']
        ancestor_turns = {event['payload'].get('turn_id') for ancestor in ancestors
                          for _, event in nodes[ancestor]['own'] if event['type'] == 'turn_context'} - {None}
        own_turns = {event['payload'].get('turn_id') for _, event in node['own'] if event['type'] == 'turn_context'} - {None}
        if ancestor_turns & own_turns:
            raise RuntimeError('Ancestor turn identity appears inside actual child execution boundary')
        visiting.remove(sid)
        resolved.add(sid)
    for sid in nodes:
        resolve(sid)
    owners = {}
    for sid, node in nodes.items():
        for _, event in node['own']:
            if event['type'] == 'turn_context':
                turn = event['payload'].get('turn_id')
                if isinstance(turn, str) and turn:
                    if turn in owners and owners[turn] != sid:
                        raise RuntimeError('Conflicting own turn identity across rollout owners')
                    owners[turn] = sid
    return nodes


def rollout_pairs(files, readonly_evidence=None, project=None):
    nodes = rollout_own_streams(files)
    sessions = {}
    # Tool host handles are local to an actual owner; another child's wait
    # must never supply terminal evidence for this owner's background task.
    active_backgrounds, origins = set(), {}
    for sid, node in nodes.items():
        path = node['path']
        sessions[sid] = {'pending': {}, 'completed': 0, 'last_task': None,
                         'background_started': set(), 'background_finished': set(), 'background_terminal_events': [],
                         'process_status_not_exported': []}
        for line_number, event in node['own']:
            kind, payload = event['type'], event['payload']
            if kind == 'event_msg':
                typ = payload.get('type')
                if typ in ('task_started', 'task_complete', 'turn_aborted', 'task_aborted'):
                    sessions[sid]['last_task'] = typ
            if kind == 'response_item':
                typ = payload.get('type')
                if typ in ('function_call', 'custom_tool_call', 'function_call_output', 'custom_tool_call_output'):
                    cid = payload.get('call_id')
                    if not isinstance(cid, str) or not cid:
                        raise RuntimeError('Tool call/output lacks call_id')
                    token = ('custom' if typ.startswith('custom') else 'function', cid)
                    record = sessions[sid]
                    if typ.endswith('_output'):
                        if token not in record['pending']:
                            raise RuntimeError('Unmatched or out-of-order tool output')
                        call = record['pending'].pop(token)
                        started, finished = background_handles(call, payload)
                        projections = stdout_projection_commands(call)
                        if projections is not None:
                            sessions[sid]['process_status_not_exported'].append({
                                'classification': 'status_not_exported_stdout_only', 'call_id': cid,
                                'call_sha256': canonical_item_sha256(call), 'source': str(path),
                                'call_line': next(n for n, original in node['own'] if original['payload'] is call),
                                'output_line': line_number, 'output_sha256': canonical_item_sha256(payload),
                                'commands': projections,
                                'limitation': 'No nested process terminal or absence of writers inferred; requires independent scoped process census and stable inventories.'})
                        polls = background_poll_targets(call)
                        if started & finished:
                            raise RuntimeError('Ambiguous background start/terminal order within one output')
                        for handle in started:
                            identity = (sid, handle)
                            if identity in active_backgrounds:
                                if handle in polls:
                                    continue  # A still-running poll is not a new launch.
                                raise RuntimeError('Ambiguous reuse of an active background tool handle')
                            if handle in polls:
                                raise RuntimeError('Polling background tool handles lack a preceding own start')
                            active_backgrounds.add(identity)
                            # A completed generation may legitimately reuse a
                            # handle; bind exceptions/terminal proof to this start.
                            origins[identity] = {'call': call, 'session_id': sid, 'path': str(path),
                                'output_line': line_number, 'timestamp': event.get('timestamp')}
                        confirmed_finished = set()
                        for handle in finished:
                            identity = (sid, handle)
                            if handle not in polls:
                                if identity in active_backgrounds:
                                    raise RuntimeError('Synchronous result cannot close another active background tool handle')
                                # A directly completed exec result is not a
                                # terminal observation for any earlier/later bg.
                                continue
                            if identity not in active_backgrounds:
                                raise RuntimeError('Terminal background tool handles lack a preceding own start')
                            active_backgrounds.remove(identity)
                            confirmed_finished.add(handle)
                        record['background_started'].update(started)
                        record['background_finished'].update(confirmed_finished)
                        if confirmed_finished:
                            output_text = '\n'.join(text_fragments(payload.get('output', '')))
                            status = re.search(r'(?:^|\n)Script (completed|failed|terminated)(?:\n|$)', output_text)
                            record['background_terminal_events'].append({'handles': sorted(confirmed_finished),
                                'started_by_call_ids': {handle: origins[(sid, handle)]['call']['call_id'] for handle in sorted(confirmed_finished)},
                                'status': status.group(1) if status else 'exec_exit_observed',
                                'call_id': payload['call_id'], 'timestamp': event.get('timestamp'),
                                'output_sha256': canonical_item_sha256(payload)})
                        record['completed'] += 1
                    else:
                        if token in record['pending']:
                            raise RuntimeError('Duplicate pending tool call')
                        record['pending'][token] = payload
                elif typ == 'agent_message':
                    if not {'author', 'recipient', 'content'} <= payload.keys():
                        raise RuntimeError('Unrecognized agent_message metadata envelope')
                elif typ not in ('message', 'reasoning', 'web_search_call', 'ghost_snapshot', 'compaction'):
                    raise RuntimeError(f'Unrecognized response item format: {typ}')
    if not sessions:
        raise RuntimeError('No attributable sessions')
    for record in sessions.values():
        if record['pending']:
            raise RuntimeError('Unanswered session tool calls')
    unresolved_backgrounds = active_backgrounds
    nonwriting = []
    if readonly_evidence is not None:
        entries = readonly_evidence['items']
        expected = {(item['session_id'], item['handle']) for item in entries}
        if expected != unresolved_backgrounds or len(entries) != len(unresolved_backgrounds):
            raise RuntimeError('Nonwriting review must exactly match unresolved background handles')
        for entry in entries:
            origin = origins[(entry['session_id'], entry['handle'])]
            call = origin['call']
            if (not entry['handle'].startswith('exec:') or origin['session_id'] != entry['session_id']
                    or call['call_id'] != entry['call_id'] or canonical_item_sha256(call) != entry['call_sha256']):
                raise RuntimeError('Nonwriting review does not match original exec call identity/hash')
            parsed = readonly_background_command(call, project)
            nonwriting.append({**origin, **parsed, 'handle': entry['handle'],
                               'judge_decision': 'unresolved_background_verified_nonwriting'})
    elif unresolved_backgrounds:
        raise RuntimeError('Known background tool handles lack terminal evidence: ' + ', '.join(sid + ':' + handle for sid, handle in sorted(unresolved_backgrounds)))
    return {sid: {'completed_calls': r['completed'], 'last_task': r['last_task'], 'pending': [],
                  'background_started': sorted(r['background_started']), 'background_finished': sorted(r['background_finished']),
                  'background_active': sorted(handle for owner, handle in active_backgrounds if owner == sid),
                  'background_terminal_events': r['background_terminal_events'],
                  'process_status_not_exported': r['process_status_not_exported'],
                  'attribution': nodes[sid]['attribution'],
                  'unresolved_background_nonwriting': [entry for entry in nonwriting if entry['session_id'] == sid],
                  'terminal_event_present': r['last_task'] in ('task_complete', 'turn_aborted', 'task_aborted'),
                  'record_incomplete': r['last_task'] not in ('task_complete', 'turn_aborted', 'task_aborted')}
            for sid, r in sessions.items()}

def text_fragments(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for part in value.values():
            yield from text_fragments(part)
    elif isinstance(value, list):
        for part in value:
            yield from text_fragments(part)


def background_poll_targets(call):
    """Recognize target handles from the same wait/stdin wrappers as the parser."""
    arguments = str(call.get('arguments', call.get('input', '')))
    name = call.get('name', '')
    targets = set()
    if 'write_stdin' in name or 'tools.write_stdin' in arguments:
        targets.update('exec:' + sid for sid in re.findall(r'session_id[\s"\x27]*:\s*(\d+)', arguments))
    if 'wait' in name or 'tools.wait' in arguments:
        cells = re.findall(r'cell_id[\s"\x27]*:\s*(?:["\x27]([^"\x27]+)["\x27]|(\d+))', arguments)
        targets.update('cell:' + (quoted or numeric) for quoted, numeric in cells)
    return targets

def readonly_web_companion(source, *, before_projection=False):
    """Recognize only the observed literal read-only web companion shapes."""
    string = r'''(?:"(?:[^"\\\r\n]|\\.)*"|'[^'\\\r\n]*')'''
    def array(row):
        return r'\[\s*' + row + r'(?:\s*,\s*' + row + r'){0,2}\s*\]'
    found = array(r'\{\s*ref_id\s*:\s*' + string + r'\s*,\s*pattern\s*:\s*' + string + r'\s*\}')
    opened = array(r'\{\s*ref_id\s*:\s*' + string + r'\s*\}')
    searched = array(r'\{\s*q\s*:\s*' + string + r'\s*\}')
    if before_projection:
        domains = array(r'"(?:[^"\\\r\n]|\\.)*"')
        query = r'\{\s*q\s*:\s*' + string + r'\s*,\s*domains\s*:\s*' + domains + r'\s*\}'
        actions = r'search_query\s*:\s*\[\s*' + query + r'(?:\s*,\s*' + query + r'){2,3}\s*\]'
    else:
        actions = (r'(?:find\s*:\s*' + found + r'\s*,\s*search_query\s*:\s*' + searched
                   + r'|open\s*:\s*' + opened + r'|find\s*:\s*' + found + r'\s*,\s*open\s*:\s*' + opened + r')')
    pattern = (r'\s*text\(await\s+tools\.web__run\(\{\s*' + actions
               + r'\s*,\s*response_length\s*:\s*"(?:short|medium|long)"\s*\}\)\)\s*;?\s*')
    if not re.fullmatch(pattern, source):
        return False
    try:
        for value in re.findall(string, source):
            if value.startswith('"'):
                json.loads(value)
    except ValueError:
        return False
    return True


VARIABLE_STDOUT_PROGRAMS = {
    # Complete native-source fingerprints: fixed alias, literal commands and
    # only the observed read-only ALL_TOOLS metadata expressions, if present.
    '2976e2029e11dbd79a247c78294b6fc417c19d0f3e49ef51e4d8c6df45fabc16': (9000, False),
    '19e2fb361dc67f863070099be6394e9894cb3cf362073f9504b2ab8151bb4c38': (20000, False),
    '67db3ff81aa4c22eab768cf33a2a7deb8f888d9727587df0911ff17f42285c5b': (8000, True),
}


def variable_stdout_program(call):
    source = call.get('input', call.get('arguments', ''))
    if not isinstance(source, str) or call.get('name', '').rsplit('.', 1)[-1] != 'exec':
        return None
    known = VARIABLE_STDOUT_PROGRAMS.get(hashlib.sha256(source.encode('utf-8')).hexdigest())
    if known is None:
        return None
    literal = r'"(?:[^"\\\r\n]|\\.)*"'
    match = re.match(r'\s*const\s+r\s*=\s*await\s+tools\.exec_command\(\{\s*cmd\s*:\s*('
                     + literal + r')\s*,\s*(?:max_output_tokens|"max_output_tokens")\s*:\s*([1-9][0-9]*)\s*\}\);\s*text\(r\.output\)', source)
    if not match or int(match[2]) != known[0]:
        raise RuntimeError('Known variable stdout projection no longer matches its literal declaration')
    try:
        command = json.loads(match[1])
    except ValueError:
        raise RuntimeError('Invalid known variable stdout command literal') from None
    return {'commands': [{'command_sha256': hashlib.sha256(command.encode('utf-8')).hexdigest(),
                          'max_output_tokens': known[0]}], 'direct_return': known[1]}


def direct_exec_packet(value):
    """Typed direct process status only; output is always opaque stdout DATA."""
    return (isinstance(value, dict)
            and set(value) <= {'chunk_id', 'wall_time_seconds', 'exit_code', 'original_token_count', 'output', 'session_id'}
            and isinstance(value.get('output'), str)
            and ('chunk_id' not in value or isinstance(value['chunk_id'], str))
            and ('wall_time_seconds' not in value or (type(value['wall_time_seconds']) in (int, float)
                and math.isfinite(value['wall_time_seconds']) and value['wall_time_seconds'] >= 0))
            and ('original_token_count' not in value or (type(value['original_token_count']) is int and value['original_token_count'] >= 0))
            and (value.get('session_id') is None or (type(value['session_id']) is int and value['session_id'] > 0))
            and (value.get('exit_code') is None or type(value['exit_code']) is int)
            and (type(value.get('exit_code')) is int or type(value.get('session_id')) is int))


def literal_data_full_exec_program(call):
    """Distinguish quoted .output DATA from the two observed full-return programs.

    A full anchored grammar leaves no unexamined outer JS: literal patch DATA
    (if present), then one literal exec whose entire result is printed. This is
    deliberately not a general JS/string masker or a whole-source whitelist.
    """
    source = call.get('input', call.get('arguments', ''))
    if (not isinstance(source, str) or call.get('name', '').rsplit('.', 1)[-1] != 'exec'
            or not re.search(r'\.\s*output\b', source)):
        return None
    string = r'"(?:[^"\\\r\n]|\\.)*"'
    patch = r'text\(await\s+tools\.apply_patch\((' + string + r')\)\)\s*;\s*'
    command = (r'text\(await\s+tools\.exec_command\(\{\s*(?:cmd|"cmd")\s*:\s*('
               + string + r')\s*,\s*(?:max_output_tokens|"max_output_tokens")\s*:\s*'
               r'([1-9][0-9]*)\s*\}\)\)\s*;?')
    match = re.fullmatch(r'\s*(?:' + patch + r')?' + command + r'\s*', source)
    if not match:
        return None
    try:
        if match[1] is not None:
            json.loads(match[1])
        json.loads(match[2])
    except ValueError:
        raise RuntimeError('Invalid quoted DATA in full exec return program') from None
    return {'has_patch': match[1] is not None}


def stdout_projection_commands(call, *, with_auxiliary=False):
    """Recognize only confirmed literal exec stdout projections, never execute JS."""
    source = call.get('input', call.get('arguments', ''))
    if not isinstance(source, str) or call.get('name', '').rsplit('.', 1)[-1] != 'exec':
        return (None, False) if with_auxiliary else None
    if literal_data_full_exec_program(call) is not None:
        return (None, False) if with_auxiliary else None
    variable = variable_stdout_program(call)
    if variable is not None:
        auxiliary = 'variable_direct' if variable['direct_return'] else 'variable'
        return (variable['commands'], auxiliary) if with_auxiliary else variable['commands']
    prefix = r'text\(\(await\s+tools\.exec_command\('
    before_projection = False
    if not re.match(r'\s*' + prefix, source) or not re.search(r'\)\)\.output\)', source):
        projected = re.search(prefix, source)
        if projected and readonly_web_companion(source[:projected.start()], before_projection=True):
            source = source[projected.start():]
            before_projection = True
        elif projected or (re.search(r'tools\s*\.\s*exec_command\b', source)
                           and re.search(r'\.\s*output\b', source)):
            raise RuntimeError('Unrecognized stdout projection program prefix')
        else:
            return (None, False) if with_auxiliary else None
    string = r'"(?:[^"\\\r\n]|\\.)*"'
    cmd = r'(?:cmd|"cmd")\s*:\s*' + string
    limit = r'(?:max_output_tokens|"max_output_tokens")\s*:\s*[1-9][0-9]*'
    fields = '(?:' + cmd + r'\s*,\s*' + limit + '|' + limit + r'\s*,\s*' + cmd + ')'
    pattern = re.compile(r'\s*' + prefix + r'\s*(\{\s*' + fields + r'\s*\})\s*\)\)\.output\)\s*;?')
    cursor, commands = 0, []
    while source[cursor:].strip():
        match = pattern.match(source, cursor)
        if not match:
            raise RuntimeError('Unrecognized stdout projection syntax; process status unavailable')
        literal = re.sub(r'([{,]\s*)(cmd|max_output_tokens)(\s*:)', r'\1"\2"\3', match.group(1))
        try:
            obj = json.loads(literal)
        except ValueError:
            raise RuntimeError('Unrecognized stdout projection literal') from None
        commands.append({'command_sha256': hashlib.sha256(obj['cmd'].encode('utf-8')).hexdigest(),
                         'max_output_tokens': obj['max_output_tokens']})
        cursor = match.end()
        if before_projection:
            if source[cursor:].strip():
                raise RuntimeError('Unrecognized reverse stdout projection program tail')
            return (commands, 'before') if with_auxiliary else commands
        # This complete program still exports no exec process status. Its
        # single known read-only companion contributes only opaque web DATA.
        if len(commands) == 1 and readonly_web_companion(source[cursor:]):
            return (commands, 'after') if with_auxiliary else commands
    return (commands, False) if with_auxiliary else commands


def flattened_allsettled_exec_source(source):
    """Only the two observed literal programs can flatten Promise results."""
    string = r'"(?:[^"\\\r\n]|\\.)*"'
    command = r'tools\.exec_command\(\{\s*cmd\s*:\s*' + string + r'\s*,\s*(?:max_output_tokens|"max_output_tokens")\s*:\s*[1-9][0-9]*\s*\}\)'
    declaration = re.match(r'\s*const\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*await\s+Promise\.allSettled\(\[\s*'
                           + command + r'\s*,\s*' + command + r'\s*\]\)\s*;\s*', source)
    if not declaration:
        return False
    variable = re.escape(declaration[1])
    loop = (r'for\s*\(\s*let\s+i\s*=\s*0\s*;\s*i\s*<\s*' + variable
            + r'\.length\s*;\s*i\+\+\s*\)\s*text\(\{\s*i\s*,\s*\.\.\.' + variable + r'\[i\]\s*\}\)\s*;?')
    each = variable + r'\.forEach\(\(\s*x\s*,\s*i\s*\)\s*=>\s*text\(\{\s*i\s*,\s*\.\.\.x\s*\}\)\)\s*;?'
    if not re.fullmatch(r'(?:' + loop + '|' + each + r')\s*', source[declaration.end():]):
        return False
    try:
        for value in re.findall(r'cmd\s*:\s*(' + string + ')', declaration[0]):
            json.loads(value)
    except ValueError:
        return False
    return True


def background_handles(call, output):
    arguments = str(call.get('arguments', call.get('input', '')))
    name = call.get('name', '')
    started, finished = set(), set()
    is_stdin = 'write_stdin' in name or 'tools.write_stdin' in arguments
    is_process = 'exec_command' in name or 'tools.exec_command' in arguments or is_stdin
    is_cell = name.rsplit('.', 1)[-1] in ('exec', 'wait')
    is_wait = 'wait' in name or 'tools.wait' in arguments
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate process packet key')
            result[key] = value
        return result
    def reject_constant(value):
        raise ValueError('Non-JSON process packet constant: ' + value)
    literal_full = literal_data_full_exec_program(call)
    if literal_full is not None:
        envelope = output.get('output')
        expected = 3 if literal_full['has_patch'] else 2
        if (not isinstance(envelope, list) or len(envelope) != expected
                or not all(isinstance(block, dict) and set(block) == {'type', 'text'}
                           and block['type'] in ('text', 'input_text') and isinstance(block['text'], str)
                           for block in envelope)
                or envelope[0]['text'].splitlines()[:1] != ['Script completed']):
            raise RuntimeError('Unrecognized literal full exec output envelope')
        # Only the final typed block belongs to exec. Patch DATA and the exec
        # packet's stdout are opaque even if they print fake handles/statuses.
        try:
            packet = json.loads(envelope[-1]['text'], object_pairs_hook=unique_object, parse_constant=reject_constant)
        except ValueError:
            raise RuntimeError('Unrecognized literal full exec process packet') from None
        if not direct_exec_packet(packet):
            raise RuntimeError('Unrecognized literal full exec process packet')
        sid = packet.get('session_id')
        if type(sid) is int:
            (finished if type(packet.get('exit_code')) is int else started).add('exec:' + str(sid))
        return started, finished
    projections, projection_auxiliary = stdout_projection_commands(call, with_auxiliary=True)
    flattened_source = is_cell and flattened_allsettled_exec_source(arguments)

    def mcp_data(value):
        return (isinstance(value, dict) and 'content' in value and 'isError' in value
            and set(value) <= {'content', 'structuredContent', 'isError'}
            and isinstance(value['content'], list) and type(value['isError']) is bool
            and ('structuredContent' not in value or isinstance(value['structuredContent'], dict)))

    def blocks(value):
        # Only tool envelope blocks. A process packet's output is stdout DATA,
        # even when it prints a transcript or JSON resembling another tool.
        if isinstance(value, str):
            yield value
        elif isinstance(value, list):
            for item in value:
                yield from blocks(item)
        elif isinstance(value, dict):
            if mcp_data(value):
                yield value
            elif value.get('type') in ('text', 'input_text') and isinstance(value.get('text'), str):
                yield value['text']
            elif any(key in value for key in ('exit_code', 'session_id', 'wall_time_seconds')):
                yield value
            elif isinstance(value.get('content'), list):
                yield from blocks(value['content'])
            else:
                yield value

    packets = []
    first_header = None
    first_block = True
    for block in blocks(output.get('output', '')):
        if isinstance(block, dict):
            packets.append(block)
            first_block = False
            continue
        text = block.lstrip()
        host_header = first_block and text.startswith('Script ')
        # Script status belongs to the first tool header, not later printed
        # input_text blocks. Never search escaped/newline-containing stdout.
        if first_block:
            first_header = text.splitlines()[0] if text.splitlines() else ''
        first_block = False
        if projections is not None:
            continue
        payload = text
        if text.startswith('Script '):
            divided = re.split(r'\r?\nOutput:\r?\n', text, maxsplit=1)
            if len(divided) == 2:
                payload = divided[1]
            elif flattened_source and host_header:
                payload = ''
        decoder = (json.JSONDecoder(object_pairs_hook=unique_object, parse_constant=reject_constant)
                   if flattened_source else json.JSONDecoder())
        remainder = payload.lstrip()
        while remainder.startswith(('{', '[')):
            try:
                obj, consumed = decoder.raw_decode(remainder)
            except ValueError:
                if flattened_source:
                    raise RuntimeError('Malformed or ambiguous flattened process packet') from None
                break
            if isinstance(obj, dict):
                packets.append(obj)
            elif isinstance(obj, list):
                packets.extend(obj)
            remainder = remainder[consumed:].lstrip()
        if flattened_source and remainder:
            raise RuntimeError('Malformed or ambiguous flattened process packet trailing data')
        # Direct exec's human-readable status is restricted to its header.
        # Everything following Output: is untrusted process stdout.
        if is_process and not text.startswith('{') and not text.startswith('Script '):
            header = re.split(r'\r?\nOutput:\r?\n', text, maxsplit=1)[0]
            for line in header.splitlines():
                if line.startswith('Process running with session ID') and not re.fullmatch(r'Process running with session ID\s+\d+\s*', line):
                    raise RuntimeError('Unrecognized authoritative process-running header')
            for sid in re.findall(r'(?:^|\r?\n)Process running with session ID\s+(\d+)(?:\s|$)', header):
                started.add('exec:' + sid)
            if is_stdin and re.search(r'(?:^|\r?\n)Process exited with code\s+-?\d+(?:\s|$)', header):
                finished.update('exec:' + sid for sid in re.findall(r'session_id[\s"\x27]*:\s*(\d+)', arguments))
    if is_cell and first_header:
        running = re.fullmatch(r'Script running with cell ID\s+([A-Za-z0-9_-]+)\.?', first_header)
        if running:
            started.add('cell:' + running.group(1))
        elif first_header.startswith('Script running'):
            raise RuntimeError('Unrecognized authoritative running-cell header')
        elif is_wait and re.fullmatch(r'Script (completed|failed|terminated)', first_header):
            cells = re.findall(r'cell_id[\s"\x27]*:\s*(?:["\x27]([^"\x27]+)["\x27]|(\d+))', arguments)
            finished.update('cell:' + (quoted or numeric) for quoted, numeric in cells)
    if is_process:
        if projections is not None:
            envelope = output.get('output')
            # The host may omit stdout blocks. Their absence is not evidence
            # of empty stdout or inner process termination: all remain unknown.
            # Both two- and three-block reverse programs were observed; never
            # guess which web/stdout DATA block the host omitted or why.
            if (not isinstance(envelope, list)
                    or (len(envelope) != 4 if projection_auxiliary == 'variable_direct'
                        else len(envelope) != 2 if projection_auxiliary == 'variable'
                        else len(envelope) not in (2, 3) if projection_auxiliary == 'before'
                        else len(envelope) != 3 if projection_auxiliary else not 1 <= len(envelope) <= len(projections) + 1)
                    or first_header != 'Script completed'
                    or not all(isinstance(block, dict) and block.get('type') in ('text', 'input_text')
                               and isinstance(block.get('text'), str)
                               and (not projection_auxiliary or set(block) == {'type', 'text'}) for block in envelope)):
                raise RuntimeError('Unrecognized stdout projection output envelope')
            if projection_auxiliary == 'variable_direct':
                try:
                    metadata = json.loads(envelope[2]['text'])
                    direct = json.loads(envelope[3]['text'])
                except ValueError:
                    raise RuntimeError('Unknown mixed projection/direct packet envelope') from None
                if (not isinstance(metadata, list)
                        or not all(isinstance(item, dict) and set(item) == {'name', 'description'}
                                   and all(isinstance(value, str) for value in item.values()) for item in metadata)
                        or not direct_exec_packet(direct)):
                    raise RuntimeError('Unknown mixed projection/direct authoritative packet')
                sid = direct.get('session_id')
                if type(sid) is int:
                    if type(direct.get('exit_code')) is int:
                        finished.add('exec:' + str(sid))
                    else:
                        started.add('exec:' + str(sid))
            return started, finished
        flattened_indices = set()
        for obj in packets:
            if not isinstance(obj, dict):
                continue
            if set(obj) == {'i', 'status', 'value'} and flattened_source:
                if type(obj['i']) is not int or obj['i'] not in (0, 1) or obj['i'] in flattened_indices:
                    raise RuntimeError('Unrecognized flattened Promise result index')
                value = obj['value']
                if obj['status'] != 'fulfilled' or not direct_exec_packet(value):
                    raise RuntimeError('Unrecognized flattened Promise process status; never infer completion')
                flattened_indices.add(obj['i'])
                obj = {'status': obj['status'], 'value': obj['value']}
            if (set(obj) == {'i', 'result'} and type(obj['i']) is int and obj['i'] >= 0
                    and 'Promise.allSettled' in arguments and isinstance(obj['result'], dict)):
                obj = obj['result']
            # A sibling MCP result is tool DATA, not a process wrapper. Its
            # structuredContent may legitimately contain request-context UUIDs
            # named session_id. Do not interpret any of that data as exec state.
            if mcp_data(obj):
                continue
            # Only this confirmed Promise.allSettled envelope can expose its
            # direct process result. Never descend into arbitrary nested data.
            if (set(obj) == {'status', 'value'} and obj['status'] == 'fulfilled'
                    and 'Promise.allSettled' in arguments and isinstance(obj['value'], dict)
                    and any(key in obj['value'] for key in ('exit_code', 'wall_time_seconds'))):
                obj = obj['value']
            if not any(key in obj for key in ('session_id', 'exit_code', 'wall_time_seconds')):
                def contains_metadata(value):
                    if isinstance(value, dict):
                        return any(key in value for key in ('session_id', 'exit_code', 'wall_time_seconds')) or any(contains_metadata(v) for v in value.values())
                    return isinstance(value, list) and any(contains_metadata(v) for v in value)
                if contains_metadata(obj):
                    raise RuntimeError('Unrecognized nested process-status wrapper; cannot infer pending state')
            sid = obj.get('session_id')
            if ('session_id' in obj and sid is not None and type(sid) is not int
                    or obj.get('exit_code') is not None and type(obj['exit_code']) is not int):
                raise RuntimeError('Unrecognized authoritative process-status identity/exit code')
            if type(sid) is int and any(k in obj for k in ('output', 'exit_code', 'wall_time_seconds')):
                if type(obj.get('exit_code')) is int:
                    finished.add('exec:' + str(sid))
                else:
                    started.add('exec:' + str(sid))
            if type(obj.get('exit_code')) is int and is_stdin:
                finished.update('exec:' + sid for sid in re.findall(r'session_id[\s"\x27]*:\s*(\d+)', arguments))
        if flattened_source:
            running = first_header and re.fullmatch(r'Script running with cell ID\s+([A-Za-z0-9_-]+)\.?', first_header)
            if not running and (first_header not in ('Script completed', 'Script failed', 'Script terminated')
                                or flattened_indices != {0, 1} or len(packets) != 2):
                raise RuntimeError('Flattened Promise results lack complete authoritative packet coverage')
    return started, finished

def asset_pairs(path):
    if not path.exists():
        return {'log_present': False, 'pending': [], 'completed': 0}
    pending, completed = set(), 0
    for event in json_events(path):
        cid = event.get('call_id')
        if not isinstance(cid, str) or not cid:
            raise RuntimeError('Unrecognized asset call identity')
        if event['event'] == 'request':
            if cid in pending:
                raise RuntimeError('Duplicate pending asset request')
            pending.add(cid)
        elif event['event'] == 'response':
            if cid not in pending:
                raise RuntimeError('Unmatched asset response')
            pending.remove(cid)
            completed += 1
        else:
            raise RuntimeError('Unknown asset event')
    if pending:
        raise RuntimeError('Pending asset requests')
    return {'log_present': True, 'pending': [], 'completed': completed}

def collect_census():
    script = '''$ErrorActionPreference='Stop'
$marker='B1_CLOSED_SEAL_CIM_V1'
$rows=@(Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,ExecutablePath,CommandLine,CreationDate)
[pscustomobject]@{complete=$true;collector_pid=$PID;collector_marker=$marker;processes=$rows} | ConvertTo-Json -Depth 6 -Compress
'''
    command = ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script]
    proc = subprocess.run(command, capture_output=True, timeout=60)
    if proc.returncode:
        raise RuntimeError('Complete CIM process enumeration failed')
    # Windows PowerShell redirected output follows the active console encoding.
    for encoding in ('utf-8-sig', 'utf-16', 'gbk'):
        try:
            data = json.loads(proc.stdout.decode(encoding))
            break
        except (ValueError, UnicodeError):
            pass
    else:
        raise RuntimeError('CIM output could not be decoded unambiguously')
    return data

def normalized(path):
    return str(Path(path).resolve()).replace('\\', '/').rstrip('/').casefold()

def scoped_text(text, path):
    target = normalized(path)
    if re.search(re.escape(target) + r'''(?=$|[\s/"'])''', text.replace('\\', '/').casefold()):
        return True
    # Resolve standalone absolute path arguments too: Windows 8.3 aliases must
    # not evade an exact assigned-project match.
    for match in re.finditer(r'''"([^"]+)"|'([^']+)'|(\S+)''', text):
        token = next(part for part in match.groups() if part is not None)
        if token.startswith('-') and '=' in token:
            token = token.split('=', 1)[1]
        try:
            if Path(token).is_absolute():
                candidate = normalized(token)
                if candidate == target or candidate.startswith(target + '/'):
                    return True
        except (OSError, ValueError):
            continue
    return False

def verify_census(census, project, own, state, session_ids, known_instance_ids=(), known_actor_pids=()):
    return _verify_census(census, project, own, state, session_ids, known_instance_ids, known_actor_pids, os.getpid())


def replay_census(census, project, own, session_ids, known_instance_ids, known_actor_pids, helper_generation):
    """Pure archived-proof replay; never a fresh/current-process proof."""
    pid = helper_generation.get('ProcessId')
    rows = census.get('processes', [])
    if (type(pid) is not int or pid <= 0 or pid in known_actor_pids
            or not helper_generation.get('CreationDate')
            or [r for r in rows if r.get('ProcessId') == pid] != [helper_generation]):
        raise RuntimeError('Archived helper exact generation identity required')
    result = _verify_census(census, project, own, {}, session_ids, known_instance_ids, known_actor_pids, pid)
    return {**result, 'mode': 'archived_raw_proof_replay', 'helper_generation': helper_generation}


def _verify_census(census, project, own, state, session_ids, known_instance_ids, known_actor_pids, helper_pid):
    if census.get('complete') is not True or census.get('collector_marker') != COLLECTOR:
        raise RuntimeError('A complete attributable CIM census is required')
    rows = census.get('processes')
    if not isinstance(rows, list) or not rows:
        raise RuntimeError('CIM census has no complete process list')
    by_pid = {}
    for row in rows:
        pid = row.get('ProcessId')
        if type(pid) is not int or pid < 0 or pid in by_pid or not isinstance(row.get('Name'), str):
            raise RuntimeError('Malformed or duplicate CIM process identity')
        if pid == 0 and (row['Name'].casefold() not in ('system idle process', 'idle') or row.get('CommandLine') not in (None, '')):
            raise RuntimeError('Malformed PID zero system process identity')
        by_pid[pid] = row
    helper = by_pid.get(helper_pid)
    collector = by_pid.get(census.get('collector_pid'))
    if not helper or 'closed_seal' not in (helper.get('CommandLine') or '') or not collector or COLLECTOR not in (collector.get('CommandLine') or ''):
        raise RuntimeError('Helper/collector process exclusion identity is not proven')
    excluded = {helper_pid, census['collector_pid']}
    known = set()
    if any(type(pid) is not int or pid <= 0 for pid in known_actor_pids):
        raise RuntimeError('Known actor PIDs must be positive integers')
    if any(not isinstance(instance, str) or not re.fullmatch(r'[^\s@]+@[a-fA-F0-9]{8,64}', instance) for instance in known_instance_ids):
        raise RuntimeError('Known Unity instance identifiers are malformed')
    known.update(known_actor_pids)
    for source in [state, *state.get('attempts', [])]:
        for key in ('pid', 'root_pid', 'cli_pid'):
            value = source.get(key)
            if type(value) is int and value > 0:
                known.add(value)
    # Exact recorded session UUID also identifies resumed CLI command lines,
    # whose --cd argument may be absent. PID absence in old state is not proof.
    identities = set(session_ids) | {state.get('session_id')}
    identities.discard(None)
    blocked = []
    for pid, row in by_pid.items():
        if pid == 0:
            continue  # Windows System Idle Process is not an executable writer.
        if pid in excluded:
            continue
        name, command = row['Name'].casefold(), row.get('CommandLine')
        if 'unity' in name and not command:
            raise RuntimeError('Unknown/null Unity process command line prevents closed-editor verification')
        if pid in known:
            blocked.append(pid)
        elif command and (scoped_text(command, project) or scoped_text(command, own)
                          or any(sid in command for sid in identities)
                          or any(re.search(re.escape(instance.casefold()) + r'''(?=$|[\s"'])''', command.casefold()) for instance in known_instance_ids)):
            blocked.append(pid)
    # Also include descendants of positively scoped processes. Do not infer
    # external hosted processes are contained by the CLI's Windows Job Object.
    changed = True
    while changed:
        changed = False
        for pid, row in by_pid.items():
            if pid not in excluded and pid not in blocked and row.get('ParentProcessId') in (set(blocked) | known):
                blocked.append(pid)
                changed = True
    if blocked:
        raise RuntimeError('Live assigned-project/editor/known writer processes: ' + ', '.join(map(str, sorted(blocked))))
    return {'no_assigned_editor': True, 'no_known_writers': True, 'excluded_pids': sorted(excluded),
            'known_pid_fields_present': sorted(known), 'session_id_command_checks': sorted(identities),
            'known_instance_ids_checked': sorted(known_instance_ids),
            'process_count': len(rows), 'scope_limit': 'Recorded sessions, scoped command lines and descendants; not a global filesystem isolation guarantee.'}

def log_files(own):
    attempts = sorted(own.glob('attempt-*.jsonl'))
    sessions = sorted((own / 'home/sessions').rglob('*.jsonl'))
    return attempts, sessions, attempts + sessions + ([own / 'asset-tools.jsonl'] if (own / 'asset-tools.jsonl').exists() else [])

def validate_exit0_request(item, entry):
    args = item.get('arguments')
    if (item.get('type') != 'mcp_tool_call' or item.get('server') != 'unity'
            or item.get('tool') != 'execute_code' or not isinstance(args, dict)
            or set(args) != {'action', 'code', 'safety_checks'} or args['action'] != 'execute'
            or args['safety_checks'] is not False or not isinstance(args['code'], str)
            or args != entry.get('exit_parameters')
            or not re.fullmatch(r'\s*UnityEditor\.EditorApplication\.Exit\(0\);\s*return\s+"(?:[^"\\\r\n]|\\.)*";\s*', args['code'])
            or hashlib.sha256(args['code'].encode('utf-8')).hexdigest() != entry.get('code_sha256')):
        raise RuntimeError('Completed evidence-writer cancellation only permits exact Unity Exit(0) plus return literal')


def validate_completed_evidence_writer(entry, evidence, project, log_hashes):
    """An independent exact-source review, not a generic JavaScript sandbox."""
    if (entry.get('mode') != 'unity_exit0_completed_evidence_writer'
            or entry.get('outer_js_no_external_writes') is not False):
        raise RuntimeError('Evidence-writer mode must truthfully declare outer writes')
    outer, terminal, scope = (entry.get(key) for key in ('outer', 'terminal', 'scope'))
    if not all(isinstance(value, dict) for value in (outer, terminal, scope)):
        raise RuntimeError('Evidence-writer review lacks exact execution and write-scope bindings')
    root = runner.check_links(Path(project).parent / 'evidence').resolve()
    if (normalized(scope.get('evidence_root', '')) != normalized(root)
            or scope.get('decision') != 'audit_evidence_only'
            or not isinstance(scope.get('writer_id'), str) or not scope['writer_id']
            or scope.get('reviewer_id') != evidence['reviewer']
            or scope['reviewer_id'] in (scope['writer_id'], outer.get('owner_session_id'))):
        raise RuntimeError('Evidence-writer scope needs independent actual writer/reviewer identities')
    paths = scope.get('write_paths')
    if not isinstance(paths, list) or not paths or len({normalized(p) for p in paths}) != len(paths):
        raise RuntimeError('Evidence-writer review requires exact distinct write paths')
    for spelling in paths:
        target = runner.check_links(Path(spelling)).resolve()
        if not target.is_relative_to(root) or target == root:
            raise RuntimeError('Reviewed outer write escapes audit evidence root')
    refs = scope.get('helper_source_refs')
    if not isinstance(refs, list) or not refs:
        raise RuntimeError('Evidence-writer helper source references missing')
    for ref in refs:
        target = runner.check_links(Path(ref['path'])).resolve()
        if (not (target.is_relative_to(root) or target == root.parent / 'provenance_tool.py') or not target.is_file()
                or runner.digest(target) != ref.get('sha256')):
            raise RuntimeError('Evidence-writer helper source changed or escaped blind workspace')
    source_path = Path(outer.get('source_log', ''))
    hashes = {normalized(k): v for k, v in log_hashes.items()}
    if normalized(source_path) not in hashes or runner.digest(source_path) != hashes[normalized(source_path)]:
        raise RuntimeError('Evidence-writer original source log lacks current full hash anchor')
    native_path = Path(entry.get('native_source_log', ''))
    if normalized(native_path) not in hashes or runner.digest(native_path) != hashes[normalized(native_path)]:
        raise RuntimeError('Exit cancellation native log lacks current full hash anchor')
    native_event = dict(json_events(native_path, allow_diagnostics=True, with_line=True)).get(entry.get('native_source_line'), {})
    native_item = native_event.get('item', {})
    if (native_event.get('type') != 'item.started' or native_item.get('id') != entry['id']
            or canonical_item_sha256(native_item) != entry['item_sha256']):
        raise RuntimeError('Exit cancellation exact native original event changed')
    validate_exit0_request(native_item, entry)
    files = [Path(p) for p in log_hashes if '/sessions/' in normalized(p) and Path(p).suffix == '.jsonl']
    nodes = rollout_own_streams(files)
    sid = outer.get('owner_session_id')
    if sid not in nodes or normalized(nodes[sid]['path']) != normalized(source_path):
        raise RuntimeError('Evidence-writer source is not the actual own session')
    rows = dict(nodes[sid]['own'])
    def payload(line, expected_type, cid, digest):
        row = rows.get(line, {})
        value = row.get('payload', {})
        if (row.get('type') != 'response_item' or value.get('type') != expected_type
                or value.get('call_id') != cid or canonical_item_sha256(value) != digest):
            raise RuntimeError('Evidence-writer exact own call/output binding changed')
        return value
    call = payload(outer.get('source_line'), 'custom_tool_call', outer.get('call_id'), outer.get('call_sha256'))
    started = payload(outer.get('start_output_line'), 'custom_tool_call_output', outer.get('call_id'), outer.get('start_output_sha256'))
    wait = payload(terminal.get('call_line'), 'function_call', terminal.get('call_id'), terminal.get('call_sha256'))
    ended = payload(terminal.get('output_line'), 'function_call_output', terminal.get('call_id'), terminal.get('output_sha256'))
    source = call.get('input')
    if (call.get('name') != 'exec' or not isinstance(source, str)
            or hashlib.sha256(source.encode('utf-8')).hexdigest() != outer.get('source_sha256')
            or not outer['source_line'] < outer['start_output_line'] < terminal['call_line'] < terminal['output_line']):
        raise RuntimeError('Evidence-writer enclosing source or execution order changed')
    # Only the confirmed literal params declaration and unique call-site bind
    # this native request; independent source review owns all other JS effects.
    quoted = r'"(?:[^"\\\r\n]|\\.)*"'
    params = re.findall(r'const params=\{action:(' + quoted + r'),code:(' + quoted + r'),safety_checks:false\};', source)
    if (len(params) != 1 or source.count('tools.mcp__unity__execute_code(') != 1
            or 'const result=await tools.mcp__unity__execute_code(params);' not in source
            or {'action': json.loads(params[0][0]), 'code': json.loads(params[0][1]), 'safety_checks': False} != entry.get('exit_parameters')):
        raise RuntimeError('Native Exit request is not bound to the reviewed enclosing source')
    handle = outer.get('handle')
    begin_handles, _ = background_handles(call, started)
    _, end_handles = background_handles(wait, ended)
    header = ended.get('output')
    if isinstance(header, list) and header and isinstance(header[0], dict) and header[0].get('type') in ('text', 'input_text'):
        header = header[0].get('text')
    first = header.splitlines()[0] if isinstance(header, str) and header.splitlines() else ''
    if (not isinstance(handle, str) or not re.fullmatch(r'cell:[A-Za-z0-9_-]+', handle)
            or begin_handles != {handle} or end_handles != {handle}
            or wait.get('name') != 'wait' or terminal.get('status') not in ('completed', 'failed', 'terminated')
            or first != 'Script ' + terminal['status']):
        raise RuntimeError('Evidence-writer enclosing cell lacks actual authoritative own terminal')
    # Also apply ordinary owner-scoped ordering/pairing: a later-generation or
    # unrelated terminal cannot be substituted for this exact start.
    paired = rollout_pairs(files)[sid]['background_terminal_events']
    if not any(e['call_id'] == terminal['call_id'] and e['started_by_call_ids'].get(handle) == outer['call_id']
               and e['output_sha256'] == terminal['output_sha256'] for e in paired):
        raise RuntimeError('Evidence-writer terminal does not close the reviewed generation')


def validate_cancellations(path, group, project, log_hashes):
    evidence = read(path)
    if (type(evidence.get('version')) is not int or evidence['version'] not in (1, 2) or evidence.get('group') != group
            or not isinstance(evidence.get('project'), str) or normalized(evidence['project']) != normalized(project)
            or evidence.get('decision') != 'cancelled_by_target_termination' or evidence.get('reviewed') is not True
            or not isinstance(evidence.get('reviewer'), str) or not evidence['reviewer'].strip()):
        raise RuntimeError('Judge cancellation evidence lacks exact scope and explicit reviewed decision')
    claimed = evidence.get('log_sha256')
    if not isinstance(claimed, dict) or not claimed or any(not isinstance(v, str) or not re.fullmatch('[0-9a-f]{64}', v) for v in claimed.values()):
        raise RuntimeError('Judge cancellation evidence needs all original log hashes')
    mapped = {normalized(k): v for k, v in claimed.items()}
    if len(mapped) != len(claimed) or mapped != {normalized(k): v for k, v in log_hashes.items()}:
        raise RuntimeError('Original log hashes differ from reviewed cancellation evidence')
    items = evidence.get('items')
    if not isinstance(items, list) or not items:
        raise RuntimeError('Judge cancellation item list must be nonempty')
    for item in items:
        if (not isinstance(item, dict) or not isinstance(item.get('id'), str)
                or not isinstance(item.get('item_sha256'), str) or not re.fullmatch('[0-9a-f]{64}', item['item_sha256'])
                or item.get('decision') != 'cancelled_by_target_termination'
                or item.get('unity_target_only') is not True
                or (evidence['version'] == 1 and item.get('outer_js_no_external_writes') is not True)
                or not isinstance(item.get('request_summary'), str) or not item['request_summary'].strip()):
            raise RuntimeError('Each cancellation needs an exact reviewed Unity-only request hash and summary')
        if evidence['version'] == 2:
            validate_completed_evidence_writer(item, evidence, project, log_hashes)
    actors, instances = evidence.get('known_actor_pids'), evidence.get('known_instance_ids')
    if (not isinstance(actors, list) or not actors or any(type(pid) is not int or pid <= 0 for pid in actors)
            or len(set(actors)) != len(actors) or not isinstance(instances, list) or not instances
            or any(not isinstance(x, str) or not re.fullmatch(r'[^\s@]+@[a-fA-F0-9]{8,64}', x) for x in instances)
            or len(set(instances)) != len(instances)):
        raise RuntimeError('Cancellation evidence needs known actor PIDs and exact native instance identifiers')
    return evidence

def validate_readonly_background(path, group, project, log_hashes):
    evidence = read(path)
    if (type(evidence.get('version')) is not int or evidence['version'] != 1 or evidence.get('group') != group
            or not isinstance(evidence.get('project'), str) or normalized(evidence['project']) != normalized(project)
            or evidence.get('decision') != 'unresolved_background_verified_nonwriting'
            or evidence.get('reviewed') is not True or not isinstance(evidence.get('reviewer'), str)
            or not evidence['reviewer'].strip()):
        raise RuntimeError('Nonwriting review needs exact scope and explicit judge decision')
    hashes = evidence.get('log_sha256')
    if (not isinstance(hashes, dict) or not hashes
            or {normalized(k): v for k, v in hashes.items()} != {normalized(k): v for k, v in log_hashes.items()}
            or len({normalized(k) for k in hashes}) != len(hashes)):
        raise RuntimeError('Nonwriting review original log hashes differ')
    items = evidence.get('items')
    if not isinstance(items, list) or not items:
        raise RuntimeError('Nonwriting review requires exact original call entries')
    for entry in items:
        if (not isinstance(entry, dict) or not isinstance(entry.get('handle'), str)
                or not re.fullmatch(r'exec:[0-9]+', entry['handle'])
                or not isinstance(entry.get('session_id'), str) or not entry['session_id']
                or not isinstance(entry.get('call_id'), str) or not entry['call_id']
                or not isinstance(entry.get('call_sha256'), str) or not re.fullmatch('[0-9a-f]{64}', entry['call_sha256'])
                or entry.get('classification') != 'finite_sleep_optional_single_log_tail'):
            raise RuntimeError('Nonwriting review has malformed exact-call binding/classification')
    actors, instances = evidence.get('known_actor_pids'), evidence.get('known_instance_ids', [])
    if (not isinstance(actors, list) or not actors or any(type(pid) is not int or pid <= 0 for pid in actors)
            or len(set(actors)) != len(actors) or not isinstance(instances, list)
            or any(not isinstance(x, str) or not re.fullmatch(r'[^\s@]+@[a-fA-F0-9]{8,64}', x) for x in instances)
            or len(set(instances)) != len(instances)):
        raise RuntimeError('Nonwriting review needs known actor identities')
    return evidence


def logs_proof(own, cancellations=None, readonly_evidence=None, project=None):
    attempts, sessions, files = log_files(own)
    result = {'sessions': rollout_pairs(sessions, readonly_evidence, project), 'native': native_pairs(attempts, cancellations),
              'assets': asset_pairs(own / 'asset-tools.jsonl')}
    result['known_background_handles'] = sorted({handle for record in result['sessions'].values()
                                                 for handle in record['background_started']})
    result['unresolved_background_handles'] = sorted({entry['handle'] for record in result['sessions'].values()
                                                      for entry in record['unresolved_background_nonwriting']})
    result['nonwriting_background_handles'] = result['unresolved_background_handles'][:]
    result['files'] = {str(path): runner.digest(path) for path in files}
    return result

def save_inventory(path):
    return {'exists': path.exists(), 'files': runner.inventory(path) if path.exists() else {}}

@contextmanager
def defer_seal_publication(destination):
    original = runner.write_json
    target = (destination / 'seal.json').resolve()
    def staged(path, value):
        if Path(path).resolve() == target:
            return original(destination / 'seal.pending.json', value)
        return original(path, value)
    runner.write_json = staged
    try:
        yield
    finally:
        runner.write_json = original

def seal_closed(admin, group, census_collector=collect_census, cancelled_unity_evidence=None,
                readonly_background_evidence=None):
    admin = Path(admin).resolve()
    manifest = runner.manifest(admin)
    if group not in manifest['groups']:
        raise RuntimeError('Unknown frozen group')
    own = admin / 'groups' / group
    state_path = own / 'state.json'
    original = read(state_path)
    if original.get('status') not in runner.TERMINAL or original.get('process_tree_stopped') is not True or original.get('actual_model_verified') is not True:
        raise RuntimeError('Terminal, stopped and actual-model-verified state required')
    destination = admin / 'sealed' / group
    if destination.exists():
        raise RuntimeError('Refusing to overwrite an existing or partial sealed result')
    if original.get('terminal_drain', {}).get('passed') is True:
        raise RuntimeError('Existing positive drain evidence must use ordinary sealing')
    review_id = uuid.uuid4().hex
    review = own / 'closed-seal-reviews' / review_id
    review.mkdir(parents=True)
    shutil.copy2(state_path, review / 'original-state.json')
    failed_report = own / 'terminal-drain-report.json'
    if failed_report.exists():
        shutil.copy2(failed_report, review / 'original-failed-drain-report.json')
    project = Path(manifest['groups'][group]['project'])
    product = project.parent.name
    saves = Path(os.environ.get('USERPROFILE', str(Path.home()))) / 'AppData/LocalLow/SJSBenchmark' / ('AITest_' + product)
    proof_path = own / 'terminal-drain-closed-proof.json'
    if proof_path.exists():
        raise RuntimeError('A previous closed-editor proof requires manual judge review, not overwrite')
    try:
        cancellations = None
        if cancelled_unity_evidence is not None:
            _, _, files = log_files(own)
            cancellations = validate_cancellations(cancelled_unity_evidence, group, project,
                                                 {str(path): runner.digest(path) for path in files})
        readonly_review = None
        if readonly_background_evidence is not None:
            _, _, files = log_files(own)
            readonly_review = validate_readonly_background(readonly_background_evidence, group, project,
                                                          {str(path): runner.digest(path) for path in files})
        proof = logs_proof(own, cancellations, readonly_review, project)
        census = census_collector()
        proof['census'] = census
        instances = sorted({x for reviewdoc in (cancellations, readonly_review) if reviewdoc
                            for x in reviewdoc.get('known_instance_ids', [])})
        actors = sorted({x for reviewdoc in (cancellations, readonly_review) if reviewdoc
                         for x in reviewdoc['known_actor_pids']})
        proof['process_verification'] = verify_census(census, project, own, original, proof['sessions'], instances, actors)
        if cancellations:
            evidence_copy = own / 'terminal-drain-closed-cancellations.json'
            if evidence_copy.exists():
                raise RuntimeError('Refusing to overwrite an existing judge cancellation evidence copy')
            shutil.copy2(cancelled_unity_evidence, evidence_copy)
            if read(evidence_copy) != cancellations:
                raise RuntimeError('Judge cancellation evidence changed after validation')
            proof['judge_cancellation_evidence'] = {'path': str(Path(cancelled_unity_evidence).resolve()),
                'sha256': runner.digest(evidence_copy), 'sealed_filename': evidence_copy.name, 'document': cancellations}
        if readonly_review:
            readonly_copy = own / 'terminal-drain-closed-nonwriting.json'
            if readonly_copy.exists():
                raise RuntimeError('Refusing to overwrite existing nonwriting judge evidence')
            shutil.copy2(readonly_background_evidence, readonly_copy)
            if read(readonly_copy) != readonly_review:
                raise RuntimeError('Nonwriting review changed after validation')
            proof['judge_nonwriting_evidence'] = {'sha256': runner.digest(readonly_copy),
                'path': str(Path(readonly_background_evidence).resolve()), 'sealed_filename': readonly_copy.name,
                'document': readonly_review}
        proof.update(version=1, review_id=review_id, utc=runner.utc(), mode='closed_editor_verified',
                     project=str(project), source_before=runner.inventory(project), saves_before=save_inventory(saves),
                     original_state_sha256=runner.digest(review / 'original-state.json'))
        runner.write_json(proof_path, proof)
        shutil.copy2(proof_path, review / 'precopy-proof.json')
        state = copy.deepcopy(original)
        state['terminal_drain'] = {'passed': True, 'mode': 'closed_editor_verified', 'quiescent': True,
                                   'editor_restarted': False, 'no_known_writers': True,
                                   'proof_path': str(proof_path), 'proof_sha256': runner.digest(proof_path)}
        state.update(needs_seal_review=False, closed_seal_review_id=review_id)
        state.pop('seal_error', None)
        runner.write_json(state_path, state)
        # The waiting pipeline must never see seal.json before both stable-copy
        # and second process-census verification have completed.
        with defer_seal_publication(destination):
            runner.seal(admin, group)
        after_census = census_collector()
        verification = verify_census(after_census, project, own, original, proof['sessions'], instances, actors)
        if logs_proof(own, cancellations, readonly_review, project)['files'] != proof['files']:
            raise RuntimeError('Tool/session logs changed during terminal copy')
        if cancellations and (runner.digest(cancelled_unity_evidence) != proof['judge_cancellation_evidence']['sha256']
                or runner.digest(destination / evidence_copy.name) != proof['judge_cancellation_evidence']['sha256']):
            raise RuntimeError('Judge cancellation evidence changed during terminal copy')
        if readonly_review and (runner.digest(readonly_background_evidence) != proof['judge_nonwriting_evidence']['sha256']
                or runner.digest(destination / readonly_copy.name) != proof['judge_nonwriting_evidence']['sha256']):
            raise RuntimeError('Nonwriting judge evidence changed during terminal copy')
        after_source, after_saves = runner.inventory(project), save_inventory(saves)
        copied_source = runner.inventory(destination / 'project')
        copied_saves = save_inventory(destination / 'saves')
        if (after_source != proof['source_before'] or copied_source != proof['source_before']
                or after_saves != proof['saves_before'] or copied_saves != proof['saves_before']):
            raise RuntimeError('Source, saves or copied hashes changed across terminal snapshot')
        post = {'utc': runner.utc(), 'passed': True, 'review_id': review_id, 'census': after_census,
                'process_verification': verification, 'source_files': after_source, 'saves': after_saves,
                'precopy_proof_sha256': runner.digest(proof_path)}
        runner.write_json(review / 'postcopy-proof.json', post)
        runner.write_json(destination / 'closed-editor-postcopy.json', post)
        seal = read(destination / 'seal.pending.json')
        (destination / 'seal.pending.json').rename(destination / 'closed-editor-runner-manifest.json')
        seal['closed_editor_verification'] = {'review_id': review_id, 'precopy_sha256': runner.digest(proof_path),
                                             'postcopy_sha256': runner.digest(destination / 'closed-editor-postcopy.json')}
        seal['all_files'] = runner.inventory(destination)
        seal['all_files'].pop('seal.json', None)
        runner.write_json(destination / 'seal.json', seal)
        return {'sealed': str(destination), 'mode': 'closed_editor_verified', 'review_id': review_id}
    except Exception as exc:
        failure = {'utc': runner.utc(), 'passed': False, 'review_id': review_id, 'error': str(exc)}
        runner.write_json(review / 'failure.json', failure)
        current = read(state_path)
        current.update(needs_seal_review=True, seal_error=str(exc), closed_seal_review_id=review_id)
        if current.get('terminal_drain', {}).get('mode') == 'closed_editor_verified':
            current['terminal_drain']['passed'] = False
        runner.write_json(state_path, current)
        if (destination / 'seal.json').exists():
            (destination / 'seal.json').rename(destination / 'seal-unverified.json')
        raise

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--admin', type=Path, default=runner.DEFAULT_ADMIN)
    parser.add_argument('--group', required=True)
    parser.add_argument('--cancelled-unity-evidence', type=Path)
    parser.add_argument('--readonly-background-evidence', type=Path)
    args = parser.parse_args(argv)
    # Same Windows UTF-8 infrastructure convention; no model or Unity command.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    print(json.dumps(seal_closed(args.admin, args.group, cancelled_unity_evidence=args.cancelled_unity_evidence,
                                readonly_background_evidence=args.readonly_background_evidence), ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
