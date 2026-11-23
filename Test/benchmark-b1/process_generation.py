"""Pure process-generation checks; never collect processes or authorize closure.

Callers first run the frozen helper against the complete, unfiltered census for
direct scope, unknown Unity processes, real CLI/Editor roots and actual collector
exclusions. These functions separately account for archived auxiliary generations.
No process name, null command line or missing executable path grants an exclusion.
"""
import re


def require(value, message):
    if not value:
        raise RuntimeError(message)


def _row(value):
    require(isinstance(value, dict)
            and type(value.get('ProcessId')) is int and value['ProcessId'] >= 0
            and type(value.get('ParentProcessId')) is int and value['ParentProcessId'] >= 0
            and isinstance(value.get('Name'), str)
            and 'CommandLine' in value and 'ExecutablePath' in value
            and (value['CommandLine'] is None or isinstance(value['CommandLine'], str))
            and (value['ExecutablePath'] is None or isinstance(value['ExecutablePath'], str)),
            'Malformed actual archived CIM row')
    require(value['ProcessId'] != 0 or (value['Name'].casefold() in ('system idle process', 'idle')
            and value['CommandLine'] in (None, '')), 'Malformed archived PID-zero identity')
    return {**value, 'CreationDate': value.get('CreationDate')}


def archived_cim_rows(value):
    """Extract original CIM rows, retaining nullable columns and unknown creation.

    Missing or malformed PID/PPID cannot establish a graph and is rejected rather
    than skipped. Creation is required once a row is scoped or a descendant.
    """
    rows = []
    def visit(part):
        if isinstance(part, dict):
            if {'ProcessId', 'ParentProcessId', 'Name'} <= part.keys():
                rows.append(_row(part))
            else:
                for nested in part.values():
                    visit(nested)
        elif isinstance(part, list):
            for nested in part:
                visit(nested)
    visit(value)
    return rows


def creation_ms(value):
    require(isinstance(value, str), 'Missing or untyped process creation generation')
    match = re.fullmatch(r'/Date\(([0-9]+)\)/', value)
    require(match is not None, 'Unknown process creation serialization; no PID-only lineage guess')
    return int(match[1])


def actual_generation_tree(candidates, actor_roots, direct_scope):
    """Keep true archived descendants; reject only temporally impossible edges.

    ``direct_scope`` receives all rows, including nullable command/path. A row
    identified directly remains selected even when its numeric parent is reused.
    Returned rows are evidence anchors, not permanent numeric known-PID seeds.
    """
    require(isinstance(actor_roots, (set, list, tuple))
            and all(type(pid) is int and pid > 0 for pid in actor_roots), 'Malformed actor roots')
    by_pid = {}
    for value in candidates:
        row = _row(value)
        pid = row['ProcessId']
        if pid in by_pid:
            require(by_pid[pid] == row, 'Conflicting process generation rows in one original census')
        by_pid[pid] = row
    selected = {pid for pid, row in by_pid.items() if pid in actor_roots or direct_scope(row)}
    for pid in selected:
        creation_ms(by_pid[pid]['CreationDate'])
    rejected = []
    changed = True
    while changed:
        changed = False
        for pid, row in by_pid.items():
            parent = row['ParentProcessId']
            if pid in selected or parent not in selected:
                continue
            child_ms, parent_ms = creation_ms(row['CreationDate']), creation_ms(by_pid[parent]['CreationDate'])
            if child_ms < parent_ms:
                item = {'pid': pid, 'parent_pid': parent, 'child_creation_ms': child_ms,
                        'parent_creation_ms': parent_ms, 'decision': 'impossible_for_this_parent_generation_only'}
                if item not in rejected:
                    rejected.append(item)
                continue
            selected.add(pid)
            changed = True
    return [by_pid[pid] for pid in sorted(selected)], rejected


def verify_auxiliary_absence(census, generations, excluded):
    """Supplement the frozen full-census guard using exact auxiliary anchors.

    ``excluded`` must be the frozen helper's proved own collector exclusions.
    An exact strictly later current creation proves only that historical PID
    generation ended. Children between the old and new creation, including both
    millisecond boundaries, remain possible historical orphans and block. A child
    strictly newer than that current generation cannot belong to the old one.
    The complete current tree still needs the caller's original full-scope and
    unknown-Unity guard. Raw rows/anchors are never removed or rewritten.
    """
    require(isinstance(census, dict) and isinstance(census.get('processes'), list), 'Missing raw full census rows')
    require(isinstance(generations, dict) and isinstance(excluded, (set, list, tuple))
            and all(type(pid) is int and pid > 0 for pid in excluded), 'Malformed generation/exclusion inputs')
    by_pid = {}
    for value in census['processes']:
        row = _row(value)
        require(row['ProcessId'] not in by_pid, 'Duplicate raw census PID')
        by_pid[row['ProcessId']] = row
    impossible_edges, reused_generations, newer_generation_edges = [], [], []
    for pid, value in generations.items():
        anchor = _row(value)
        require(type(pid) is int and pid > 0 and anchor['ProcessId'] == pid, 'Auxiliary generation identity mismatch')
        parent_born = creation_ms(anchor['CreationDate'])
        current_born = None
        if pid in by_pid and pid not in excluded:
            current = by_pid[pid]
            current_born = creation_ms(current['CreationDate'])
            require(current_born > parent_born,
                    'Present historical auxiliary PID generation or ambiguous PID reuse: ' + str(pid))
            reused_generations.append({'pid': pid, 'historical_generation': anchor,
                'current_generation': current, 'historical_creation_ms': parent_born,
                'current_creation_ms': current_born,
                'decision': 'strictly_later_current_generation_ends_historical_generation_only'})
        for row in by_pid.values():
            if row['ProcessId'] in excluded or row['ParentProcessId'] != pid:
                continue
            born = creation_ms(row['CreationDate'])
            if born < parent_born:
                impossible_edges.append({'pid': row['ProcessId'], 'parent_pid': pid,
                    'child_creation_ms': born, 'parent_creation_ms': parent_born,
                    'decision': 'impossible_for_this_historical_parent_generation_only'})
            elif current_born is not None and born > current_born:
                newer_generation_edges.append({'pid': row['ProcessId'], 'parent_pid': pid,
                    'child_generation': row, 'child_creation_ms': born,
                    'historical_parent_creation_ms': parent_born, 'current_parent_creation_ms': current_born,
                    'decision': 'strictly_newer_than_reused_parent_generation_not_a_historical_descendant',
                    'current_tree_still_requires_unmodified_full_scope_guard': True})
            else:
                raise RuntimeError('Live or ambiguous descendant of historical auxiliary generation: ' + str(row['ProcessId']))
    return {'no_live_historical_auxiliary_generations': True, 'checked_generation_count': len(generations),
            'excluded_impossible_numeric_edges': impossible_edges,
            'excluded_reused_pid_generations': reused_generations,
            'excluded_newer_generation_edges': newer_generation_edges,
            'direct_scope_and_unknown_unity_checked_by_unmodified_frozen_helper': True}
