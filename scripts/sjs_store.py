"""Durable JSONL store, disposable snapshots and one controller writer."""
from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from sjs_config import (ROOT, SJSError, canonical, digest, prepare_config, require,
                        template_identity, validate_overrides)
from sjs_model import actor_record, empty_state, reduce_command, TERMINAL

STREAMS = ("tasks", "direction")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def read_json(path):
    def invalid(value):
        raise ValueError("non-finite JSON number: " + value)
    return json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=invalid)


def atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(canonical(payload) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def workspace_for(instance):
    root = Path(instance).resolve()
    for p in [root.parent, *root.parent.parents]:
        if (p / ".git").exists():
            return p
    return root.parent


def private_dir(instance):
    root = Path(instance).resolve()
    return workspace_for(root) / ".serial-job-session" / "private" / digest(str(root))[:20]


def process_alive(pid):
    if pid == os.getpid():
        return True
    if os.name == "nt":
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            # Access denied means the process may still own this lease.
            return ctypes.get_last_error() == 5
        code = ctypes.c_ulong()
        ok = kernel.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))
        kernel.CloseHandle(ctypes.c_void_p(handle))
        return not ok or code.value == 259
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


@contextmanager
def file_lock(path, *, break_stale=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    if break_stale and path.exists():
        held = read_json(path)
        require(isinstance(held.get("pid"), int) and not process_alive(held["pid"]), "LOCK_OWNER_ALIVE", owner=held)
        # Never break a lease automatically; retain its metadata for recovery.
        os.replace(path, path.with_name(path.name + ".stale-" + token))
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise SJSError("WRITER_BUSY", lock=str(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(canonical({"pid": os.getpid(), "token": token, "created_at": now()}))
            f.flush()
            os.fsync(f.fileno())
        yield
    finally:
        if path.exists() and read_json(path).get("token") == token:
            path.unlink()


def segment_name(stream, index):
    return "records/%s-%06d.jsonl" % (stream, index)


def load_run(instance):
    root = Path(instance).resolve()
    if not (root / "run.json").is_file():
        if (root / "INSTANCE.json").exists():
            raise SJSError("UNSUPPORTED_PROTOCOL", "v5 requires a new instance; v3/v4 execution and migration are not provided")
        raise SJSError("INSTANCE_NOT_FOUND")
    require((root / "run.json").resolve().is_relative_to(root), "RUN_PATH_ESCAPE")
    run = read_json(root / "run.json")
    require(run.get("schema") == "sjs-run/v5", "UNSUPPORTED_PROTOCOL")
    return run


def _save_state(root, state):
    cache = copy.deepcopy(state)
    cache.pop("checksum", None)
    cache["checksum"] = digest(cache)
    atomic_json(Path(root) / "state.json", cache)


def _cached_state(root):
    try:
        require((Path(root) / "state.json").resolve().is_relative_to(Path(root).resolve()), "SNAPSHOT_PATH_ESCAPE")
        state = read_json(Path(root) / "state.json")
        checksum = state.pop("checksum")
        require(state.get("schema") == "sjs-state/v5" and digest(state) == checksum, "SNAPSHOT_INVALID")
        require(all(k in state for k in ("config", "commands", "cursors", "segments", "run_hash")), "SNAPSHOT_INVALID")
        return state
    except (OSError, ValueError, KeyError, TypeError, SJSError):
        return None


def _read_segment(root, rel, offset=0):
    path = Path(root) / rel
    require(path.resolve().is_relative_to(Path(root).resolve()), "LOG_PATH_ESCAPE")
    require(path.stat().st_size >= offset, "LOG_TRUNCATED", path=rel)
    if path.stat().st_size == offset:
        return []
    rows = []
    with path.open("rb") as f:
        f.seek(offset)
        while True:
            start = f.tell()
            raw = f.readline()
            if not raw:
                break
            require(raw.endswith(b"\n"), "TORN_TAIL", path=rel, offset=start)
            try:
                event = json.loads(raw)
                checksum = event.pop("checksum")
                require(event.get("schema") == "sjs-event/v5" and digest(event) == checksum, "EVENT_CHECKSUM_MISMATCH", path=rel, offset=start)
            except (ValueError, KeyError, TypeError) as exc:
                raise SJSError("CORRUPT_LOG", path=rel, offset=start) from exc
            require(event.get("stream") == rel.split("/")[-1].split("-")[0], "STREAM_MISMATCH", path=rel)
            rows.append((event, rel, f.tell()))
    return rows


def _terminal_count(before, after):
    return sum(not t.get("parent_id") and t["status"] in TERMINAL
               and before["tasks"].get(k, {}).get("status") not in TERMINAL for k, t in after["tasks"].items())


def _consume(state, event, rel, offset):
    require(event.get("seq") == state["revision"] + 1, "LOG_SEQUENCE_GAP", seq=event.get("seq"))
    require(event["command_id"] not in state["commands"], "DUPLICATE_COMMITTED_COMMAND")
    after, _ = reduce_command(state, event, replay=True)
    stream = event["stream"]
    index = int(Path(rel).stem.split("-")[-1])
    segment = after["segments"].get(stream, {"index": index, "count": 0})
    count = segment["count"] if segment["index"] == index else 0
    count += _terminal_count(state, after) if stream == "tasks" else len(event["operations"])
    after["segments"][stream] = {"index": index, "count": count}
    after["cursors"][rel] = offset
    return after


def _tail(root, state, *, full=False):
    rows, present = [], {}
    for stream in STREAMS:
        if full:
            indices = sorted(int(p.stem.split("-")[-1]) for p in (Path(root) / "records").glob(stream + "-*.jsonl"))
            require(indices == list(range(1, len(indices) + 1)), "SEGMENT_SEQUENCE_GAP", stream=stream)
        index = 1 if full else state["segments"].get(stream, {"index": 1})["index"]
        while (Path(root) / segment_name(stream, index)).exists():
            rel = segment_name(stream, index)
            rows.extend(_read_segment(root, rel, 0 if full else state["cursors"].get(rel, 0)))
            present[stream] = index
            index += 1
        require(stream in present, "STREAM_MISSING", stream=stream)
    for event, rel, offset in sorted(rows, key=lambda row: row[0]["seq"]):
        state = _consume(state, event, rel, offset)
    for stream, index in present.items():
        if state["segments"].get(stream, {}).get("index", 0) < index:
            state["segments"][stream] = {"index": index, "count": 0}
        state["cursors"].setdefault(segment_name(stream, index), 0)
    return state


def load_snapshot(instance, *, verify_template=True):
    root = Path(instance).resolve()
    run = load_run(root)
    state = _cached_state(root)
    state = _tail(root, state or empty_state(), full=state is None)
    require(state.get("run_hash") == digest(run), "RUN_CONFIG_CHANGED", message="Creation config is immutable; use recorded policy changes or create a new instance")
    if verify_template:
        require(state["config"].get("template") == template_identity(), "TEMPLATE_CHANGED", expected=state["config"].get("template"), actual=template_identity())
    return state


def rebuild(instance, *, persist=True):
    root = Path(instance).resolve()
    def build():
        run = load_run(root)
        state = _tail(root, empty_state(), full=True)
        require(state.get("run_hash") == digest(run), "RUN_CONFIG_CHANGED")
        if persist:
            _save_state(root, state)
        return state
    if persist:
        with file_lock(private_dir(root) / "writer.lock"):
            return build()
    return build()


def _append(path, event):
    event = copy.deepcopy(event)
    event["checksum"] = digest(event)
    raw = (canonical(event) + "\n").encode("utf-8")
    with Path(path).open("ab") as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
        return f.tell()


def _rotate(root, state, stream):
    segment = state["segments"][stream]
    rel = segment_name(stream, segment["index"])
    limits = state["config"]["rotation"]
    if segment["count"] < limits[stream] and (Path(root) / rel).stat().st_size < limits["bytes"]:
        return
    index = segment["index"] + 1
    new_rel = segment_name(stream, index)
    with (Path(root) / new_rel).open("xb") as f:
        f.flush()
        os.fsync(f.fileno())
    state["segments"][stream] = {"index": index, "count": 0}
    state["cursors"][new_rel] = 0


def _prepare(root, state, request):
    command = copy.deepcopy(request)
    require(isinstance(command.get("command_id"), str) and command["command_id"].strip(), "COMMAND_ID_REQUIRED")
    actor = actor_record(command.get("actor"))
    require(actor.get("role", "orchestrator") == "orchestrator" or actor["kind"] == "human", "CONTROL_WRITER_REQUIRED")
    operations = command.get("operations")
    require(isinstance(operations, list) and operations and all(isinstance(o, dict) for o in operations), "OPERATIONS_REQUIRED")
    direction = [o.get("op") == "assess_direction" for o in operations]
    require(all(direction) or not any(direction), "CROSS_STREAM_TRANSACTION")
    command["stream"] = "direction" if all(direction) else "tasks"
    command["request_hash"] = digest({"operations": operations, "actor": actor, "stream": command["stream"]})
    previous = state["commands"].get(command["command_id"])
    if previous:
        require(previous["request_hash"] == command["request_hash"], "COMMAND_ID_COLLISION")
        return None, {"ok": True, "duplicate": True, "revision": state["revision"], **previous}
    require(isinstance(command.get("expected_revision"), int) and not isinstance(command["expected_revision"], bool), "EXPECTED_REVISION_REQUIRED")
    require(command["expected_revision"] == state["revision"], "REVISION_CONFLICT", actual=state["revision"])
    import schema_validate
    errors = schema_validate.validate_named(command, "command.schema.json")
    require(not errors, "INVALID_COMMAND", errors=errors)
    import check_skill_integrity
    integrity = check_skill_integrity.check_manifest()
    require(integrity["ok"], "TEMPLATE_INTEGRITY_FAILED", integrity=integrity)
    changed_template = state["config"].get("template") != template_identity()
    require(not changed_template or len(operations) == 1 and operations[0].get("op") == "rebind_template", "TEMPLATE_CHANGED")
    known_tasks = set(state["tasks"])
    for op in operations:
        if op.get("op") == "initialize":
            raise SJSError("INITIALIZE_ONLY_AT_CREATION")
        if op.get("op") == "create":
            known_tasks.add(op.get("task_id"))
        elif op.get("task_id"):
            require(op["task_id"] in known_tasks, "TASK_NOT_FOUND", task_id=op["task_id"])
        if "overrides" in op:
            validate_overrides(op["overrides"], root)
        if op.get("op") == "start":
            task = state["tasks"].get(op.get("task_id"), {})
            for overrides in (state["config"].get("overrides", []), task.get("overrides", [])):
                validate_overrides(copy.deepcopy(overrides), root)
        if op.get("op") == "timing":
            require(state["config"]["modules"]["estimates"], "ESTIMATES_DISABLED")
            import sjs_estimates
            op["sample"] = sjs_estimates.normalize_sample(op["sample"])
        if op.get("op") == "rebind_template":
            require(op.get("template") == template_identity(), "INVALID_TEMPLATE_IDENTITY")
    command["schema"] = "sjs-event/v5"
    command["seq"] = state["revision"] + 1
    command["at"] = now()
    return command, None


def _commit(root, state, request):
    command, duplicate = _prepare(root, state, request)
    if duplicate:
        return duplicate
    after, outcomes = reduce_command(state, command)
    # All validation precedes the only authoritative write. A snapshot failure
    # after fsync is safe: subsequent reads replay this complete envelope.
    stream = command["stream"]
    rel = segment_name(stream, state["segments"][stream]["index"])
    offset = _append(Path(root) / rel, command)
    after["cursors"][rel] = offset
    after["segments"][stream]["count"] += _terminal_count(state, after) if stream == "tasks" else len(command["operations"])
    _rotate(root, after, stream)
    _save_state(root, after)
    return {"ok": True, "revision": after["revision"], "command_id": command["command_id"], "outcomes": outcomes}


def apply_command(instance, request):
    root = Path(instance).resolve()
    load_run(root)
    with file_lock(private_dir(root) / "writer.lock"):
        return _commit(root, load_snapshot(root, verify_template=False), request)


def normalize_route(key):
    return "".join(key.casefold().split())


def resolve_route(route_key, workspace=None):
    workspace = Path(workspace or workspace_for(Path.cwd() / "placeholder"))
    path = workspace / ".serial-job-session" / "instances.json"
    require(path.is_file(), "ROUTE_NOT_FOUND")
    registry = read_json(path)
    require(registry.get("schema") == "sjs-registry/v5", "UNSUPPORTED_REGISTRY")
    entry = registry["instances"].get(normalize_route(route_key))
    require(entry is not None, "ROUTE_NOT_FOUND")
    return (workspace / entry["path"]).resolve()


def initialize(instance, raw, *, dry_run=False, register=True):
    root = Path(instance).resolve()
    prepared = prepare_config(raw)
    if not prepared["ok"]:
        return prepared
    require(not root.is_relative_to(ROOT.resolve()), "INSTANCE_INSIDE_TEMPLATE")
    require(not root.exists(), "TARGET_EXISTS")
    cfg = prepared["config"]
    workspace = Path(cfg.get("workspace", workspace_for(root))).resolve()
    require(not workspace.is_relative_to(root), "WORKSPACE_INSIDE_INSTANCE")
    cfg["workspace"] = str(workspace)
    cfg["instance_id"] = str(uuid.uuid4())
    cfg["template"] = template_identity()
    if raw.get("template"):
        require(raw["template"] == cfg["template"], "TEMPLATE_CHANGED")
    if dry_run:
        return {"ok": True, "dry_run": True, "config": cfg, "files": ["run.json", "state.json", segment_name("tasks", 1), segment_name("direction", 1)]}
    # Maintainer edits must be reflected in the release baseline first.
    import check_skill_integrity
    integrity = check_skill_integrity.check_manifest()
    require(integrity["ok"], "TEMPLATE_INTEGRITY_FAILED", integrity=integrity)
    root.parent.mkdir(parents=True, exist_ok=True)
    registry_file = workspace / ".serial-job-session" / "instances.json"
    @contextmanager
    def registration():
        if register:
            with file_lock(registry_file.with_suffix(".lock")):
                registry = read_json(registry_file) if registry_file.exists() else {"schema": "sjs-registry/v5", "instances": {}}
                require(registry.get("schema") == "sjs-registry/v5", "UNSUPPORTED_REGISTRY")
                require(normalize_route(cfg["route_key"]) not in registry["instances"], "ROUTE_KEY_CONFLICT")
                yield registry
        else:
            yield None
    with registration() as registry:
        staging = Path(tempfile.mkdtemp(prefix="." + root.name + "-", dir=root.parent)).resolve()
        try:
            (staging / "records").mkdir()
            # Store only explicit parameters in the creation file. The first
            # log envelope captures effective defaults once for deterministic
            # replay, including after an explicit template rebind.
            run = {**clean_config(raw), "schema": "sjs-run/v5", "instance_id": cfg["instance_id"],
                   "workspace": cfg["workspace"], "template": cfg["template"]}
            atomic_json(staging / "run.json", run)
            event = {"schema": "sjs-event/v5", "command_id": "initialize", "request_hash": digest(run),
                     "seq": 1, "stream": "tasks", "at": now(),
                     "actor": {"kind": "human", "id": "explicit-creation-config"},
                     "operations": [{"op": "initialize", "config": cfg, "run_hash": digest(run)}]}
            (staging / segment_name("direction", 1)).touch()
            offset = _append(staging / segment_name("tasks", 1), event)
            state = _consume(empty_state(), event, segment_name("tasks", 1), offset)
            state["segments"]["direction"] = {"index": 1, "count": 0}
            state["cursors"][segment_name("direction", 1)] = 0
            _save_state(staging, state)
            os.rename(staging, root)
            if registry is not None:
                relative = os.path.relpath(root, workspace).replace("\\", "/")
                registry["instances"][normalize_route(cfg["route_key"])] = {"id": cfg["instance_id"], "path": relative}
                atomic_json(registry_file, registry)
        finally:
            if staging.exists():
                require(staging.is_relative_to(root.parent.resolve()) and staging != root, "UNSAFE_STAGING_CLEANUP")
                shutil.rmtree(staging)
    return {"ok": True, "instance": str(root), "instance_id": cfg["instance_id"], "revision": 1}


def clean_config(raw):
    from sjs_config import clean
    return clean(raw)


def check_instance(instance, *, integrity=True):
    root = Path(instance).resolve()
    rebuilt = rebuild(root, persist=False)
    snapshot = _cached_state(root)
    warnings = []
    if snapshot is None or digest(snapshot) != digest(rebuilt):
        warnings.append("SNAPSHOT_REBUILD_RECOMMENDED")
    errors = []
    if rebuilt["config"]["template"] != template_identity():
        errors.append("TEMPLATE_CHANGED")
    if integrity:
        import check_skill_integrity
        if not check_skill_integrity.check_manifest()["ok"]:
            errors.append("TEMPLATE_INTEGRITY_FAILED")
    for path in root.rglob("*"):
        if path.is_file() and (len(path.relative_to(root).parts) > 2 or not path.resolve().is_relative_to(root)):
            errors.append("INSTANCE_LAYOUT_VIOLATION:" + path.relative_to(root).as_posix())
    return {"ok": not errors, "revision": rebuilt["revision"], "tasks": len(rebuilt["tasks"]), "errors": errors, "warnings": warnings}


def recover(instance, *, dry_run=True, break_stale=False):
    root = Path(instance).resolve()
    load_run(root)
    def inspect():
        repairs = []
        for stream in STREAMS:
            files = sorted((root / "records").glob(stream + "-*.jsonl"))
            for path in files:
                with path.open("rb") as f:
                    while True:
                        offset = f.tell()
                        raw = f.readline()
                        if not raw:
                            break
                        if not raw.endswith(b"\n"):
                            require(path == files[-1], "TORN_SEALED_SEGMENT")
                            repairs.append((path, offset, raw))
        return repairs
    if dry_run:
        return {"ok": True, "dry_run": True, "repairs": [{"path": p.relative_to(root).as_posix(), "offset": o, "bytes": len(b)} for p, o, b in inspect()]}
    with file_lock(private_dir(root) / "writer.lock", break_stale=break_stale):
        backups = []
        for path, offset, fragment in inspect():
            backup = private_dir(root) / (path.name + ".fragment-" + uuid.uuid4().hex + ".bin")
            with backup.open("xb") as f:
                f.write(fragment)
                f.flush()
                os.fsync(f.fileno())
            with path.open("r+b") as f:
                f.truncate(offset)
                f.flush()
                os.fsync(f.fileno())
            backups.append(str(backup))
        state = rebuild(root, persist=False)
        _save_state(root, state)
        return {"ok": True, "revision": state["revision"], "fragment_backups": backups}
