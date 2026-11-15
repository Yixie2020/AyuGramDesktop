#!/usr/bin/env python3

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import subprocess
import sys
import time


TAG_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]*")
TASK_ID_PATTERN = re.compile(
	r"[0-9]{4}/[0-9]{2}/[0-9]{2}/[a-z0-9][a-z0-9-]*"
)
VALID_STATUSES = {"todo", "in-progress", "approved", "blocked"}
DEFAULT_TASK_TYPE = "implement"
VALID_TASK_TYPES = {DEFAULT_TASK_TYPE, "verify"}
VALID_FINDINGS = {"confirmed", "deviation", "inconclusive"}
COMMIT_HASH_PATTERN = re.compile(
	r"(?i)\b(?:commit|revision|sha(?:-1)?)\b[^\r\n]{0,32}(?<!#)\b[0-9a-f]{7,64}\b"
)
LEGACY_COMMIT_FIELDS = ("Task-Base-SHA:", "Implementation-SHA:")
PROJECT_ARCHIVE_DIR = "archive"
PROJECT_LINK_PATTERN = re.compile(r"\]\(\.\./\.\./(?!\.\./)")
ARCHIVED_PROJECT_LINK_PATTERN = re.compile(r"\]\(\.\./\.\./\.\./")
STATE_FIELD_ORDER = [
	"status",
	"type",
	"created",
	"project",
	"depends_on",
	"claimed_by",
	"claimed_at",
	"claim_order",
	"lease_until",
	"phase",
	"inbox_receipt",
]
PORTABLE_GOLDEN = "test_TelegramForcePortable"
PORTABLE_LIVE = "TelegramForcePortable"
PORTABLE_REAL = "real_TelegramForcePortable"
PORTABLE_MARKER = "testing"
OVERLAY_PATHS_FILE = "test-overlay.paths"
OVERLAY_PATCH_FILE = "test-overlay.patch"
OVERLAY_SUBMODULES_FILE = "test-overlay-submodules.json"
OVERLAY_SUBMODULES_DIR = "test-overlay-submodules"
TEST_LOG_FILE = "test_log.txt"
TEST_COMPLETE_MARKER = "TEST_COMPLETE"
STALE_CRASH_DIR = "stale-crash"
BUILD_LOCK_PROCESS_NAMES = {
	"cl.exe",
	"cmake.exe",
	"cvtres.exe",
	"link.exe",
	"moc.exe",
	"mspdbsrv.exe",
	"msbuild.exe",
	"ninja.exe",
	"rc.exe",
	"rcc.exe",
	"uic.exe",
}


class WorkspaceError(RuntimeError):
	pass


def run_git(path, *args, check=True):
	result = subprocess.run(
		["git", "-C", str(path), *args],
		stdout=subprocess.PIPE,
		stderr=subprocess.PIPE,
		text=True,
	)
	if check and result.returncode:
		raise WorkspaceError(result.stderr.strip() or result.stdout.strip())
	return result


def git_root(path):
	result = run_git(path, "rev-parse", "--show-toplevel")
	return Path(result.stdout.strip()).resolve()


def absolute_git_dir(path):
	result = run_git(path, "rev-parse", "--git-common-dir")
	value = Path(result.stdout.strip())
	if not value.is_absolute():
		value = Path(path) / value
	return value.resolve()


def source_root(value):
	start = Path(value).expanduser().resolve() if value else Path.cwd()
	root = git_root(start)
	if not (root / "Telegram" / "build").is_dir():
		raise WorkspaceError(f"Not a Telegram Desktop checkout: {root}")
	return root


def read_machine_tag(root):
	path = root / "Telegram" / "build" / "ai-machine-tag"
	if not path.is_file():
		raise WorkspaceError(
			f"Missing {path}. Create it with a stable lowercase machine tag."
		)
	value = path.read_text(encoding="utf-8").strip()
	if not TAG_PATTERN.fullmatch(value):
		raise WorkspaceError(
			f"Invalid machine tag {value!r}; use lowercase letters, digits, and hyphens."
		)
	return value


def repository_config(args, create=False):
	root = source_root(args.source_root)
	machine = read_machine_tag(root)
	checkout = root.name
	if not TAG_PATTERN.fullmatch(checkout):
		raise WorkspaceError(
			f"Invalid checkout folder {checkout!r}; use lowercase letters, digits, and hyphens."
		)

	main_value = args.ai_main or os.environ.get("AI_TDESKTOP_ROOT")
	main = (
		Path(main_value).expanduser().resolve()
		if main_value
		else (root.parent / "ai-tdesktop").resolve()
	)
	worktrees_value = args.worktrees_root or os.environ.get(
		"AI_TDESKTOP_WORKTREES_ROOT"
	)
	worktrees = (
		Path(worktrees_value).expanduser().resolve()
		if worktrees_value
		else (root.parent / "ai-tdesktop-worktrees").resolve()
	)

	if not (main / ".git").exists():
		raise WorkspaceError(f"Missing ai-tdesktop repository: {main}")
	if git_root(main) != main:
		raise WorkspaceError(f"ai-tdesktop is not a worktree root: {main}")

	main_branch = run_git(main, "branch", "--show-current").stdout.strip()
	if main_branch != "master":
		raise WorkspaceError(
			f"The main ai-tdesktop worktree must be on master, not {main_branch!r}."
		)

	tag = f"{machine}-{checkout}"
	if create:
		inbox = main / "inbox"
		(inbox / "backup").mkdir(parents=True, exist_ok=True)
		(inbox / "inbox.md").touch(exist_ok=True)
		worktrees.mkdir(parents=True, exist_ok=True)
	return {
		"source_root": str(root),
		"machine_tag": machine,
		"checkout_folder": checkout,
		"checkout_tag": tag,
		"ai_main": str(main),
		"worktrees_root": str(worktrees),
		"inbox": str(main / "inbox"),
	}


def linked_worktree(config, path, branch, create, label):
	main = Path(config["ai_main"])
	if create and not path.exists():
		branch_exists = run_git(
			main,
			"show-ref",
			"--verify",
			"--quiet",
			f"refs/heads/{branch}",
			check=False,
		).returncode == 0
		arguments = ["worktree", "add"]
		if not branch_exists:
			arguments.extend(["-b", branch])
		arguments.extend([str(path), branch if branch_exists else "master"])
		run_git(main, *arguments)
	if not path.is_dir():
		raise WorkspaceError(f"Missing {label}: {path}")
	if absolute_git_dir(main) != absolute_git_dir(path):
		raise WorkspaceError(
			f"The {label} does not belong to {main}: {path}"
		)
	actual_branch = run_git(path, "branch", "--show-current").stdout.strip()
	if actual_branch != branch:
		raise WorkspaceError(
			f"Expected {path} on {branch}, found {actual_branch!r}."
		)


def worktree_config(args, create=False):
	config = repository_config(args, create=create)
	tag = config["checkout_tag"]
	branch = f"slot/{tag}"
	slot = Path(config["worktrees_root"]) / tag
	linked_worktree(config, slot, branch, create, "checkout AI worktree")
	return {
		**config,
		"slot_worktree": str(slot),
		"slot_branch": branch,
	}


def inbox_worktree_config(args, create=False):
	config = repository_config(args, create=create)
	tag = config["checkout_tag"]
	branch = f"inbox/{tag}"
	worktree = Path(config["worktrees_root"]) / f"{tag}-inbox"
	linked_worktree(config, worktree, branch, create, "inbox AI worktree")
	return {
		**config,
		"inbox_worktree": str(worktree),
		"inbox_branch": branch,
	}


def ensure_clean(path, label):
	status = run_git(path, "status", "--porcelain", "--untracked-files=all").stdout
	if status.strip():
		raise WorkspaceError(f"{label} is not clean:\n{status.rstrip()}")


def sync_local_slot(config):
	sync_branch_worktree(
		config,
		"slot_worktree",
		"slot_branch",
		"ai-tdesktop slot",
	)


def sync_inbox_worktree(config):
	sync_branch_worktree(
		config,
		"inbox_worktree",
		"inbox_branch",
		"ai-tdesktop inbox worktree",
	)


def sync_branch_worktree(config, worktree_key, branch_key, label):
	main = Path(config["ai_main"])
	worktree = Path(config[worktree_key])
	branch = config[branch_key]
	ensure_clean(main, "ai-tdesktop master")
	ensure_clean(worktree, label)
	counts = run_git(
		worktree,
		"rev-list",
		"--left-right",
		"--count",
		f"master...{branch}",
	).stdout.strip().split()
	master_only, worktree_only = (int(value) for value in counts)
	if worktree_only:
		raise WorkspaceError(
			f"{branch} has {worktree_only} unpublished commit(s)."
		)
	if master_only:
		run_git(worktree, "merge", "--ff-only", "master")


def has_origin(path):
	return run_git(path, "remote", "get-url", "origin", check=False).returncode == 0


def origin_master_exists(path):
	return run_git(
		path,
		"show-ref",
		"--verify",
		"--quiet",
		"refs/remotes/origin/master",
		check=False,
	).returncode == 0


def retryable_push_failure(message):
	lowered = message.lower()
	return "non-fast-forward" in lowered or "fetch first" in lowered


def sync_canonical(config):
	main = Path(config["ai_main"])
	ensure_clean(main, "ai-tdesktop master")
	ensure_clean(Path(config["slot_worktree"]), "ai-tdesktop slot")
	if has_origin(main):
		run_git(main, "fetch", "origin")
		if not origin_master_exists(main):
			raise WorkspaceError("origin/master does not exist")
		run_git(main, "merge", "--ff-only", "origin/master")
	sync_local_slot(config)


def state_paths(root):
	tasks = root / "tasks"
	if not tasks.is_dir():
		return []
	return sorted(tasks.glob("*/*/*/*/state.yaml"))


def task_id_for_state(root, path):
	relative = path.relative_to(root)
	parts = relative.parts
	if len(parts) != 6 or parts[0] != "tasks" or parts[-1] != "state.yaml":
		raise WorkspaceError(f"Invalid task state path: {path}")
	value = "/".join(parts[1:5])
	if not TASK_ID_PATTERN.fullmatch(value):
		raise WorkspaceError(f"Invalid task identifier: {value}")
	return value


def state_path(root, task_id):
	if not TASK_ID_PATTERN.fullmatch(task_id):
		raise WorkspaceError(f"Invalid task identifier: {task_id!r}")
	path = root / "tasks" / task_id / "state.yaml"
	if not path.is_file():
		raise WorkspaceError(f"Task does not exist: {task_id}")
	return path


def parse_scalar(value):
	value = value.strip()
	if value == "null":
		return None
	if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
		return value[1:-1]
	return value


def parse_dependencies(value):
	value = value.strip()
	if value == "[]":
		return []
	if not value.startswith("[") or not value.endswith("]"):
		raise WorkspaceError(f"depends_on must be an inline YAML list, found {value!r}")
	return [
		str(parse_scalar(part))
		for part in value[1:-1].split(",")
		if part.strip()
	]


def load_state(root, path):
	values = {}
	for line in path.read_text(encoding="utf-8-sig").splitlines():
		if not line or line[0].isspace() or ":" not in line:
			continue
		key, value = line.split(":", 1)
		values[key] = value.strip()
	for required in ("status", "created", "project", "depends_on", "claimed_by"):
		if required not in values:
			raise WorkspaceError(f"Missing {required} in {path}")
	status = str(parse_scalar(values["status"]))
	if status not in VALID_STATUSES:
		raise WorkspaceError(f"Invalid status {status!r} in {path}")
	kind = parse_scalar(values.get("type", DEFAULT_TASK_TYPE))
	kind = DEFAULT_TASK_TYPE if kind is None else str(kind)
	if kind not in VALID_TASK_TYPES:
		raise WorkspaceError(f"Invalid task type {kind!r} in {path}")
	project = parse_scalar(values["project"])
	if project is not None and not TAG_PATTERN.fullmatch(str(project)):
		raise WorkspaceError(f"Invalid project slug {project!r} in {path}")
	order = parse_scalar(values.get("claim_order", "null"))
	if order is not None:
		try:
			order = int(order)
		except ValueError as error:
			raise WorkspaceError(f"Invalid claim_order in {path}: {order!r}") from error
	task_id = task_id_for_state(root, path)
	task_file = path.with_name("task.md")
	title = task_id.rsplit("/", 1)[-1]
	if task_file.is_file():
		for line in task_file.read_text(encoding="utf-8-sig").splitlines():
			if line.startswith("# "):
				title = line[2:].strip()
				break
	return {
		"id": task_id,
		"title": title,
		"status": status,
		"type": kind,
		"created": str(parse_scalar(values["created"])),
		"project": project,
		"depends_on": parse_dependencies(values["depends_on"]),
		"claimed_by": parse_scalar(values["claimed_by"]),
		"claimed_at": parse_scalar(values.get("claimed_at", "null")),
		"claim_order": order,
		"lease_until": parse_scalar(values.get("lease_until", "null")),
		"phase": parse_scalar(values.get("phase", "null")),
		"state_path": str(path),
	}


def load_states(root):
	return {
		state["id"]: state
		for state in (load_state(root, path) for path in state_paths(root))
	}


def format_scalar(value):
	if value is None:
		return "null"
	return str(value)


def update_state(path, changes):
	data = path.read_bytes()
	if data.startswith(b"\xef\xbb\xbf"):
		raise WorkspaceError(f"Refusing to preserve a UTF-8 BOM in {path}")
	newline = "\r\n" if b"\r\n" in data else "\n"
	lines = data.decode("utf-8").splitlines()
	positions = {}
	for index, line in enumerate(lines):
		if line and not line[0].isspace() and ":" in line:
			positions[line.split(":", 1)[0]] = index
	for key, value in changes.items():
		line = f"{key}: {format_scalar(value)}"
		if key in positions:
			lines[positions[key]] = line
			continue
		if key not in STATE_FIELD_ORDER:
			raise WorkspaceError(f"Unknown task state field: {key}")
		order = STATE_FIELD_ORDER.index(key)
		insert_at = len(lines)
		for later in STATE_FIELD_ORDER[order + 1:]:
			if later in positions:
				insert_at = positions[later]
				break
		lines.insert(insert_at, line)
		positions = {
			name: index + (1 if index >= insert_at else 0)
			for name, index in positions.items()
		}
		positions[key] = insert_at
	path.write_bytes((newline.join(lines) + newline).encode("utf-8"))


def task_ready(task, states):
	return all(
		dependency in states and states[dependency]["status"] == "approved"
		for dependency in task["depends_on"]
	)


def queue_sort_key(task):
	return (
		task["claimed_at"] or "9999",
		task["claim_order"] if task["claim_order"] is not None else 1_000_000,
		task["created"],
		task["id"],
	)


def changed_paths(path):
	result = set()
	for arguments in (
		("diff", "--name-only"),
		("diff", "--cached", "--name-only"),
		("ls-files", "--others", "--exclude-standard"),
	):
		for value in run_git(path, *arguments).stdout.splitlines():
			if value:
				result.add(value)
	return sorted(result)


def unpublished_counts(config):
	values = run_git(
		config["slot_worktree"],
		"rev-list",
		"--left-right",
		"--count",
		f"master...{config['slot_branch']}",
	).stdout.strip().split()
	return {"master_only": int(values[0]), "slot_only": int(values[1])}


def task_relative_dir(task_id):
	return f"tasks/{task_id}"


def source_task_ref(task_id, name):
	if not TASK_ID_PATTERN.fullmatch(task_id):
		raise WorkspaceError(f"Invalid task identifier: {task_id!r}")
	return f"refs/ai-tasks/{task_id}/{name}"


def resolved_ref(path, value):
	result = run_git(
		path,
		"rev-parse",
		"--verify",
		f"{value}^{{commit}}",
		check=False,
	)
	return result.stdout.strip() if not result.returncode else None


def ensure_changes_scoped(slot, task_ids, extra_paths=()):
	allowed = tuple(task_relative_dir(task_id) + "/" for task_id in task_ids)
	unexpected = [
		path for path in changed_paths(slot)
		if not path.startswith(allowed) and path not in extra_paths
	]
	if unexpected:
		raise WorkspaceError(
			"The AI slot has changes outside the active task: "
			+ ", ".join(unexpected)
		)


def update_main_from_origin(config):
	main = Path(config["ai_main"])
	ensure_clean(main, "ai-tdesktop master")
	if has_origin(main):
		run_git(main, "fetch", "origin")
		if not origin_master_exists(main):
			raise WorkspaceError("origin/master does not exist")
		run_git(main, "merge", "--ff-only", "origin/master")


def sync_inbox_canonical(config):
	update_main_from_origin(config)
	sync_inbox_worktree(config)


def publish_worktree(config, worktree_key, branch_key, label):
	main = Path(config["ai_main"])
	worktree = Path(config[worktree_key])
	branch = config[branch_key]
	ensure_clean(main, "ai-tdesktop master")
	ensure_clean(worktree, label)
	while True:
		update_main_from_origin(config)
		rebase = run_git(worktree, "rebase", "master", check=False)
		if rebase.returncode:
			run_git(worktree, "rebase", "--abort", check=False)
			raise WorkspaceError(
				"AI state conflicts with newer master; the worktree commits were preserved. "
				+ (rebase.stderr.strip() or rebase.stdout.strip())
			)
<<<<<<< HEAD
=======
		ensure_new_tasks_have_projects(worktree)
		if validate is not None:
			validate(worktree)
>>>>>>> v7.4.4
		if has_origin(main):
			push = run_git(worktree, "push", "origin", "HEAD:master", check=False)
			if push.returncode:
				message = push.stderr.strip() or push.stdout.strip()
				if retryable_push_failure(message):
					continue
				raise WorkspaceError(message)
		merge = run_git(main, "merge", "--ff-only", branch, check=False)
		if not merge.returncode:
			return True
		master_advanced = run_git(
			main,
			"merge-base",
			"--is-ancestor",
			"master",
			branch,
			check=False,
		).returncode != 0
		if master_advanced:
			continue
		raise WorkspaceError(
			"Could not fast-forward local AI master: "
			+ (merge.stderr.strip() or merge.stdout.strip())
		)


def publish_slot(config):
	return publish_worktree(
		config,
		"slot_worktree",
		"slot_branch",
		"ai-tdesktop slot",
	)


def publish_inbox(config):
	return publish_worktree(
		config,
		"inbox_worktree",
		"inbox_branch",
		"ai-tdesktop inbox worktree",
	)


def commit_paths(config, paths, subject):
	slot = Path(config["slot_worktree"])
	for path in paths:
		run_git(slot, "add", "--", path)
	if run_git(slot, "diff", "--cached", "--quiet", check=False).returncode == 0:
		raise WorkspaceError("No AI task state changed")
	run_git(slot, "commit", "-m", subject)
	return publish_slot(config)


def task_summary(task, states):
	return {
		**task,
		"ready": task_ready(task, states),
	}


def normalized_task_name(value):
	return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def resolve_task(states, value):
	if TASK_ID_PATTERN.fullmatch(value):
		if value not in states:
			raise WorkspaceError(f"Task does not exist: {value}")
		return states[value]
	name = normalized_task_name(value)
	if not name:
		raise WorkspaceError("Task name is empty")
	exact = [
		task for task in states.values()
		if task["id"].rsplit("/", 1)[-1] == name
	]
	if not exact:
		exact = [
			task for task in states.values()
			if normalized_task_name(task["title"]) == name
		]
	unfinished = [
		task for task in exact
		if task["status"] in ("todo", "in-progress", "blocked")
	]
	candidates = unfinished or exact
	if not candidates:
		raise WorkspaceError(f"No task matches short name: {value!r}")
	if len(candidates) != 1:
		raise WorkspaceError(
			f"Task name {value!r} is ambiguous: "
			+ ", ".join(sorted(task["id"] for task in candidates))
		)
	return candidates[0]


def command_queue(args):
	config = worktree_config(args, create=True)
	main = Path(config["ai_main"])
	slot = Path(config["slot_worktree"])
	main_dirty = changed_paths(main)
	slot_dirty = changed_paths(slot)
	if not main_dirty and not slot_dirty:
		counts = unpublished_counts(config)
		if not counts["slot_only"]:
			sync_canonical(config)
	states = load_states(slot)
	tag = config["checkout_tag"]
	values = [task_summary(task, states) for task in states.values()]
	own_in_progress = sorted(
		(
			task for task in values
			if task["claimed_by"] == tag and task["status"] == "in-progress"
		),
		key=queue_sort_key,
	)
	own_todo = sorted(
		(
			task for task in values
			if task["claimed_by"] == tag and task["status"] == "todo"
		),
		key=queue_sort_key,
	)
	own_blocked = sorted(
		(
			task for task in values
			if task["claimed_by"] == tag and task["status"] == "blocked"
		),
		key=queue_sort_key,
	)
	unclaimed_todo = sorted(
		(
			task for task in values
			if task["claimed_by"] is None and task["status"] == "todo"
		),
		key=lambda task: (task["created"], task["id"]),
	)
	violations = []
	if len(own_in_progress) > 1:
		violations.append(f"{tag} has more than one in-progress task")
	for task in values:
		if task["status"] == "in-progress" and task["claimed_by"] is None:
			violations.append(f"{task['id']} is in-progress but unclaimed")
		if task["status"] == "blocked" and task["claimed_by"] is None:
			violations.append(f"{task['id']} is blocked but unclaimed")
	inbox_file = main / "inbox" / "inbox.md"
	result = {
		**config,
		"inbox_nonempty": (
			inbox_file.is_file()
			and bool(inbox_file.read_text(encoding="utf-8").strip())
		),
		"own_in_progress": own_in_progress,
		"own_todo": own_todo,
		"own_blocked": own_blocked,
		"unclaimed_todo": unclaimed_todo,
		"other_claimed_unfinished": sum(
			1 for task in values
			if task["claimed_by"] not in (None, tag)
			and task["status"] in ("todo", "in-progress", "blocked")
		),
		"ai_main_dirty": main_dirty,
		"slot_dirty": slot_dirty,
		"commits": unpublished_counts(config),
		"violations": violations,
	}
	print(json.dumps(result, indent=2, sort_keys=True))


def command_resolve(args):
	config = worktree_config(args, create=True)
	main = Path(config["ai_main"])
	slot = Path(config["slot_worktree"])
	main_dirty = changed_paths(main)
	if main_dirty:
		raise WorkspaceError(
			"ai-tdesktop master is not clean: " + ", ".join(main_dirty)
		)
	if not changed_paths(slot) and not unpublished_counts(config)["slot_only"]:
		sync_canonical(config)
	states = load_states(slot)
	task = task_summary(resolve_task(states, args.name), states)
	active = sorted(
		value["id"] for value in states.values()
		if value["claimed_by"] == config["checkout_tag"]
		and value["status"] == "in-progress"
	)
	print(json.dumps({
		**config,
		"task": task,
		"other_active_task": next(
			(value for value in active if value != task["id"]),
			None,
		),
		"slot_dirty": changed_paths(slot),
		"commits": unpublished_counts(config),
	}, indent=2, sort_keys=True))


def command_start(args):
	config = worktree_config(args, create=True)
	sync_canonical(config)
	slot = Path(config["slot_worktree"])
	states = load_states(slot)
	task = states.get(args.task)
	if task is None:
		raise WorkspaceError(f"Task does not exist: {args.task}")
	if task["status"] != "todo" or task["claimed_by"] not in (
		None,
		config["checkout_tag"],
	):
		raise WorkspaceError(
			f"Task is not available todo work for this checkout: {args.task}"
		)
	if not task_ready(task, states):
		raise WorkspaceError(f"Task has unfinished dependencies: {args.task}")
	active = [
		value["id"] for value in states.values()
		if value["claimed_by"] == config["checkout_tag"]
		and value["status"] == "in-progress"
	]
	if active:
		raise WorkspaceError("Another task is already in progress: " + ", ".join(active))
	path = state_path(slot, args.task)
	update_state(path, {
		"status": "in-progress",
		"claimed_by": config["checkout_tag"],
		"claimed_at": (
			task["claimed_at"]
			or datetime.datetime.now().astimezone().isoformat()
		),
		"claim_order": task["claim_order"] or 1,
		"lease_until": None,
		"phase": "setup",
	})
	try:
		commit = commit_paths(
			config,
			[str(path.relative_to(slot))],
			f"Start {args.task} on {config['checkout_tag']}",
		)
	except WorkspaceError as error:
		main_states = load_states(Path(config["ai_main"]))
		main_task = main_states.get(args.task)
		lost = (
			main_task is not None
			and (
				main_task["status"] != "todo"
				or main_task["claimed_by"] not in (
					None,
					config["checkout_tag"],
				)
			)
		)
		counts = unpublished_counts(config)
		if lost and counts["slot_only"] == 1:
			head = run_git(slot, "rev-parse", "HEAD").stdout.strip()
			run_git(slot, "rebase", "--onto", "master", head)
			raise WorkspaceError(
				f"Start lost to newer master for {args.task}"
			) from error
		raise
	print(json.dumps({
		"task": args.task,
		"status": "in-progress",
		"published": bool(commit),
	}, indent=2, sort_keys=True))


def command_retry(args):
	config = worktree_config(args, create=True)
	sync_canonical(config)
	slot = Path(config["slot_worktree"])
	states = load_states(slot)
	task = states.get(args.task)
	if task is None:
		raise WorkspaceError(f"Task does not exist: {args.task}")
	if (
		task["status"] != "blocked"
		or task["claimed_by"] != config["checkout_tag"]
	):
		raise WorkspaceError(
			f"Task is not blocked work owned by this checkout: {args.task}"
		)
	if not task_ready(task, states):
		raise WorkspaceError(f"Task has unfinished dependencies: {args.task}")
	active = [
		value["id"] for value in states.values()
		if value["claimed_by"] == config["checkout_tag"]
		and value["status"] == "in-progress"
	]
	if active:
		raise WorkspaceError("Another task is already in progress: " + ", ".join(active))
	path = state_path(slot, args.task)
	update_state(path, {
		"status": "in-progress",
		"phase": "resume",
		"lease_until": None,
	})
	routed = path.parent / "work" / "discovered-routed.md"
	if routed.is_file():
		routed.unlink()
	print(json.dumps({
		"task": args.task,
		"status": "in-progress",
		"local": True,
		"published": False,
	}, indent=2, sort_keys=True))


def task_action_config(args, require_status="in-progress", allow_project=False):
	config = worktree_config(args, create=True)
	slot = Path(config["slot_worktree"])
	ensure_clean(Path(config["ai_main"]), "ai-tdesktop master")
	task = load_state(slot, state_path(slot, args.task))
	extra_paths = ()
	if allow_project and task["project"] is not None:
		extra_paths = (f"projects/{task['project']}/project.md",)
	ensure_changes_scoped(slot, [args.task], extra_paths)
	if task["status"] != require_status or task["claimed_by"] != config["checkout_tag"]:
		raise WorkspaceError(
			f"Task is not {require_status} work owned by this checkout: {args.task}"
		)
	return config, slot


def command_checkpoint(args):
	_, slot = task_action_config(args)
	path = state_path(slot, args.task)
	update_state(path, {"phase": args.phase})
	print(json.dumps({
		"task": args.task,
		"phase": args.phase,
		"local": True,
		"published": False,
	}, indent=2, sort_keys=True))


def task_commit_matches(source, value, task_id):
	message = run_git(source, "show", "-s", "--format=%B", value).stdout.rstrip("\n")
	lines = message.split("\n")
	return (
		len(lines) == 3
		and bool(lines[0])
		and not lines[1]
		and lines[2] == f"Task: {task_id}"
	)


def validate_task_commit(source, value, task_id):
	if not task_commit_matches(source, value, task_id):
		raise WorkspaceError(
			"Telegram commit message must be exactly a one-line subject, a blank line, "
			f"and Task: {task_id}"
		)


def task_series_refs(source, task_id):
	commits = run_git(
		source,
		"log",
		"--first-parent",
		"--format=%H",
		"--fixed-strings",
		f"--grep=Task: {task_id}",
		"HEAD",
	).stdout.splitlines()
	for green in commits:
		if not task_commit_matches(source, green, task_id):
			continue
		current = green
		while True:
			parent = resolved_ref(source, f"{current}^")
			if parent is None:
				raise WorkspaceError(
					"A task implementation cannot start at the repository root"
				)
			if not task_commit_matches(source, parent, task_id):
				return parent, green
			current = parent
	return None


<<<<<<< HEAD
=======
def task_tip_commits(source, task_id):
	commits = [
		commit for commit in run_git(
			source,
			"log",
			"--all",
			"--format=%H",
			"--fixed-strings",
			f"--grep=Task: {task_id}",
		).stdout.splitlines()
		if task_commit_matches(source, commit, task_id)
	]
	return [
		commit for commit in commits
		if not any(
			commit != other and is_ancestor(source, commit, other)
			for other in commits
		)
	]


def task_has_retained_source_change(slot, state):
	if state["type"] == "verify":
		return False
	result_path = (
		Path(slot)
		/ task_relative_dir(state["id"])
		/ "work"
		/ "result.md"
	)
	if not result_path.is_file():
		return True
	outcomes = [
		line.split(":", 1)[1].strip()
		for line in result_path.read_text(encoding="utf-8-sig").splitlines()
		if line.startswith("Outcome:")
	]
	return outcomes != ["already-satisfied"]


def source_lineage_report(config, slot, task_id, extra_requirements=(), revision="HEAD"):
	states = load_states(slot)
	task = states.get(task_id)
	if task is None:
		raise WorkspaceError(f"Task does not exist: {task_id}")
	source = Path(config["source_root"])
	required, unfinished, missing_history, missing, witnesses, tips = (
		lineage_presence(
			source, slot, states,
			(*task["depends_on"], *extra_requirements), revision,
		)
	)
	branches = run_git(
		source,
		"for-each-ref",
		"--format=%(refname:short)",
		"refs/heads",
	).stdout.splitlines()
	projects = live_project_slugs(slot)
	current_branch_name = current_branch(source)
	switchable = task["project"] is None and current_branch_name not in projects
	compatible = [
		branch for branch in branches
		if switchable
		and branch not in projects
		and all(
			commits and any(
				is_ancestor(source, commit, branch)
				for commit in commits
			)
			for commits in tips.values()
		)
	]
	return {
		"task": task_id,
		"current_branch": current_branch_name,
		"current_satisfies": not missing,
		"unfinished_dependencies": unfinished,
		"required_source_tasks": required,
		"missing_source_tasks": missing,
		"unavailable_source_tasks": missing_history,
		"witnessed_source_tasks": witnesses,
		"compatible_local_branches": compatible,
	}


def lineage_presence(source, slot, states, dependencies, revision):
	"""Which approved source work among dependencies revision has.

	Returns the required ids, unfinished ids, ids with no commit anywhere,
	ids revision lacks, witnesses that stand in for a folded commit, and the
	tip commits of each required id.
	"""
	superseded = load_superseded(slot)
	retired = load_retired(slot)
	required = []
	unfinished = []
	for dependency in dependencies:
		if dependency in retired:
			continue
		resolved = resolve_task_id(states, superseded, dependency)["id"]
		state = states[resolved]
		if state["status"] != "approved":
			if resolved not in unfinished:
				unfinished.append(resolved)
			continue
		if not task_has_retained_source_change(slot, state) or resolved in required:
			continue
		required.append(resolved)

	tips = {}
	for dependency in required:
		commits = task_tip_commits(source, dependency)
		carried_from = states[dependency]["carried_from"]
		if not commits and carried_from is not None:
			commits = task_tip_commits(source, carried_from)
		tips[dependency] = commits
	missing_history = [
		dependency for dependency, commits in tips.items()
		if not commits
	]
	missing = [
		dependency for dependency, commits in tips.items()
		if not commits or not any(
			is_ancestor(source, commit, revision) for commit in commits
		)
	]
	witnesses = {}
	for dependency in missing:
		witness = lineage_witness(source, states, dependency, revision)
		if witness is not None:
			witnesses[dependency] = witness
	missing = [dependency for dependency in missing if dependency not in witnesses]
	return required, unfinished, missing_history, missing, witnesses, tips


def lineage_witness(source, states, dependency, revision="HEAD"):
	# A history rewrite can fold a task's commit into later work: an approved
	# task built on it whose own commit is on HEAD shows its change is there.
	for state in states.values():
		if (
			state["status"] == "approved"
			and dependency in state["depends_on"]
			and any(
				is_ancestor(source, commit, revision)
				for commit in task_tip_commits(source, state["id"])
			)
		):
			return state["id"]
	return None


>>>>>>> v7.4.4
def is_ancestor(source, older, newer="HEAD"):
	return not run_git(
		source,
		"merge-base",
		"--is-ancestor",
		older,
		newer,
		check=False,
	).returncode


def task_type(slot, task_id):
	return load_state(slot, state_path(slot, task_id))["type"]


def validate_source_state(config, task_id, required, kind=DEFAULT_TASK_TYPE):
	source = Path(config["source_root"])
	base = resolved_ref(source, source_task_ref(task_id, "base"))
	green = resolved_ref(source, source_task_ref(task_id, "green"))
	run = resolved_ref(source, source_task_ref(task_id, "run"))
	head = resolved_ref(source, "HEAD")
	if base is None:
		raise WorkspaceError("The local task baseline ref is missing")
	if run is None or head != run:
		raise WorkspaceError("Telegram HEAD no longer matches the task run ref")
	if kind == "verify":
		if green is not None:
			raise WorkspaceError(
				"A verification task must not retain a Telegram implementation commit"
			)
		if head != base:
			raise WorkspaceError(
				"A verification task must leave Telegram at its local baseline"
			)
		return
	if green is None:
		if required:
			raise WorkspaceError("An approved task must retain a Telegram implementation commit")
		if head != base:
			raise WorkspaceError(
				"A blocked task without an implementation must be restored to its local baseline"
			)
		return
	if not is_ancestor(source, green, head):
		raise WorkspaceError(
			"The retained task implementation is not in Telegram HEAD history"
		)
	validate_task_commit(source, green, task_id)


def ensure_no_persisted_commit_hashes(root):
	text_suffixes = {".json", ".log", ".md", ".txt", ".yaml", ".yml"}
	paths = [root] if root.is_file() else sorted(root.rglob("*"))
	for path in paths:
		if not path.is_file() or path.suffix.lower() not in text_suffixes:
			continue
		try:
			text = path.read_text(encoding="utf-8-sig")
		except UnicodeDecodeError:
			continue
		if any(field in text for field in LEGACY_COMMIT_FIELDS):
			raise WorkspaceError(f"Legacy commit-hash field found in {path}")
		if COMMIT_HASH_PATTERN.search(text):
			raise WorkspaceError(f"Persisted commit hash found in {path}")


def delete_source_refs(config, task_id, retain_implementation=False):
	source = Path(config["source_root"])
	names = ("run",) if retain_implementation else ("run", "green", "base")
	for name in names:
		value = source_task_ref(task_id, name)
		if resolved_ref(source, value) is not None:
			run_git(source, "update-ref", "-d", value)


def command_source_begin(args):
	config, _ = task_action_config(args)
	source = Path(config["source_root"])
	base = source_task_ref(args.task, "base")
	green = source_task_ref(args.task, "green")
	run = source_task_ref(args.task, "run")
	base_value = resolved_ref(source, base)
	green_value = resolved_ref(source, green)
	head = resolved_ref(source, "HEAD")
	retained = (
		base_value is not None
		and green_value is not None
		and is_ancestor(source, base_value, green_value)
		and is_ancestor(source, green_value, head)
		and task_commit_matches(source, green_value, args.task)
	)
	if retained or (base_value is not None and green_value is None and head == base_value):
		state = "resumed"
	else:
		series = task_series_refs(source, args.task)
		if series is not None:
			series_base, series_green = series
			run_git(source, "update-ref", base, series_base)
			run_git(source, "update-ref", green, series_green)
			state = "reconciled" if base_value is not None else "recovered"
		else:
			ensure_clean(source, "Telegram source checkout")
			run_git(source, "update-ref", base, "HEAD")
			if green_value is not None:
				run_git(source, "update-ref", "-d", green)
			state = "reconciled" if base_value is not None else "initialized"
	run_git(source, "update-ref", run, "HEAD")
	print(json.dumps({
		"task": args.task,
		"source_state": state,
		"has_retained_implementation": (
			resolved_ref(source, green) is not None
		),
	}, indent=2, sort_keys=True))


def mark_source_green(config, task_id):
	source = Path(config["source_root"])
	ensure_clean(source, "Telegram source checkout")
	base = source_task_ref(task_id, "base")
	if resolved_ref(source, base) is None:
		raise WorkspaceError("The local task baseline ref is missing")
	if run_git(source, "merge-base", "--is-ancestor", base, "HEAD", check=False).returncode:
		raise WorkspaceError("The retained implementation does not descend from the task baseline")
	validate_task_commit(source, "HEAD", task_id)
	run_git(source, "update-ref", source_task_ref(task_id, "green"), "HEAD")
	run_git(source, "update-ref", source_task_ref(task_id, "run"), "HEAD")


def command_source_mark_green(args):
	config, _ = task_action_config(args)
	mark_source_green(config, args.task)
	print(json.dumps({
		"task": args.task,
		"source_state": "retained",
	}, indent=2, sort_keys=True))


<<<<<<< HEAD
=======
# Every project is performed on a branch named by its slug: in tdesktop on
# the private remote, and under the same name on the origin of every
# submodule the project changes. Every new task belongs to a project, and
# project-switch moves a checkout between project branches. Legacy tasks
# without a project start only from branches that are not a live project's.

def current_branch(source):
	if source is None:
		return None
	result = run_git(source, "branch", "--show-current", check=False)
	return (not result.returncode and result.stdout.strip()) or None


def live_project_slugs(root):
	projects = Path(root) / "projects"
	if not projects.is_dir():
		return set()
	return {
		path.name for path in projects.iterdir()
		if path.name != PROJECT_ARCHIVE_DIR and (path / "project.md").is_file()
	}


def branch_project(config, slot):
	branch = current_branch(config.get("source_root"))
	return branch if branch in live_project_slugs(slot) else None


def ensure_task_branch(config, slot, task):
	branch = current_branch(config.get("source_root"))
	project = task["project"]
	if project is not None:
		if branch != project:
			raise WorkspaceError(
				f"{task['id']} belongs to project {project}: run project-switch "
				f"--project {project} first (current branch: "
				f"{branch or 'detached HEAD'})"
			)
	elif branch in live_project_slugs(slot):
		raise WorkspaceError(
			f"{task['id']} is legacy work without a project, but branch {branch} "
			f"belongs to project {branch}; start it from a non-project branch"
		)


def fetch_remote(repo, remote):
	run_git(repo, "fetch", "--quiet", remote)


def tree_gitlinks(repo, revision):
	result = {}
	for entry in run_git(repo, "ls-tree", "-r", "-z", revision).stdout.split("\0"):
		if not entry:
			continue
		metadata, path = entry.split("\t", 1)
		mode, _, value = metadata.split()
		if mode == "160000":
			result[path] = value
	return result


def linear_commits(repo, older, newer):
	if run_git(repo, "rev-list", "--merges", f"{older}..{newer}").stdout.strip():
		raise WorkspaceError(
			f"Project history in {repo} contains a merge; it must stay linear"
		)
	return run_git(
		repo, "rev-list", "--reverse", f"{older}..{newer}",
	).stdout.split()


def changed_files(repo, commit):
	return set(literal_paths(
		repo, "diff-tree", "--no-commit-id", "--name-only", "-r",
		"--no-renames", f"{commit}^", commit,
	))


def changed_gitlinks(source, older, newer):
	before = tree_gitlinks(source, older)
	after = tree_gitlinks(source, newer)
	return {
		path: (before.get(path), pin)
		for path, pin in after.items()
		if before.get(path) not in (None, pin)
	}


def gitlink_changes(repo, commit):
	"""The pins commit sets, by submodule path."""
	tokens = run_git(
		repo, "diff-tree", "-r", "-z", "--no-commit-id", "--raw", "--no-abbrev",
		"--no-renames", f"{commit}^", commit,
	).stdout.split("\0")
	result = {}
	for metadata, path in zip(tokens[0::2], tokens[1::2]):
		fields = metadata.lstrip(":").split()
		if len(fields) >= 4 and fields[1] == "160000":
			result[path] = fields[3]
	return result


def submodule_ranges(source, older, commits):
	"""Submodules whose pin any of commits sets, as (pin at older, newest pin).

	The pins the commits set along one submodule must stay on one line of
	history, which may move back and forth; the newest pin is the one every
	other pin is an ancestor of.
	"""
	base = tree_gitlinks(source, older)
	pins = {}
	for commit in commits:
		for path, pin in gitlink_changes(source, commit).items():
			if path in base:
				pins.setdefault(path, []).append(pin)
	ranges = {}
	for path, values in pins.items():
		module = source / path
		if not (module / ".git").exists():
			raise WorkspaceError(f"Project commits change {path}, which is not initialized")
		newest = base[path]
		for pin in values:
			if is_ancestor(module, newest, pin):
				newest = pin
			elif not is_ancestor(module, pin, newest):
				raise WorkspaceError(
					f"Project commits pin {path} on diverging histories; keep its "
					"pins on one line of history"
				)
		if newest != base[path]:
			ranges[path] = (base[path], newest)
	return ranges


def set_index_gitlink(repo, path, revision):
	run_git(repo, "update-index", "--force-remove", "--", path)
	run_git(repo, "update-index", "--add", "--cacheinfo", f"160000,{revision},{path}")


def replay_commits(repo, commits, onto, adjust=None):
	"""Cherry-pick commits onto a detached onto, keeping authors and messages.

	A commit that becomes empty is dropped and maps to its predecessor.
	Returns the new tip and a map from each replayed commit to its copy.
	"""
	run_git(repo, "checkout", "--quiet", "--detach", onto)
	mapping = {}
	tip = onto
	for commit in commits:
		pick = run_git(repo, "cherry-pick", "--no-commit", commit, check=False)
		if pick.returncode and not literal_paths(
			repo, "diff", "--name-only", "--diff-filter=U",
		):
			raise WorkspaceError(
				f"Could not replay {commit} in {repo}: "
				+ (pick.stderr.strip() or pick.stdout.strip())
			)
		if adjust is not None:
			adjust(commit)
		unmerged = literal_paths(repo, "diff", "--name-only", "--diff-filter=U")
		if unmerged:
			raise WorkspaceError(
				f"Replaying {commit} in {repo} conflicts in: " + ", ".join(unmerged)
			)
		if run_git(repo, "diff", "--cached", "--quiet", check=False).returncode:
			run_git(repo, "commit", "--quiet", "--no-edit", "-C", commit)
			tip = resolved_ref(repo, "HEAD")
		run_git(repo, "cherry-pick", "--quit", check=False)
		mapping[commit] = tip
	return tip, mapping


def restore_repository(repo, branch, head):
	run_git(repo, "cherry-pick", "--abort", check=False)
	run_git(repo, "reset", "--quiet", "--hard")
	if branch is not None:
		run_git(repo, "checkout", "--quiet", "--force", branch)
	else:
		run_git(repo, "checkout", "--quiet", "--force", "--detach", head)


def rewrite_project_history(source, commits, onto, ranges, targets):
	"""Replay tdesktop commits onto onto, rebasing their submodule commits.

	ranges maps a submodule path to the (older, newer) pins its project
	commits span; targets maps it to the commit those are rebased onto. Every
	pin a replayed commit sets is rewritten to its rebased copy. On failure
	every touched repository is restored to where it was.
	"""
	branch = current_branch(source)
	original = resolved_ref(source, "HEAD")
	protect_worktrees_from_submodule_target(source, onto)
	for path, (older, _) in ranges.items():
		target = targets.get(path)
		if target is not None and target != older:
			protect_worktrees_from_submodule_target(source / path, target)
	with git_safe_termination():
		return replay_project_history(
			source, commits, onto, ranges, targets, branch, original,
		)


def replay_project_history(source, commits, onto, ranges, targets, branch, original):
	touched = []
	pins = {}
	try:
		for path, (older, newer) in ranges.items():
			target = targets.get(path)
			if target is None or target == older:
				continue
			module = source / path
			touched.append(
				(module, current_branch(module), resolved_ref(module, "HEAD"))
			)
			_, mapping = replay_commits(
				module, linear_commits(module, older, newer), target,
			)
			mapping[older] = target
			pins[path] = mapping

		def adjust(commit):
			for path, mapping in pins.items():
				pin = gitlink_at(source, commit, path)
				if pin == gitlink_at(source, f"{commit}^", path):
					continue
				if pin not in mapping:
					raise WorkspaceError(
						f"{commit} pins {path} outside the project's submodule commits"
					)
				set_index_gitlink(source, path, mapping[pin])

		tip, mapping = replay_commits(source, commits, onto, adjust)
		run_git(source, "update-ref", f"refs/heads/{branch}", tip, original)
		run_git(source, "checkout", "--quiet", branch)
		update_source_submodules(source)
	except BaseException:
		for module, module_branch, head in reversed(touched):
			restore_repository(module, module_branch, head)
		if branch is not None:
			run_git(source, "update-ref", f"refs/heads/{branch}", original)
		restore_repository(source, branch, original)
		update_source_submodules(source)
		raise
	return tip, mapping


def gitlink_at(repo, revision, path):
	entry = run_git(
		repo, "--literal-pathspecs", "ls-tree", "-z", revision, "--", path,
	).stdout.split("\0")[0]
	if not entry:
		return None
	metadata = entry.split("\t", 1)[0].split()
	return metadata[2] if metadata[0] == "160000" else None


def task_ref(repo, task_id, name):
	return resolved_ref(repo, source_task_ref(task_id, name))


def set_task_ref(repo, task_id, name, value):
	ref = source_task_ref(task_id, name)
	if value is not None:
		run_git(repo, "update-ref", ref, value)
	elif resolved_ref(repo, ref) is not None:
		run_git(repo, "update-ref", "-d", ref)


def reconcile_task_refs(source, task_id, carried_from=None):
	series = task_series_refs(source, task_id)
	carried = (
		task_series_refs(source, carried_from) if carried_from is not None else None
	)
	if series is None:
		series = carried
	elif carried is not None and carried[1] == series[0]:
		series = (carried[0], series[1])
	if series is None:
		set_task_ref(source, task_id, "base", resolved_ref(source, "HEAD"))
		set_task_ref(source, task_id, "green", None)
	else:
		set_task_ref(source, task_id, "base", series[0])
		set_task_ref(source, task_id, "green", series[1])
	set_task_ref(source, task_id, "run", resolved_ref(source, "HEAD"))


def project_task_config(args):
	config, slot = task_action_config(args)
	source = Path(config["source_root"])
	task = load_state(slot, state_path(slot, args.task))
	if task["project"] is None:
		raise WorkspaceError(f"{args.task} is standalone; it has no project branch")
	ensure_task_branch(config, slot, task)
	ensure_source_clean(source)
	head = resolved_ref(source, "HEAD")
	if task_ref(source, args.task, "run") != head:
		raise WorkspaceError("Telegram HEAD no longer matches the task run ref")
	if task_ref(source, args.task, "base") is None:
		raise WorkspaceError("The local task baseline ref is missing")
	if MIRROR_REMOTE not in run_git(source, "remote").stdout.split():
		raise WorkspaceError(f"Project branches need a {MIRROR_REMOTE} remote")
	return config, source, task


def remote_branch_tip(repo, remote, branch):
	return resolved_ref(repo, f"refs/remotes/{remote}/{branch}")


def module_branch_tips(source, paths, project, tips):
	for path in paths:
		if path not in tips:
			module = source / path
			fetch_remote(module, "origin")
			tips[path] = remote_branch_tip(module, "origin", project)
	return tips


def recorded_modules(source, task_id):
	return [
		source / path for path in tree_gitlinks(source, "HEAD")
		if (source / path / ".git").exists()
		and task_ref(source / path, task_id, "shared") is not None
	]


def recorded_module_tips_moved(source, task_id, project, rewrite_from):
	modules = set(recorded_modules(source, task_id))
	modules.update(
		source / path for path in changed_gitlinks(source, rewrite_from, "HEAD")
		if (source / path / ".git").exists()
	)
	for module in sorted(modules):
		fetch_remote(module, "origin")
		if remote_branch_tip(module, "origin", project) != task_ref(
			module, task_id, "shared",
		):
			return True
	return False


def clear_module_shared_refs(source, task_id):
	for module in recorded_modules(source, task_id):
		set_task_ref(module, task_id, "shared", None)


def command_project_sync(args):
	"""Bring this task's work onto the shared project branch.

	The shared tips a sync incorporates are recorded as the task's `shared`
	refs (in tdesktop and in each project submodule), so that a push after a
	base rebase leases against exactly what was incorporated. A base rebase
	keeps the pre-rebase tip as `rewrite-from` until it is published; if the
	shared branch moves first, that unpublished rewrite is discarded and
	redone on the new history instead of overwriting it.
	"""
	config, source, task = project_task_config(args)
	project = task["project"]
	fetch_remote(source, MIRROR_REMOTE)
	remote_tip = remote_branch_tip(source, MIRROR_REMOTE, project)
	shared = task_ref(source, args.task, "shared")
	fix = None
	if args.base_fix:
		fix = resolved_ref(source, args.base_fix)
		if fix is None:
			raise WorkspaceError(f"Unknown base fix commit: {args.base_fix}")
	discarded = False
	head = resolved_ref(source, "HEAD")
	rewrite_from = task_ref(source, args.task, "rewrite-from")
	if rewrite_from is not None and remote_tip == head:
		set_task_ref(source, args.task, "rewrite-from", None)
	elif rewrite_from is not None and (
		remote_tip != shared
		or recorded_module_tips_moved(source, args.task, project, rewrite_from)
	):
		protect_worktrees_from_submodule_target(source, rewrite_from)
		run_git(source, "reset", "--quiet", "--hard", rewrite_from)
		update_source_submodules(source)
		set_task_ref(source, args.task, "rewrite-from", None)
		clear_module_shared_refs(source, args.task)
		reconcile_task_refs(source, args.task, task["carried_from"])
		discarded = True
		fix = None
	head = resolved_ref(source, "HEAD")
	base = task_ref(source, args.task, "base")
	own = linear_commits(source, base, head)
	ranges = submodule_ranges(source, base, own)
	tips = module_branch_tips(source, ranges, project, {})
	targets = {}
	diverged = False
	for path, (_, pin) in ranges.items():
		module = source / path
		pinned = gitlink_at(source, remote_tip, path) if remote_tip else None
		tip = tips[path]
		targets[path] = (
			tip
			if tip is not None and (pinned is None or is_ancestor(module, pinned, tip))
			else pinned
		)
		if (
			remote_tip != head
			and tip is not None
			and tip != task_ref(module, args.task, "shared")
			and not is_ancestor(module, tip, pin)
		):
			diverged = True
	moved = (
		remote_tip is not None
		and remote_tip != shared
		and not is_ancestor(source, remote_tip, head)
	)
	synced = False
	if moved or diverged:
		head, mapping = rewrite_project_history(
			source,
			own,
			remote_tip if moved else base,
			ranges,
			targets,
		)
		reconcile_task_refs(source, args.task, task["carried_from"])
		if fix is not None:
			fix = mapping.get(fix, fix)
		synced = True
	set_task_ref(source, args.task, "shared", remote_tip)
	rebased = False
	base_fix = None
	if args.onto:
		remote, _, name = args.onto.partition("/")
		if not name:
			raise WorkspaceError(f"--onto needs a remote branch, found {args.onto!r}")
		fetch_remote(source, remote)
		onto = remote_branch_tip(source, remote, name)
		if onto is None:
			raise WorkspaceError(f"Remote branch does not exist: {args.onto}")
		fork = run_git(source, "merge-base", onto, head).stdout.strip()
		commits = linear_commits(source, fork, head)
		reorder = False
		if fix is not None:
			if fix not in commits:
				raise WorkspaceError("The base fix is not a commit of this project branch")
			others = [commit for commit in commits if commit != fix]
			fixed = changed_files(source, fix)
			if gitlink_changes(source, fix) or fixed & set().union(
				*(changed_files(source, commit) for commit in others)
			):
				raise WorkspaceError(
					"A base fix may touch only files no project commit changes"
				)
			reorder = commits[0] != fix
			commits = [fix] + others
		if fork != onto or reorder:
			ranges = submodule_ranges(source, fork, commits)
			module_branch_tips(source, ranges, project, tips)
			if task_ref(source, args.task, "rewrite-from") is None:
				set_task_ref(source, args.task, "rewrite-from", head)
			head, mapping = rewrite_project_history(
				source,
				commits,
				onto,
				ranges,
				{path: gitlink_at(source, onto, path) for path in ranges},
			)
			reconcile_task_refs(source, args.task, task["carried_from"])
			rebased = True
			if fix is not None and mapping[fix] != onto:
				base_fix = mapping[fix]
		elif fix is not None:
			base_fix = fix
	for path, tip in tips.items():
		set_task_ref(source / path, args.task, "shared", tip)
	print(json.dumps({
		"task": args.task,
		"branch": project,
		"synced": synced,
		"rebased_onto_base": rebased,
		"rewrite_discarded": discarded,
		"build_required": synced or rebased or discarded,
		"base_fix": base_fix,
	}, indent=2, sort_keys=True))


def push_branch(
		repo, remote, revision, branch, lease=None, force=False,
		retry="project-sync"):
	arguments = ["push", "--quiet", remote]
	if force:
		arguments.insert(1, "--force")
	elif lease is not None:
		arguments.insert(1, f"--force-with-lease=refs/heads/{branch}:{lease}")
	push = run_git(
		repo, *arguments, f"{revision}:refs/heads/{branch}", check=False,
	)
	if push.returncode:
		raise WorkspaceError(
			f"Pushing {branch} from {repo} was rejected; rerun {retry}: "
			+ (push.stderr.strip() or push.stdout.strip())
		)


def live_branch_tip(repo, remote, branch):
	listed = run_git(repo, "ls-remote", remote, f"refs/heads/{branch}").stdout.split()
	return listed[0] if listed else None


def module_publications(source, head, project):
	"""Top-level submodules whose pinned commit the project branch publishes.

	A pin already on some remote branch needs nothing unless the submodule
	has a project branch to keep up to date. Nested submodules are not
	covered, so a pinned nested commit must already be on a remote branch.
	"""
	unpublished = run_git(
		source, "rev-list", head, "--not",
		f"--exclude={MIRROR_REMOTE}/{MIRROR_BRANCH_PREFIX}*",
		f"--remotes={MIRROR_REMOTE}", "--remotes=origin",
	).stdout.split()
	newest = {}
	for commit in unpublished:
		for path, pin in gitlink_changes(source, commit).items():
			module = source / path
			if (module / ".git").exists() and (
				path not in newest or is_ancestor(module, newest[path], pin)
			):
				newest[path] = pin
	result = []
	for path, pin in tree_gitlinks(source, head).items():
		module = source / path
		if not (module / ".git").exists():
			continue
		published = pin
		if path in newest and is_ancestor(module, pin, newest[path]):
			published = newest[path]
		for revision in {pin, published}:
			for nested, nested_pin in tree_gitlinks(module, revision).items():
				child = module / nested
				if (child / ".git").exists() and not run_git(
					child, "branch", "--remotes", "--contains", nested_pin,
					check=False,
				).stdout.strip():
					raise WorkspaceError(
						f"{path}/{nested} is pinned at a commit on no remote branch; "
						"project branches cover only top-level submodules, so "
						"publish that nested commit first"
					)
		contained = bool(run_git(
			module, "branch", "--remotes", "--contains", published,
		).stdout.strip())
		if contained and remote_branch_tip(module, "origin", project) is None:
			continue
		result.append((path, module, published, contained))
	return result


def command_project_push(args):
	"""Publish the project branch: submodule branches, then tdesktop.

	After a base rebase the order changes so that nothing shared moves until
	tdesktop's lease holds: the new submodule commits go first to this
	checkout's own staging branch _staging/<slug>/<checkout-tag>, outside the
	project-slug grammar, which makes every pin fetchable, then
	tdesktop moves, then the submodule project branches.
	"""
	config, source, task = project_task_config(args)
	project = task["project"]
	fetch_remote(source, MIRROR_REMOTE)
	head = resolved_ref(source, "HEAD")
	remote_tip = remote_branch_tip(source, MIRROR_REMOTE, project)
	shared = task_ref(source, args.task, "shared")
	rewritten = remote_tip is not None and not is_ancestor(source, remote_tip, head)
	if rewritten and (not args.rewritten or remote_tip != shared):
		raise WorkspaceError(
			f"{MIRROR_REMOTE}/{project} has commits this branch lacks; rerun project-sync"
		)
	publications = module_publications(source, head, project)
	if rewritten:
		staging = f"_staging/{project}/{config['checkout_tag']}"
		for _, module, pin, contained in publications:
			if not contained:
				push_branch(module, "origin", pin, staging, force=True)
		push_branch(source, MIRROR_REMOTE, head, project, lease=shared)
	modules = []
	for path, module, pin, contained in publications:
		fetch_remote(module, "origin")
		tip = remote_branch_tip(module, "origin", project)
		if tip is None:
			if contained:
				continue
			push_branch(module, "origin", pin, project)
		elif tip == pin or is_ancestor(module, pin, tip):
			continue
		elif is_ancestor(module, tip, pin):
			push_branch(module, "origin", pin, project)
		elif args.rewritten and (
			tip == task_ref(module, args.task, "shared")
			# Nobody published on top of this rewrite, so a commit there that
			# the shared tdesktop branch does not pin is another checkout's
			# unpublished half, which that checkout replays at its next sync.
			or live_branch_tip(source, MIRROR_REMOTE, project) == head
		):
			push_branch(module, "origin", pin, project, lease=tip)
		else:
			raise WorkspaceError(
				f"{path} branch {project} moved since project-sync; rerun project-sync"
			)
		modules.append(path)
	if args.base_fix:
		remote, _, name = (args.base or "").partition("/")
		if not name:
			raise WorkspaceError("--base-fix needs --base naming the remote branch")
		fix = resolved_ref(source, args.base_fix)
		fetch_remote(source, remote)
		base_tip = remote_branch_tip(source, remote, name)
		if base_tip != fix:
			if resolved_ref(source, f"{fix}^") != base_tip:
				raise WorkspaceError(
					f"{args.base} moved past the base fix; rerun project-sync --onto"
				)
			push_branch(source, remote, fix, name)
	if not rewritten:
		push_branch(source, MIRROR_REMOTE, head, project)
	set_task_ref(source, args.task, "shared", None)
	set_task_ref(source, args.task, "rewrite-from", None)
	clear_module_shared_refs(source, args.task)
	print(json.dumps({
		"task": args.task,
		"branch": project,
		"pushed": True,
		"rewritten": rewritten,
		"submodules": sorted(modules),
		"base_fix_pushed": bool(args.base_fix),
	}, indent=2, sort_keys=True))


PROJECT_BASE_BRANCH = "alpha"


def checked_out_elsewhere(source, branch):
	"""Other worktrees of source's repository that have branch checked out."""
	source = Path(source).resolve()
	result = []
	worktree = None
	for line in run_git(source, "worktree", "list", "--porcelain").stdout.splitlines():
		if line.startswith("worktree "):
			worktree = Path(line[len("worktree "):]).resolve()
		elif line == f"branch refs/heads/{branch}" and worktree != source:
			result.append(str(worktree))
	return result


def local_branch_work(source, revision, onto, refs):
	"""The commits of revision that onto lacks, oldest first.

	They start where revision last left one of the remote-tracking refs, the
	branch's own first and then the base it was created from, as their reflogs
	record it, so a remote branch rewritten or rewound since then does not
	bring its old commits back as local work. Without such a record the local
	commits are known only when onto is an ancestor of revision.
	"""
	fork = None
	for ref in refs:
		fork = run_git(
			source, "merge-base", "--fork-point", ref, revision, check=False,
		).stdout.strip() or None
		if fork is not None:
			break
	if fork is None:
		if not is_ancestor(source, onto, revision):
			raise WorkspaceError(
				f"Cannot tell which commits of {revision} are local: no reflog of "
				+ " or ".join(refs)
				+ " records where it forked; ask the owner what to do with them"
			)
		fork = onto
	span = [f"{fork}..{revision}", "--not", onto]
	if run_git(source, "rev-list", "--merges", *span).stdout.strip():
		raise WorkspaceError(
			f"{revision} has local merge commits; project branches stay linear"
		)
	return run_git(source, "rev-list", "--reverse", *span).stdout.split()


def publish_module_pins(source, head, branch, project):
	"""Push the submodule commits head pins that no remote branch has yet.

	On a project branch each goes to the submodule's origin branch named like
	the tdesktop branch, which also moves forward when it already exists, as
	project-push keeps a project's submodule branches. Another branch has no
	such submodule branches, so a pin no remote has stops it instead.
	"""
	pushed = []
	for path, module, pin, contained in module_publications(source, head, branch):
		if not project:
			if contained:
				continue
			raise WorkspaceError(
				f"Branch {branch} pins a {path} commit on no remote branch; ask "
				"the owner where it belongs before switching"
			)
		fetch_remote(module, "origin")
		tip = remote_branch_tip(module, "origin", branch)
		if tip is None:
			if contained:
				continue
		elif tip == pin or is_ancestor(module, pin, tip):
			continue
		elif not is_ancestor(module, tip, pin):
			raise WorkspaceError(
				f"{path} branch {branch} on origin has commits the tdesktop branch "
				"does not pin; bring them in before switching"
			)
		push_branch(module, "origin", pin, branch, retry="project-switch")
		pushed.append(path)
	return pushed


def publish_checked_out_branch(source, branch, base, reset):
	"""Line branch, checked out in source, up with private/<branch>.

	Local commits private/<branch> lacks are replayed on top of it, or on
	private/<base> while private/<branch> does not exist, together with their
	submodule commits, and pushed to private/<branch>. Without such commits,
	reset moves the branch to that tip. Without a base, a branch private
	lacks is pushed as it is.
	"""
	project = base is not None
	remote_tip = remote_branch_tip(source, MIRROR_REMOTE, branch)
	name = branch if remote_tip is not None else base
	onto = remote_tip
	if onto is None and project:
		onto = remote_branch_tip(source, MIRROR_REMOTE, base)
		if onto is None:
			raise WorkspaceError(f"{MIRROR_REMOTE}/{base} does not exist")
	head = resolved_ref(source, "HEAD")
	result = {
		"branch": branch,
		"onto": None if onto is None else f"{MIRROR_REMOTE}/{name}",
		"replayed": 0,
		"reset": False,
		"pushed": False,
		"submodules": [],
	}
	if not project:
		publish_module_pins(source, head, branch, project)
	if onto is None:
		if run_git(
			source, "rev-list", "HEAD", "--not",
			f"--remotes={MIRROR_REMOTE}", "--remotes=origin",
		).stdout.strip():
			push_branch(source, MIRROR_REMOTE, head, branch, retry="project-switch")
			result["pushed"] = True
		return result
	refs = [f"refs/remotes/{MIRROR_REMOTE}/{name}"]
	if project and base != name:
		refs.append(f"refs/remotes/{MIRROR_REMOTE}/{base}")
	commits = [] if is_ancestor(source, "HEAD", onto) else local_branch_work(
		source, "HEAD", onto, refs,
	)
	if not commits:
		if reset and head != onto:
			protect_worktrees_from_submodule_target(source, onto)
			run_git(source, "reset", "--quiet", "--hard", onto)
			update_source_submodules(source)
			result["reset"] = True
		return result
	older = resolved_ref(source, f"{commits[0]}^")
	if older != onto:
		ranges = submodule_ranges(source, older, commits)
		head, _ = rewrite_project_history(
			source,
			commits,
			onto,
			ranges,
			{path: gitlink_at(source, onto, path) for path in ranges},
		)
		result["replayed"] = len(commits)
	result["submodules"] = publish_module_pins(source, head, branch, project)
	push_branch(
		source, MIRROR_REMOTE, head, branch, lease=remote_tip,
		retry="project-switch",
	)
	result["pushed"] = True
	return result


def enter_project_branch(source, slug):
	"""Check out slug at what other checkouts published, keeping local work."""
	remote_tip = remote_branch_tip(source, MIRROR_REMOTE, slug)
	name = slug if remote_tip is not None else PROJECT_BASE_BRANCH
	onto = remote_branch_tip(source, MIRROR_REMOTE, name)
	if onto is None:
		raise WorkspaceError(f"{MIRROR_REMOTE}/{name} does not exist")
	local = resolved_ref(source, f"refs/heads/{slug}")
	refs = [f"refs/remotes/{MIRROR_REMOTE}/{name}"]
	if name != PROJECT_BASE_BRANCH:
		refs.append(f"refs/remotes/{MIRROR_REMOTE}/{PROJECT_BASE_BRANCH}")
	moved = False
	if current_branch(source) != slug:
		keep = local is not None and not is_ancestor(source, local, onto) and bool(
			local_branch_work(source, local, onto, refs)
		)
		target = local if keep else onto
		protect_worktrees_from_submodule_target(source, target)
		if keep:
			run_git(source, "switch", "--quiet", slug)
		else:
			run_git(source, "switch", "--quiet", "--no-track", "-C", slug, onto)
			moved = local is not None and local != onto
		update_source_submodules(source)
	result = publish_checked_out_branch(source, slug, PROJECT_BASE_BRANCH, reset=True)
	result["created"] = local is None
	result["reset"] = result["reset"] or moved
	return result


PUBLIC_REMOTE = "origin"
PUBLIC_BASE_BRANCH = "dev"
RELEASE_TAG_PATTERN = re.compile(r"v\d+\.\d+\.\d+")


def latest_release_tag(source):
	"""The newest vX.Y.Z tag on the public base branch, or None."""
	base = remote_branch_tip(source, PUBLIC_REMOTE, PUBLIC_BASE_BRANCH)
	if base is None:
		return None
	for tag in run_git(
		source, "tag", "--list", "v*", "--merged", base, "--sort=-v:refname",
	).stdout.split():
		if RELEASE_TAG_PATTERN.fullmatch(tag):
			return tag
	return None


def project_task_ids(roots, slug):
	"""Every task id, live or retired, whose record in roots names the project."""
	result = set()
	for root in roots:
		result.update(
			task["id"] for task in load_states(root).values()
			if task["project"] == slug
		)
		for records in (load_superseded(root), load_splits(root)):
			result.update(
				task_id for task_id, record in records.items()
				if record.get("project") == slug
			)
	return result


def commit_records(source, span):
	"""(commit, subject, task id or None) for each commit of span, oldest first."""
	result = []
	for entry in run_git(
		source, "log", "--reverse", "--format=%H%x00%s%x00%B%x01", span,
	).stdout.split("\x01"):
		entry = entry.strip("\n")
		if not entry:
			continue
		commit, subject, body = entry.split("\x00", 2)
		lines = body.rstrip("\n").split("\n")
		task = None
		if (
			len(lines) == 3 and lines[0] and not lines[1]
			and lines[2].startswith("Task: ")
		):
			task = lines[2][len("Task: "):]
		result.append((commit, subject, task))
	return result


def push_rebased_project(source, slug, head, lease, tag):
	"""Publish a rebased project branch the way project-push --rewritten does.

	New submodule commits go first to this checkout's staging branch, so every
	pin is fetchable, then tdesktop moves under its lease, then the submodule
	project branches under theirs.
	"""
	publications = module_publications(source, head, slug)
	staging = f"_staging/{slug}/{tag}"
	tips = {}
	for path, module, pin, contained in publications:
		fetch_remote(module, "origin")
		tips[path] = remote_branch_tip(module, "origin", slug)
		if not contained:
			push_branch(module, "origin", pin, staging, force=True, retry="project-switch")
	push_branch(source, MIRROR_REMOTE, head, slug, lease=lease, retry="project-switch")
	pushed = []
	for path, module, pin, contained in publications:
		tip = tips[path]
		if tip == pin or (tip is None and contained):
			continue
		push_branch(module, "origin", pin, slug, lease=tip, retry="project-switch")
		pushed.append(path)
	return pushed


def project_branch_commits(source, head, fork, own, bases):
	"""The project's own commits of fork..head, oldest first.

	A commit whose `Task:` trailer names one of the project's tasks is the
	project's, and one naming another task belongs to the old base. A commit
	without a trailer belongs to the old base when one of bases has it, by
	reachability, patch or subject, and is the project's otherwise, such as a
	commit the owner made on the project branch.
	"""
	known = []
	for base in dict.fromkeys(bases):
		known.append((
			set(run_git(
				source, "rev-list", f"{fork}..{head}", "--not", base,
			).stdout.split()),
			{
				line[2:] for line in run_git(
					source, "cherry", base, head,
				).stdout.splitlines()
				if line.startswith("- ")
			},
			set(run_git(
				source, "log", "--format=%s", f"{head}..{base}",
			).stdout.splitlines()),
		))
	result = []
	for commit, subject, task in commit_records(source, f"{fork}..{head}"):
		if task is not None:
			if task in own:
				result.append((commit, subject, task))
			continue
		if not any(
			commit not in unreachable or commit in equivalent or subject in subjects
			for unreachable, equivalent, subjects in known
		):
			result.append((commit, subject, task))
	return result


def rebase_project_branch(source, own, slug, onto, bases, tag):
	"""Replay the checked-out project branch's own commits onto onto and push.

	Everything else between the old and the new base is replaced by the new
	base. A project commit the new base already has, by patch or by the same
	subject and trailer, is not replayed. A failed push restores the branch.
	"""
	head = resolved_ref(source, "HEAD")
	fork = run_git(source, "merge-base", onto, head, check=False).stdout.strip()
	if not fork:
		raise WorkspaceError(f"Branch {slug} shares no history with its new base")
	if run_git(source, "rev-list", "--merges", f"{fork}..{head}").stdout.strip():
		raise WorkspaceError(
			f"Branch {slug} has merge commits; project branches stay linear"
		)
	total = len(run_git(source, "rev-list", f"{fork}..{head}").stdout.split())
	project = project_branch_commits(source, head, fork, own, [onto, *bases])
	integrated = {
		(subject, task)
		for _, subject, task in commit_records(source, f"{fork}..{onto}")
		if task is not None
	}
	equivalent = {
		line[2:] for line in run_git(source, "cherry", onto, head).stdout.splitlines()
		if line.startswith("- ")
	}
	commits = [
		commit for commit, subject, task in project
		if commit not in equivalent
		and (task is None or (subject, task) not in integrated)
	]
	lease = remote_branch_tip(source, MIRROR_REMOTE, slug)
	try:
		if commits:
			older = resolved_ref(source, f"{commits[0]}^")
			ranges = submodule_ranges(source, older, commits)
			new_head, _ = rewrite_project_history(
				source,
				commits,
				onto,
				ranges,
				{path: gitlink_at(source, onto, path) for path in ranges},
			)
		else:
			protect_worktrees_from_submodule_target(source, onto)
			run_git(source, "reset", "--quiet", "--hard", onto)
			update_source_submodules(source)
			new_head = onto
		submodules = push_rebased_project(source, slug, new_head, lease, tag)
	except BaseException:
		if resolved_ref(source, "HEAD") != head:
			protect_worktrees_from_submodule_target(source, head)
			run_git(source, "reset", "--quiet", "--hard", head)
			update_source_submodules(source)
		raise
	return {
		"replayed": len(commits),
		"already_in_base": len(project) - len(commits),
		"old_base_replaced": total - len(project),
		"previous_tip": head,
		"submodules": submodules,
	}


def refresh_project_base(config, slot, source, slug, task_id, requires):
	"""Rebase the checked-out project branch before a task when it is stale.

	A branch without the newest release tag of the public base branch skipped
	a release, and origin/dev or private/alpha brings that release. A branch
	that lacks approved source work the task requires moves onto
	private/alpha. A base is used only when the branch does not already
	contain it, it brings what is missing, and it has every prerequisite of
	the task and of the project's carried tasks, so a move never drops code
	the project builds on; origin/dev is tried first for a skipped release.
	A release-only move waits while another checkout has a task of the project
	in progress. Returns None when the branch is current, otherwise what was
	found and done, with an error instead when the rebase or push failed and
	the branch was left as it was.
	"""
	release = None
	if PUBLIC_REMOTE in run_git(source, "remote").stdout.split():
		fetch_remote(source, PUBLIC_REMOTE)
		release = latest_release_tag(source)
	skipped = release is not None and not is_ancestor(source, release, "HEAD")
	states = load_states(slot)
	needs = ()
	if task_id is not None:
		if task_id not in states:
			raise WorkspaceError(f"Task does not exist: {task_id}")
		needs = (*states[task_id]["depends_on"], *requires)

	def missing(dependencies, revision):
		return lineage_presence(source, slot, states, dependencies, revision)[3]

	lacking = missing(needs, "HEAD")
	if not skipped and not lacking:
		return None
	result = {
		"skipped_release": release if skipped else None,
		"missing_source_tasks": lacking,
		"onto": None,
	}
	own = project_task_ids([slot, Path(config["ai_main"])], slug)
	carried_needs = (*needs, *(
		dependency for state in states.values()
		if state["id"] in own and state["status"] in ("approved", "in-progress")
		for dependency in state["depends_on"]
	))
	absent_here = set(missing(carried_needs, "HEAD"))
	bases = {
		f"{PUBLIC_REMOTE}/{PUBLIC_BASE_BRANCH}": remote_branch_tip(
			source, PUBLIC_REMOTE, PUBLIC_BASE_BRANCH,
		),
		f"{MIRROR_REMOTE}/{PROJECT_BASE_BRANCH}": remote_branch_tip(
			source, MIRROR_REMOTE, PROJECT_BASE_BRANCH,
		),
	}
	order = list(bases) if skipped else [f"{MIRROR_REMOTE}/{PROJECT_BASE_BRANCH}"]
	chosen = None
	for name in order:
		tip = bases[name]
		if tip is None or is_ancestor(source, tip, "HEAD"):
			continue
		if not (lacking or (skipped and is_ancestor(source, release, tip))):
			continue
		if [
			dependency for dependency in missing(carried_needs, tip)
			if dependency not in own or dependency in absent_here
		]:
			continue
		chosen = (name, tip)
		break
	if chosen is None:
		return result
	elsewhere = sorted(
		task["id"] for task in states.values()
		if task["project"] == slug
		and task["status"] == "in-progress"
		and task["claimed_by"] != config["checkout_tag"]
	)
	if elsewhere and not lacking:
		result["deferred_for"] = elsewhere
		return result
	try:
		result.update(rebase_project_branch(
			source, own, slug, chosen[1],
			[tip for tip in bases.values() if tip is not None],
			config["checkout_tag"],
		))
	except WorkspaceError as error:
		result["error"] = str(error)
		return result
	result["onto"] = chosen[0]
	return result


def command_project_switch(args):
	"""Move this checkout onto a project's branch without leaving work behind.

	The branch being left is published first: its local commits are replayed
	onto private/<branch> and pushed. The project branch is then taken from
	private/<slug>, or from private/alpha while the project has no remote
	branch, with any local commits that never landed replayed on top and
	pushed. Before a task starts, a branch that skipped a release or lacks
	what --task requires is rebased and force-pushed, see
	refresh_project_base. A task this checkout owns keeps its branch as it
	is. Uncommitted changes stop the switch, because nothing tells whose they
	are.
	"""
	config = worktree_config(args, create=True)
	source = Path(config["source_root"]).resolve()
	slot = Path(config["slot_worktree"])
	slug = args.project
	live = live_project_slugs(config["ai_main"]) | live_project_slugs(slot)
	if slug not in live:
		raise WorkspaceError(f"{slug!r} is not a live project")
	owned = [
		task for task in load_states(slot).values()
		if task["claimed_by"] == config["checkout_tag"] and (
			task["status"] in ("in-progress", "split-required")
			or (task["status"] == "todo" and task["carried_from"] is not None)
		)
	]
	for task in owned:
		if task["project"] != slug:
			raise WorkspaceError(
				f"This checkout's {task['status']} task {task['id']} runs on "
				+ (
					f"branch {task['project']}" if task["project"]
					else "a non-project branch"
				)
				+ "; finish it before switching"
			)
	previous = current_branch(source)
	result = {
		"project": slug,
		"previous_branch": previous,
		"left": None,
		"kept_active_work": bool(owned),
	}
	if owned and previous == slug:
		print(json.dumps(result, indent=2, sort_keys=True))
		return
	unknown = source_local_changes(source) + [
		f"{path} (checked-out commit on no remote branch)"
		for path in unpublished_submodule_heads(source)
	]
	if unknown:
		raise WorkspaceError(
			"Uncommitted source changes of unknown origin; ask the owner what to "
			"do with them before switching:\n" + "\n".join(unknown)
		)
	holders = checked_out_elsewhere(source, slug)
	if holders:
		raise WorkspaceError(f"Branch {slug} is checked out in {holders[0]}")
	if owned and resolved_ref(source, f"refs/heads/{slug}") is None:
		raise WorkspaceError(
			f"This checkout's task {owned[0]['id']} has no local branch {slug}"
		)
	if MIRROR_REMOTE not in run_git(source, "remote").stdout.split():
		raise WorkspaceError(f"Project branches need a {MIRROR_REMOTE} remote")
	prepare_source(source)
	fetch_remote(source, MIRROR_REMOTE)
	if previous is None:
		if run_git(
			source, "rev-list", "HEAD", "--not",
			f"--remotes={MIRROR_REMOTE}", "--remotes=origin",
		).stdout.strip():
			raise WorkspaceError(
				"The detached HEAD has commits no remote branch has; ask the owner "
				"what to do with them before switching"
			)
	elif previous != slug:
		result["left"] = publish_checked_out_branch(
			source,
			previous,
			PROJECT_BASE_BRANCH if previous in live else None,
			reset=False,
		)
	if owned:
		protect_worktrees_from_submodule_target(source, f"refs/heads/{slug}")
		run_git(source, "switch", "--quiet", slug)
		update_source_submodules(source)
	else:
		result.update(enter_project_branch(source, slug))
		result["base_refresh"] = refresh_project_base(
			config, slot, source, slug,
			getattr(args, "task", None), getattr(args, "require", None) or [],
		)
	print(json.dumps(result, indent=2, sort_keys=True))


def unpublished_submodule_heads(repo):
	"""Submodules checked out at a commit off their pin that no remote has."""
	result = []
	for path, pin in indexed_gitlinks(repo).items():
		module = Path(repo) / path
		if (
			module.resolve() != module
			or is_linked_worktree(repo, path + "/")
			or not (module / ".git").exists()
		):
			continue
		head = resolved_ref(module, "HEAD")
		if head is not None and head != pin and not run_git(
			module, "branch", "--remotes", "--contains", head, check=False,
		).stdout.strip():
			result.append(path)
		result.extend(
			f"{path}/{nested}" for nested in unpublished_submodule_heads(module)
		)
	return result


def ensure_new_tasks_have_projects(worktree):
	"""Every task a publication adds names a live project.

	The implementation carrier of a legacy split source without a project is
	the exception: its retained work stays on the branch it was begun on.
	"""
	missing = []
	for path in literal_paths(
		worktree, "diff", "--name-only", "--no-renames", "--diff-filter=A",
		"master...HEAD",
	):
		if not (path.startswith("tasks/") and path.endswith("/state.yaml")):
			continue
		task = load_state(worktree, worktree / path)
		project = task["project"]
		if project is None and task["carried_from"] is not None:
			continue
		if project is None or not (
			worktree / "projects" / project / "project.md"
		).is_file():
			missing.append(f"{task['id']} ({project or 'no project'})")
	if missing:
		raise WorkspaceError(
			"Every new task must belong to a live project: " + ", ".join(missing)
		)


def wip_repositories(repo):
	"""Repositories under repo to save, children before parents.

	A repository with changes is saved. A clean submodule is saved too when
	its HEAD moved from the pin its parent last committed and is on no remote
	branch, so the parent's snapshot never pins a commit nobody can fetch.
	"""
	result = []
	for path in indexed_gitlinks(repo):
		module = Path(repo) / path
		if module.resolve() != module or is_linked_worktree(repo, path + "/"):
			raise WorkspaceError(
				f"Cannot save {module}: it is a symlink or another checkout's worktree"
			)
		if not (module / ".git").exists():
			continue
		nested = wip_repositories(module)
		if (
			not nested
			and resolved_ref(module, "HEAD") != gitlink_at(repo, "HEAD", path)
			and not run_git(
				module, "branch", "--remotes", "--contains", "HEAD",
			).stdout.strip()
		):
			nested = [module]
		result.extend(nested)
	if source_changed_paths(repo):
		result.append(Path(repo))
	return result


def command_wip_save(args):
	"""Save source changes nobody can account for and leave the checkout clean.

	Each repository to save, submodules before their parents, commits its
	staged part and then its working tree on a detached HEAD and pushes the
	result to a new branch wip/<branch>/<checkout-tag>/<time>: tdesktop to
	private, submodules to their origin. An ignored working copy of a staged
	deletion is never published; it is copied to ai-wip/<time>/ in the git
	directory instead. Any failure, cancellation included, returns every
	repository to its branch, index and working-tree contents.
	"""
	config = worktree_config(args, create=True)
	source = Path(config["source_root"]).resolve()
	repositories = wip_repositories(source)
	if not repositories:
		print(json.dumps({"saved": False}, indent=2, sort_keys=True))
		return
	for repo in repositories:
		protect_worktrees_from_submodule_target(repo, "HEAD")
		tracked = set(literal_paths(repo, "ls-tree", "-r", "--name-only", "HEAD"))
		for ignored in literal_paths(
			repo, "ls-files", "--others", "--ignored", "--exclude-standard",
			"--directory",
		):
			parts = ignored.rstrip("/").split("/")
			for depth in range(1, len(parts) + 1):
				prefix = "/".join(parts[:depth])
				if prefix in tracked and prefix != ignored:
					raise WorkspaceError(
						f"Cannot save {repo}: ignored {ignored} lies where HEAD "
						f"tracks the file {prefix}, so cleaning would delete it"
					)
	branch = current_branch(source) or "detached"
	stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
	name = f"wip/{branch}/{config['checkout_tag']}/{stamp}"
	message = f"WIP on {branch} from {config['checkout_tag']}\n\n{args.reason}"
	backups = Path(run_git(
		source, "rev-parse", "--path-format=absolute", "--git-common-dir",
	).stdout.strip()) / "ai-wip"
	states = []
	copies = []
	with git_safe_termination():
		save_wip(
			source, repositories, name, message, stamp, config["checkout_tag"],
			backups, states, copies,
		)
	print(json.dumps({
		"saved": True,
		"branch": name,
		"repositories": [
			"." if repo == source else repo.relative_to(source).as_posix()
			for repo in repositories
		],
		"local_copies": [str(copy) for _, copy in copies],
	}, indent=2, sort_keys=True))


def save_wip(source, repositories, name, message, stamp, tag, backups, states, copies):
	local = None
	try:
		for repo in repositories:
			state = {
				"repo": repo,
				"branch": current_branch(repo),
				"head": resolved_ref(repo, "HEAD"),
				"index": run_git(repo, "write-tree").stdout.strip(),
				"snapshot": None,
			}
			states.append(state)
			for path, staged in indexed_gitlinks(repo).items():
				child = repo / path
				if (
					(child / ".git").exists()
					and staged not in (
						gitlink_at(repo, "HEAD", path), resolved_ref(child, "HEAD"),
					)
					and not run_git(
						child, "branch", "--remotes", "--contains", staged,
						check=False,
					).stdout.strip()
				):
					push_branch(child, "origin", staged, f"{name}-staged")
			run_git(repo, "checkout", "--quiet", "--detach")
			if run_git(repo, "diff", "--cached", "--quiet", check=False).returncode:
				run_git(repo, "commit", "--quiet", "-m", f"{message}\n\nStaged part.")
				for path in literal_paths(
					repo, "diff", "--name-only", "--no-renames", "--diff-filter=D",
					state["head"], "HEAD",
				):
					if os.path.lexists(repo / path) and not run_git(
						repo, "check-ignore", "--quiet", "--", path, check=False,
					).returncode:
						if local is None:
							backups.mkdir(parents=True, exist_ok=True)
							local = Path(tempfile.mkdtemp(
								prefix=f"{stamp}-{tag}-", dir=backups,
							))
						copy = local / repo.relative_to(source) / path
						copy.parent.mkdir(parents=True, exist_ok=True)
						shutil.copy2(repo / path, copy, follow_symlinks=False)
						copies.append((repo / path, copy))
			dirty = source_changed_paths(repo)
			if dirty:
				run_git(repo, "--literal-pathspecs", "add", "--all", "--", *dirty)
			if run_git(repo, "diff", "--cached", "--quiet", check=False).returncode:
				run_git(repo, "commit", "--quiet", "-m", message)
			push_branch(
				repo, MIRROR_REMOTE if repo == source else "origin", "HEAD", name,
			)
			state["snapshot"] = resolved_ref(repo, "HEAD")
		for state in reversed(states):
			if state["branch"] is not None:
				run_git(state["repo"], "checkout", "--quiet", "--force", state["branch"])
			else:
				run_git(
					state["repo"], "checkout", "--quiet", "--force", "--detach",
					state["head"],
				)
		update_source_submodules(source)
		ensure_source_clean(source)
	except BaseException:
		for state in reversed(states):
			repo = state["repo"]
			if state["snapshot"] is not None:
				run_git(
					repo, "checkout", "--quiet", "--force", "--detach",
					state["snapshot"],
				)
			run_git(repo, "reset", "--quiet", "--mixed", state["head"])
			run_git(repo, "read-tree", state["index"])
			if state["branch"] is not None:
				run_git(repo, "checkout", "--quiet", state["branch"])
		for original, copy in copies:
			original.parent.mkdir(parents=True, exist_ok=True)
			shutil.copy2(copy, original, follow_symlinks=False)
		raise


>>>>>>> v7.4.4
def run_git_binary(path, *args):
	result = subprocess.run(
		["git", "-C", str(path), *args],
		stdout=subprocess.PIPE,
		stderr=subprocess.PIPE,
	)
	if result.returncode:
		raise WorkspaceError(
			result.stderr.decode("utf-8", "replace").strip()
			or "git failed"
		)
	return result.stdout


def resolved_exe(value):
	path = Path(value).expanduser().resolve()
	if not path.is_file():
		raise WorkspaceError(f"Test executable does not exist: {path}")
	return path


def portable_root_for(exe, override):
	if override:
		root = Path(override).expanduser().resolve()
		if not root.is_dir():
			raise WorkspaceError(f"Portable root does not exist: {root}")
		return root
	for parent in exe.parents:
		if parent.suffix == ".app":
			return parent.parent
	return exe.parent


def unique_destination(directory, name):
	candidate = directory / name
	index = 2
	while candidate.exists():
		candidate = directory / (
			f"{Path(name).stem}-{index:02d}{Path(name).suffix}"
		)
		index += 1
	return candidate


def move_stale_leftover(path, target):
	for attempt in reversed(range(5)):
		moved = unique_destination(target, path.name)
		try:
			shutil.move(path, moved)
			return moved
		except OSError:
			try:
				moved.unlink(missing_ok=True)
			except OSError:
				pass
			if not attempt:
				raise
			time.sleep(0.2)


def clear_stale_crash_state(live, destination):
	report = live / "tdata" / "working"
	dumps_dir = live / "tdata" / "dumps"
	leftovers = []
	if report.is_file() and report.stat().st_size > 0:
		leftovers.append(("report", report))
	if dumps_dir.is_dir():
		leftovers.extend(
			("dump", path) for path in sorted(dumps_dir.glob("*.dmp"))
			if path.is_file()
		)
	cleared = []
	for kind, path in leftovers:
		target = destination / "dumps" if kind == "dump" else destination
		target.mkdir(parents=True, exist_ok=True)
		try:
			moved = move_stale_leftover(path, target)
		except OSError as error:
			if kind == "report":
				raise WorkspaceError(
					f"Cannot clear the stale crash report {path}: {error}"
				) from error
			cleared.append({"from": str(path), "kind": kind, "to": None})
			continue
		cleared.append({"from": str(path), "kind": kind, "to": str(moved)})
	return cleared


def setup_test_account(root):
	golden = root / PORTABLE_GOLDEN
	live = root / PORTABLE_LIVE
	real = root / PORTABLE_REAL
	if not golden.is_dir():
		raise WorkspaceError(f"Missing golden test account: {golden}")
	if (live / PORTABLE_MARKER).exists():
		return "reused-marked-live"
	if live.exists():
		if real.exists():
			shutil.rmtree(live)
			state = "replaced-manual-live"
		else:
			live.rename(real)
			state = "preserved-real"
	else:
		state = "fresh-copy"
	shutil.copytree(golden, live)
	(live / PORTABLE_MARKER).write_text("1\n", encoding="utf-8")
	return state


def reset_broken_test_account(root):
	live = root / PORTABLE_LIVE
	if not (live / PORTABLE_MARKER).exists():
		raise WorkspaceError(
			f"Refusing to reset an unmarked live folder: {live}"
		)
	shutil.rmtree(live)
	return setup_test_account(root)


def processes_with_executable(exe):
	value = str(exe)
	pids = []
	if sys.platform == "win32":
		escaped = value.replace("'", "''")
		script = (
			"Get-CimInstance Win32_Process | "
			f"Where-Object {{ $_.ExecutablePath -eq '{escaped}' }} | "
			"ForEach-Object { $_.ProcessId }"
		)
		result = subprocess.run(
			["powershell", "-NoProfile", "-Command", script],
			stdout=subprocess.PIPE,
			stderr=subprocess.PIPE,
			text=True,
		)
		for line in result.stdout.splitlines():
			line = line.strip()
			if line.isdigit():
				pids.append(int(line))
		return pids
	result = subprocess.run(
		["ps", "-axo", "pid=,comm="],
		stdout=subprocess.PIPE,
		stderr=subprocess.PIPE,
		text=True,
	)
	for line in result.stdout.splitlines():
		parts = line.strip().split(None, 1)
		if len(parts) != 2 or not parts[0].isdigit():
			continue
		pid, comm = int(parts[0]), parts[1]
		if comm == value:
			pids.append(pid)
			continue
		if sys.platform.startswith("linux"):
			try:
				if os.readlink(f"/proc/{pid}/exe") == value:
					pids.append(pid)
			except OSError:
				continue
	return pids


def kill_processes_with_executable(exe):
	killed = []
	for pid in processes_with_executable(exe):
		try:
			if sys.platform == "win32":
				subprocess.run(
					["taskkill", "/PID", str(pid), "/F"],
					stdout=subprocess.PIPE,
					stderr=subprocess.PIPE,
				)
			else:
				os.kill(pid, signal.SIGKILL)
			killed.append(pid)
		except (OSError, subprocess.SubprocessError):
			continue
	return killed


def windows_process_records():
	if sys.platform != "win32":
		return []
	script = (
		"Get-CimInstance Win32_Process | "
		"Select-Object ProcessId,ParentProcessId,Name,ExecutablePath,CommandLine | "
		"ConvertTo-Json -Compress"
	)
	result = subprocess.run(
		["powershell", "-NoProfile", "-Command", script],
		stdout=subprocess.PIPE,
		stderr=subprocess.PIPE,
		text=True,
	)
	if result.returncode or not result.stdout.strip():
		return []
	try:
		rows = json.loads(result.stdout)
	except json.JSONDecodeError:
		return []
	if isinstance(rows, dict):
		rows = [rows]
	records = []
	for row in rows:
		try:
			pid = int(row["ProcessId"])
			parent_pid = int(row["ParentProcessId"])
		except (KeyError, TypeError, ValueError):
			continue
		records.append({
			"pid": pid,
			"parent_pid": parent_pid,
			"name": str(row.get("Name") or ""),
			"executable": str(row.get("ExecutablePath") or ""),
			"command_line": str(row.get("CommandLine") or ""),
		})
	return records


def locking_process_ids(paths):
	if sys.platform != "win32" or not paths:
		return [], None
	import ctypes
	from ctypes import wintypes

	class UniqueProcess(ctypes.Structure):
		_fields_ = [
			("process_id", wintypes.DWORD),
			("process_start_time", wintypes.FILETIME),
		]

	class ProcessInfo(ctypes.Structure):
		_fields_ = [
			("process", UniqueProcess),
			("app_name", wintypes.WCHAR * 256),
			("service_name", wintypes.WCHAR * 64),
			("app_type", wintypes.DWORD),
			("app_status", wintypes.ULONG),
			("terminal_session_id", wintypes.DWORD),
			("restartable", wintypes.BOOL),
		]

	manager = ctypes.WinDLL("Rstrtmgr")
	manager.RmStartSession.argtypes = [
		ctypes.POINTER(wintypes.DWORD),
		wintypes.DWORD,
		wintypes.LPWSTR,
	]
	manager.RmStartSession.restype = wintypes.DWORD
	manager.RmRegisterResources.argtypes = [
		wintypes.DWORD,
		wintypes.UINT,
		ctypes.POINTER(wintypes.LPCWSTR),
		wintypes.UINT,
		ctypes.c_void_p,
		wintypes.UINT,
		ctypes.c_void_p,
	]
	manager.RmRegisterResources.restype = wintypes.DWORD
	manager.RmGetList.argtypes = [
		wintypes.DWORD,
		ctypes.POINTER(wintypes.UINT),
		ctypes.POINTER(wintypes.UINT),
		ctypes.POINTER(ProcessInfo),
		ctypes.POINTER(wintypes.DWORD),
	]
	manager.RmGetList.restype = wintypes.DWORD
	manager.RmEndSession.argtypes = [wintypes.DWORD]
	manager.RmEndSession.restype = wintypes.DWORD

	session = wintypes.DWORD()
	key = ctypes.create_unicode_buffer(33)
	started = manager.RmStartSession(ctypes.byref(session), 0, key)
	if started:
		return [], f"Restart Manager session failed: {started}"
	try:
		resources = (wintypes.LPCWSTR * len(paths))(
			*(str(path) for path in paths)
		)
		registered = manager.RmRegisterResources(
			session,
			len(paths),
			resources,
			0,
			None,
			0,
			None,
		)
		if registered:
			return [], f"Restart Manager registration failed: {registered}"
		needed = wintypes.UINT()
		count = wintypes.UINT()
		reboot_reasons = wintypes.DWORD()
		status = manager.RmGetList(
			session,
			ctypes.byref(needed),
			ctypes.byref(count),
			None,
			ctypes.byref(reboot_reasons),
		)
		if status == 0:
			return [], None
		if status != 234:
			return [], f"Restart Manager query failed: {status}"
		entries = (ProcessInfo * needed.value)()
		count.value = needed.value
		status = manager.RmGetList(
			session,
			ctypes.byref(needed),
			ctypes.byref(count),
			entries,
			ctypes.byref(reboot_reasons),
		)
		if status:
			return [], f"Restart Manager detail query failed: {status}"
		return sorted({
			entries[index].process.process_id
			for index in range(count.value)
		}), None
	finally:
		manager.RmEndSession(session)


def normalized_path_text(path):
	return os.path.normcase(str(Path(path).expanduser().resolve())).casefold()


def recoverable_build_processes(records, build_root, exe, holder_pids):
	build_text = normalized_path_text(build_root)
	build_command_text = build_text.replace("\\", "/")
	exe_text = normalized_path_text(exe)
	by_pid = {record["pid"]: record for record in records}
	reasons = {}
	for record in records:
		pid = record["pid"]
		name = record["name"].casefold()
		executable = record["executable"]
		command_line = record["command_line"]
		if executable and normalized_path_text(executable) == exe_text:
			reasons[pid] = "exact-checkout-executable"
		elif name in BUILD_LOCK_PROCESS_NAMES and pid in holder_pids:
			reasons[pid] = "direct-build-artifact-holder"
		elif (
			name in BUILD_LOCK_PROCESS_NAMES
			and build_command_text
			in command_line.casefold().replace("\\", "/")
		):
			reasons[pid] = "exact-build-tree-command"
	changed = True
	while changed:
		changed = False
		for record in records:
			pid = record["pid"]
			if pid in reasons:
				continue
			if (
				record["name"].casefold() in BUILD_LOCK_PROCESS_NAMES
				and record["parent_pid"] in reasons
				and record["parent_pid"] in by_pid
			):
				reasons[pid] = "verified-build-process-descendant"
				changed = True
	return [
		{
			**by_pid[pid],
			"reason": reason,
		}
		for pid, reason in sorted(reasons.items())
	]


def terminate_process_ids(processes):
	results = []
	for process in sorted(processes, key=lambda value: value["pid"], reverse=True):
		pid = process["pid"]
		try:
			if sys.platform == "win32":
				result = subprocess.run(
					["taskkill", "/PID", str(pid), "/F"],
					stdout=subprocess.PIPE,
					stderr=subprocess.PIPE,
					text=True,
				)
				stopped = not result.returncode
				error = (
					None
					if stopped
					else (result.stderr.strip() or result.stdout.strip())
				)
			else:
				os.kill(pid, signal.SIGKILL)
				stopped = True
				error = None
		except (OSError, subprocess.SubprocessError) as exception:
			stopped = False
			error = str(exception)
		results.append({
			**process,
			"stopped": stopped,
			"error": error,
		})
	return results


def path_inside(path, root):
	try:
		path.relative_to(root)
		return path != root
	except ValueError:
		return False


def build_root_matches_source(build, source):
	cache = build / "CMakeCache.txt"
	if not cache.is_file():
		return False
	prefix = "CMAKE_HOME_DIRECTORY:INTERNAL="
	for line in cache.read_text(encoding="utf-8", errors="replace").splitlines():
		if line.startswith(prefix):
			return (
				normalized_path_text(line[len(prefix):])
				== normalized_path_text(source)
			)
	return False


def command_build_lock_recover(args):
	source = source_root(args.source_root)
	build = Path(args.build_root).expanduser().resolve()
	exe = Path(args.exe).expanduser().resolve()
	if (
		not build.is_dir()
		or not path_inside(build, source)
		or not build_root_matches_source(build, source)
	):
		raise WorkspaceError(
			"Build root must be a configured CMake tree for this checkout: "
			f"{build}"
		)
	if not path_inside(exe, build):
		raise WorkspaceError(f"Executable is outside the build root: {exe}")
	artifacts = []
	for value in args.artifact:
		path = Path(value).expanduser().resolve()
		if not path_inside(path, build):
			raise WorkspaceError(f"Locked artifact is outside the build root: {path}")
		if path.is_dir():
			raise WorkspaceError(f"Locked artifact must be a file: {path}")
		if path not in artifacts:
			artifacts.append(path)
	if not 0 <= args.wait <= 60:
		raise WorkspaceError("--wait must be between 0 and 60 seconds")
	if args.wait:
		time.sleep(args.wait)

	exact_exe_killed = kill_processes_with_executable(exe)
	existing = [path for path in artifacts if path.exists()]
	holder_pids, holder_query_error = locking_process_ids(existing)
	records = windows_process_records()
	recoverable = recoverable_build_processes(
		records,
		build,
		exe,
		set(holder_pids),
	)
	stopped = terminate_process_ids(recoverable)
	if stopped:
		time.sleep(1)

	deleted = []
	already_absent = []
	delete_errors = {}
	for path in artifacts:
		if not path.exists():
			already_absent.append(str(path))
			continue
		try:
			path.unlink()
			deleted.append(str(path))
		except OSError as exception:
			delete_errors[str(path)] = str(exception)

	remaining = [path for path in artifacts if path.exists()]
	remaining_holder_pids, remaining_query_error = locking_process_ids(remaining)
	records_by_pid = {
		record["pid"]: record for record in windows_process_records()
	}
	remaining_holders = [
		records_by_pid.get(pid, {
			"pid": pid,
			"parent_pid": None,
			"name": "",
			"executable": "",
			"command_line": "",
		})
		for pid in remaining_holder_pids
	]
	safe_to_retry = (
		not delete_errors
		and not remaining
		and not remaining_holders
	)
	safety_basis = (
		"all-named-artifacts-deleted-or-absent"
		if safe_to_retry
		else "named-artifact-or-holder-remains"
	)
	print(json.dumps({
		"already_absent": already_absent,
		"artifacts": [str(path) for path in artifacts],
		"build_root": str(build),
		"delete_errors": delete_errors,
		"deleted": deleted,
		"exact_exe_killed": exact_exe_killed,
		"holder_query_error": holder_query_error,
		"remaining_holder_query_error": remaining_query_error,
		"remaining_holders": remaining_holders,
		"safe_to_retry": safe_to_retry,
		"safety_basis": safety_basis,
		"stopped_processes": stopped,
		"wait_seconds": args.wait,
	}, indent=2, sort_keys=True))
	if not safe_to_retry:
		sys.exit(2)


def parse_test_log(text):
	steps = []
	passed = []
	failed = []
	skipped = []
	screenshots = []
	for line in text.splitlines():
		if line.startswith("TEST_STEP: "):
			steps.append(line[len("TEST_STEP: "):])
		elif line.startswith("TEST_RESULT: PASS: "):
			passed.append(line[len("TEST_RESULT: PASS: "):])
		elif line.startswith("TEST_RESULT: FAIL: "):
			failed.append(line[len("TEST_RESULT: FAIL: "):])
		elif line.startswith("TEST_RESULT: N/A: "):
			skipped.append(line[len("TEST_RESULT: N/A: "):])
		elif line.startswith("SCREENSHOT: "):
			screenshots.append(line[len("SCREENSHOT: "):])
	return {
		"steps": steps,
		"pass": passed,
		"fail": failed,
		"skipped": skipped,
		"screenshots": screenshots,
	}


def log_marks_complete(text):
	# Whole-line match, never a substring. A line that merely contains the
	# literal would otherwise end a live run: a note, stage name or check
	# detail quoting the marker, or the permanent MTP seam's
	# "rpc retry code=500 type=TEST_COMPLETE request=0x..." row, whose type
	# comes straight from a server-sent rpc_error. Trailing whitespace is
	# dropped so a stray space or a reader that leaves a CR still counts;
	# leading whitespace is not, because Test::Complete() writes the marker
	# flush left.
	return any(
		line.rstrip() == TEST_COMPLETE_MARKER
		for line in text.splitlines()
	)


def tail_of_file(path, lines=60):
	if not path.is_file():
		return None
	text = path.read_text(encoding="utf-8", errors="replace")
	return "\n".join(text.splitlines()[-lines:]) if text.strip() else None


def parse_env_values(values):
	environment = {}
	for value in values or ():
		if "=" not in value:
			raise WorkspaceError(f"Invalid --env value (want NAME=VALUE): {value!r}")
		name, content = value.split("=", 1)
		if not name:
			raise WorkspaceError(f"Invalid --env value (empty name): {value!r}")
		environment[name] = content
	return environment


def command_test_run(args):
	exe = resolved_exe(args.exe)
	run_dir = Path(args.run_dir).expanduser().resolve()
	run_dir.mkdir(parents=True, exist_ok=True)
	(run_dir / "screenshots").mkdir(exist_ok=True)
	portable = portable_root_for(exe, args.portable_root)
	account = setup_test_account(portable)
	stragglers = kill_processes_with_executable(exe)

	log_path = run_dir / TEST_LOG_FILE
	if log_path.exists():
		log_path.unlink()

	environment = os.environ.copy()
	environment["TDESKTOP_TEST_EVIDENCE_DIR"] = str(run_dir)
	environment.update(parse_env_values(args.env))

	cleared = (
		clear_stale_crash_state(
			portable / PORTABLE_LIVE, run_dir / STALE_CRASH_DIR
		)
		if account == "reused-marked-live"
		else []
	)

	stdout_path = run_dir / "app_stdout.txt"
	stderr_path = run_dir / "app_stderr.txt"
	working = portable / PORTABLE_LIVE / "tdata" / "working"
	dumps_dir = portable / PORTABLE_LIVE / "tdata" / "dumps"

	launched_at = time.time()
	with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
		process = subprocess.Popen(
			[str(exe), "-testagent", "-noupdate"],
			stdout=out,
			stderr=err,
			env=environment,
			cwd=str(portable),
		)
		outcome = None
		exit_code = None
		complete_seen_at = None
		last_size = -1
		last_change = launched_at
		while True:
			time.sleep(0.5)
			now = time.time()
			size = log_path.stat().st_size if log_path.is_file() else -1
			if size != last_size:
				last_size = size
				last_change = now
			complete = False
			if size > 0:
				complete = log_marks_complete(log_path.read_text(
					encoding="utf-8", errors="replace"
				))
			if complete and complete_seen_at is None:
				complete_seen_at = now
			exit_code = process.poll()
			if exit_code is not None:
				outcome = "exited"
				break
			if complete_seen_at is not None and now - complete_seen_at > args.grace:
				process.kill()
				outcome = "killed-after-complete"
				break
			if now - launched_at > args.deadline:
				process.kill()
				outcome = "deadline-killed"
				break
			if now - last_change > args.quiet and complete_seen_at is None:
				process.kill()
				outcome = "quiet-killed"
				break
		process.wait()
	ended_at = time.time()
	kill_processes_with_executable(exe)

	log_text = (
		log_path.read_text(encoding="utf-8", errors="replace")
		if log_path.is_file()
		else ""
	)
	test_complete = log_marks_complete(log_text)
	crash_report_fresh = (
		working.is_file()
		and working.stat().st_mtime >= launched_at
		and working.stat().st_size > 0
	)
	dumps = sorted(
		str(path) for path in dumps_dir.glob("*.dmp")
		if path.stat().st_mtime >= launched_at
	) if dumps_dir.is_dir() else []
	if outcome == "exited":
		if test_complete:
			verdict_hint = "complete"
		elif crash_report_fresh or dumps:
			verdict_hint = "crash"
		else:
			verdict_hint = "died-without-complete"
	elif outcome == "killed-after-complete":
		verdict_hint = "complete"
	else:
		verdict_hint = "hang"

	print(json.dumps({
		"account": account,
		"crash_report": str(working) if working.is_file() else None,
		"crash_report_excerpt": (
			working.read_text(encoding="utf-8", errors="replace")[:4000]
			if crash_report_fresh
			else None
		),
		"crash_report_fresh": crash_report_fresh,
		"dumps": dumps,
		"duration_seconds": round(ended_at - launched_at, 1),
		"exe": str(exe),
		"exit_code": exit_code,
		"log_path": str(log_path) if log_path.is_file() else None,
		"markers": parse_test_log(log_text),
		"outcome": outcome,
		"portable_root": str(portable),
		"run_dir": str(run_dir),
		"stale_crash_cleared": cleared,
		"stderr_tail": tail_of_file(stderr_path),
		"stragglers_killed": stragglers,
		"test_complete": test_complete,
		"verdict_hint": verdict_hint,
	}, indent=2, sort_keys=True))


def command_test_cleanup(args):
	exe = Path(args.exe).expanduser().resolve()
	killed = kill_processes_with_executable(exe)
	deleted = False
	if args.delete_exe and exe.is_file():
		exe.unlink()
		deleted = True
	print(json.dumps({
		"deleted_exe": deleted,
		"exe": str(exe),
		"killed": killed,
	}, indent=2, sort_keys=True))


def command_test_account_reset(args):
	exe = resolved_exe(args.exe)
	kill_processes_with_executable(exe)
	portable = portable_root_for(exe, args.portable_root)
	account = reset_broken_test_account(portable)
	print(json.dumps({
		"account": account,
		"portable_root": str(portable),
	}, indent=2, sort_keys=True))


def overlay_work_dir(config, slot, task_id):
	work = slot / task_relative_dir(task_id) / "work"
	if not work.is_dir():
		raise WorkspaceError(f"Task work directory does not exist: {work}")
	return work


def read_overlay_paths(work):
	paths_file = work / OVERLAY_PATHS_FILE
	if not paths_file.is_file():
		raise WorkspaceError(f"Missing overlay inventory: {paths_file}")
	paths = [
		line.strip() for line in
		paths_file.read_text(encoding="utf-8-sig").splitlines()
		if line.strip()
	]
	if not paths:
		raise WorkspaceError(f"Empty overlay inventory: {paths_file}")
	return paths


def initialized_submodule_paths(source):
	lines = run_git(
		source, "submodule", "status", "--recursive"
	).stdout.splitlines()
	result = []
	for line in lines:
		if not line or line[0] == "-":
			continue
		parts = line[1:].split()
		if len(parts) >= 2:
			result.append(parts[1])
	return sorted(result, key=lambda path: (-path.count("/"), path))


def overlay_inventory_groups(source, inventory):
	submodules = initialized_submodule_paths(source)
	groups = {"": []}
	for value in inventory:
		path = PurePosixPath(value)
		if path.is_absolute() or not path.parts or ".." in path.parts:
			raise WorkspaceError(f"Invalid overlay inventory path: {value!r}")
		value = path.as_posix()
		if value in submodules:
			raise WorkspaceError(
				"Overlay inventory must name a tracked file inside the "
				"submodule, not its gitlink: " + value
			)
		owner = next(
			(
				submodule for submodule in submodules
				if value.startswith(submodule + "/")
			),
			"",
		)
		local = value[len(owner) + 1:] if owner else value
		repository = source / owner if owner else source
		tracked = run_git(
			repository,
			"ls-files",
			"--error-unmatch",
			"--",
			local,
			check=False,
		)
		if tracked.returncode:
			raise WorkspaceError(
				"Overlay inventory paths must be tracked files: " + value
			)
		groups.setdefault(owner, []).append(local)
	return groups, submodules


def overlay_coverage(inventory, repository_path):
	if not repository_path:
		return inventory
	prefix = repository_path + "/"
	return [
		path[len(prefix):]
		for path in inventory
		if path.startswith(prefix)
	]


def overlay_outside_inventory(source, inventory, submodules):
	outside = []
	for repository_path in [""] + submodules:
		repository = source / repository_path if repository_path else source
		coverage = overlay_coverage(inventory, repository_path)
		dirty = changed_paths(repository)
		gitlinks = set(gitlink_paths(repository, dirty))
		for path in dirty:
			covered = path_is_covered(path, coverage)
			covered_gitlink = (
				path in gitlinks
				and any(value.startswith(path + "/") for value in coverage)
			)
			if covered or covered_gitlink:
				continue
			outside.append(
				f"{repository_path}/{path}" if repository_path else path
			)
	return outside


def clear_overlay_submodule_bundle(work):
	manifest = work / OVERLAY_SUBMODULES_FILE
	patches = work / OVERLAY_SUBMODULES_DIR
	if manifest.is_file():
		manifest.unlink()
	if patches.is_dir():
		shutil.rmtree(patches)


def read_overlay_submodule_bundle(work):
	manifest = work / OVERLAY_SUBMODULES_FILE
	if not manifest.is_file():
		return []
	data = json.loads(manifest.read_text(encoding="utf-8"))
	if data.get("version") != 1 or not isinstance(data.get("submodules"), list):
		raise WorkspaceError(f"Invalid overlay submodule manifest: {manifest}")
	result = []
	seen = set()
	for entry in data["submodules"]:
		if not isinstance(entry, dict):
			raise WorkspaceError(f"Invalid overlay submodule entry: {entry!r}")
		repository = PurePosixPath(str(entry.get("path", "")))
		patch = PurePosixPath(str(entry.get("patch", "")))
		if (
			repository.is_absolute()
			or not repository.parts
			or ".." in repository.parts
			or patch.is_absolute()
			or not patch.parts
			or ".." in patch.parts
			or patch.parts[0] != OVERLAY_SUBMODULES_DIR
		):
			raise WorkspaceError(f"Invalid overlay submodule entry: {entry!r}")
		repository_value = repository.as_posix()
		if repository_value in seen:
			raise WorkspaceError(
				"Duplicate overlay submodule entry: " + repository_value
			)
		seen.add(repository_value)
		result.append({
			"patch": patch.as_posix(),
			"path": repository_value,
		})
	return result


def command_overlay_save(args):
	config, slot = task_action_config(args)
	source = Path(config["source_root"])
	work = overlay_work_dir(config, slot, args.task)
	inventory = read_overlay_paths(work)
	groups, submodules = overlay_inventory_groups(source, inventory)
	outside = overlay_outside_inventory(source, inventory, submodules)
	if outside:
		raise WorkspaceError(
			"Dirty source paths are outside the overlay inventory: "
			+ ", ".join(outside)
		)
	clear_overlay_submodule_bundle(work)
	root_paths = groups.get("", [])
	patch = (
		run_git_binary(source, "diff", "--binary", "HEAD", "--", *root_paths)
		if root_paths
		else b""
	)
	patch_path = work / OVERLAY_PATCH_FILE
	if patch.strip():
		patch_path.write_bytes(patch)
	else:
		patch_path.unlink(missing_ok=True)
	submodule_entries = []
	patches_dir = work / OVERLAY_SUBMODULES_DIR
	for repository_path, paths in groups.items():
		if not repository_path:
			continue
		repository = source / repository_path
		repository_patch = run_git_binary(
			repository, "diff", "--binary", "HEAD", "--", *paths
		)
		if not repository_patch.strip():
			continue
		patches_dir.mkdir(parents=True, exist_ok=True)
		name = hashlib.sha256(repository_path.encode("utf-8")).hexdigest()[:16]
		relative_patch = f"{OVERLAY_SUBMODULES_DIR}/{name}.patch"
		submodule_patch_path = work / relative_patch
		submodule_patch_path.write_bytes(repository_patch)
		submodule_entries.append({
			"patch": relative_patch,
			"path": repository_path,
		})
	if submodule_entries:
		(work / OVERLAY_SUBMODULES_FILE).write_text(
			json.dumps({
				"submodules": submodule_entries,
				"version": 1,
			}, indent=2, sort_keys=True) + "\n",
			encoding="utf-8",
		)
	if not patch.strip() and not submodule_entries:
		raise WorkspaceError("The overlay diff is empty; nothing to save")
	checks = []
	if patch.strip():
		checks.append((source, patch_path))
	checks.extend(
		(source / entry["path"], work / entry["patch"])
		for entry in submodule_entries
	)
	for repository, saved_patch in checks:
		check = subprocess.run(
			[
				"git", "-C", str(repository), "apply", "--check",
				"--reverse", str(saved_patch),
			],
			stdout=subprocess.PIPE,
			stderr=subprocess.PIPE,
			text=True,
		)
		if check.returncode:
			raise WorkspaceError(
				"The saved overlay patch does not verify: "
				+ check.stderr.strip()
			)
	restored = []
	if args.restore != "none":
		ref = source_task_ref(args.task, args.restore)
		if resolved_ref(source, ref) is None:
			raise WorkspaceError(f"Missing task ref for restore: {ref}")
		for repository_path, paths in sorted(
			groups.items(), key=lambda item: -item[0].count("/")
		):
			if not repository_path:
				continue
			run_git(source / repository_path, "checkout", "HEAD", "--", *paths)
		if root_paths:
			run_git(source, "checkout", ref, "--", *root_paths)
		restored = inventory
		remaining = []
		for repository_path, paths in groups.items():
			repository = source / repository_path if repository_path else source
			remaining.extend(
				(
					f"{repository_path}/{path}"
					if repository_path else path
				)
				for path in changed_paths(repository)
				if path_is_covered(path, paths)
			)
		if remaining:
			raise WorkspaceError(
				"Overlay paths remain dirty after restore: "
				+ ", ".join(remaining)
			)
	print(json.dumps({
		"patch": str(patch_path) if patch.strip() else None,
		"patch_bytes": len(patch) + sum(
			(work / entry["patch"]).stat().st_size
			for entry in submodule_entries
		),
		"restored": restored,
		"submodules": [entry["path"] for entry in submodule_entries],
		"task": args.task,
	}, indent=2, sort_keys=True))


def command_overlay_apply(args):
	config, slot = task_action_config(args)
	source = Path(config["source_root"])
	work = overlay_work_dir(config, slot, args.task)
	patch_path = work / OVERLAY_PATCH_FILE
	submodule_entries = read_overlay_submodule_bundle(work)
	root_patch = patch_path.is_file() and patch_path.stat().st_size
	if not root_patch and not submodule_entries:
		raise WorkspaceError(f"Missing overlay patch: {patch_path}")
	inventory = read_overlay_paths(work)
	groups, submodules = overlay_inventory_groups(source, inventory)
	for entry in submodule_entries:
		if entry["path"] not in groups or entry["path"] not in submodules:
			raise WorkspaceError(
				"Overlay submodule manifest is outside the inventory: "
				+ entry["path"]
			)
		if not (work / entry["patch"]).is_file():
			raise WorkspaceError(
				"Missing overlay submodule patch: " + entry["patch"]
			)
	applications = []
	if root_patch:
		applications.append(("", source, patch_path))
	applications.extend(
		(entry["path"], source / entry["path"], work / entry["patch"])
		for entry in submodule_entries
	)
	conflicts = []
	errors = []
	for repository_path, repository, saved_patch in applications:
		result = subprocess.run(
			[
				"git", "-C", str(repository), "apply", "--3way",
				str(saved_patch),
			],
			stdout=subprocess.PIPE,
			stderr=subprocess.PIPE,
			text=True,
		)
		if result.returncode:
			errors.append(
				f"{repository_path or '.'}: {result.stderr.strip()}"
			)
		for path in run_git(
			repository, "diff", "--name-only", "--diff-filter=U"
		).stdout.splitlines():
			conflicts.append(
				f"{repository_path}/{path}" if repository_path else path
			)
	outside = overlay_outside_inventory(source, inventory, submodules)
	applied = not errors and not conflicts and not outside
	print(json.dumps({
		"applied": applied,
		"conflicts": conflicts,
		"error": "\n".join(errors) if errors else None,
		"outside_inventory": outside,
		"submodules": [entry["path"] for entry in submodule_entries],
		"task": args.task,
	}, indent=2, sort_keys=True))


def gitlink_paths(source, paths):
	result = []
	for path in paths:
		entry = run_git(source, "ls-files", "-s", "--", path).stdout
		if entry.startswith("160000 "):
			result.append(path)
	return result


def command_source_commit(args):
	config, slot = task_action_config(args)
	if task_type(slot, args.task) == "verify":
		raise WorkspaceError(
			"A verification task carries no implementation and cannot commit "
			"Telegram source; report the deviation as a follow-up task instead"
		)
	source = Path(config["source_root"])
	subject = args.subject.strip()
	if not subject or "\n" in subject:
		raise WorkspaceError("The commit subject must be a single non-empty line")
	if len(subject) > 72:
		raise WorkspaceError(
			f"The commit subject is too long ({len(subject)} > 72 characters)"
		)
	work = slot / task_relative_dir(args.task) / "work"
	owned_file = work / "owned-paths.txt"
	if not owned_file.is_file():
		raise WorkspaceError(f"Missing owned-paths inventory: {owned_file}")
	owned = [
		line.strip() for line in
		owned_file.read_text(encoding="utf-8-sig").splitlines()
		if line.strip()
	]
	if not owned:
		raise WorkspaceError(f"Empty owned-paths inventory: {owned_file}")
	source_note = f"tasks/{args.task}.md"
	allowed = owned + [source_note]
	dirty = changed_paths(source)
	if not dirty:
		raise WorkspaceError("The source checkout has no changes to commit")
	outside = [
		path for path in dirty
		if not path_is_covered(path, allowed)
	]
	if outside:
		raise WorkspaceError(
			"Dirty source paths are outside the owned write set: "
			+ ", ".join(outside)
		)
	submodules = gitlink_paths(source, dirty)
	if submodules:
		raise WorkspaceError(
			"Submodule pointers must be committed explicitly first: "
			+ ", ".join(submodules)
		)
	for path in dirty:
		run_git(source, "add", "--", path)
	run_git(
		source,
		"commit",
		"-m",
		f"{subject}\n\nTask: {args.task}",
	)
	validate_task_commit(source, "HEAD", args.task)
	if args.mark_green:
		mark_source_green(config, args.task)
	print(json.dumps({
		"committed": dirty,
		"marked_green": bool(args.mark_green),
		"subject": subject,
		"task": args.task,
	}, indent=2, sort_keys=True))


def command_source_verify_commit(args):
	source = source_root(args.source_root)
	validate_task_commit(source, args.ref, args.task)
	subject = run_git(
		source, "show", "-s", "--format=%s", args.ref
	).stdout.strip()
	print(json.dumps({
		"ref": args.ref,
		"subject": subject,
		"task": args.task,
		"valid": True,
	}, indent=2, sort_keys=True))


def file_sha256(path):
	digest = hashlib.sha256()
	with path.open("rb") as stream:
		while True:
			chunk = stream.read(1024 * 1024)
			if not chunk:
				break
			digest.update(chunk)
	return digest.hexdigest()


def command_fence_create(args):
	root = Path(args.root).expanduser().resolve()
	if not root.is_dir():
		raise WorkspaceError(f"Fence root does not exist: {root}")
	if not args.paths:
		raise WorkspaceError("No fence paths were provided")
	lines = []
	for value in args.paths:
		path = root / value
		if not path.is_file():
			raise WorkspaceError(f"Fence path does not exist: {path}")
		lines.append(f"{file_sha256(path)}  {value}")
	target = Path(args.file).expanduser().resolve()
	target.parent.mkdir(parents=True, exist_ok=True)
	target.write_text("\n".join(lines) + "\n", encoding="utf-8")
	print(json.dumps({
		"file": str(target),
		"paths": len(lines),
	}, indent=2, sort_keys=True))


def command_fence_check(args):
	root = Path(args.root).expanduser().resolve()
	target = Path(args.file).expanduser().resolve()
	if not target.is_file():
		raise WorkspaceError(f"Fence baseline does not exist: {target}")
	mismatched = []
	missing = []
	checked = 0
	for line in target.read_text(encoding="utf-8-sig").splitlines():
		line = line.strip()
		if not line:
			continue
		if "  " not in line:
			raise WorkspaceError(f"Invalid fence line: {line!r}")
		expected, value = line.split("  ", 1)
		path = root / value
		checked += 1
		if not path.is_file():
			missing.append(value)
		elif file_sha256(path) != expected:
			mismatched.append(value)
	ok = not mismatched and not missing
	print(json.dumps({
		"checked": checked,
		"mismatched": mismatched,
		"missing": missing,
		"ok": ok,
	}, indent=2, sort_keys=True))
	if not ok:
		sys.exit(2)


def command_source_preflight(args):
	config, slot = task_action_config(args)
	source = Path(config["source_root"])
	dirty = changed_paths(source)
	submodule_lines = run_git(
		source, "submodule", "status", "--recursive"
	).stdout.splitlines()
	submodules_dirty = [
		line.strip() for line in submodule_lines
		if line and line[0] in "+-U"
	]
	work = slot / task_relative_dir(args.task) / "work"
	owned_file = work / "owned-paths.txt"
	owned = [
		line.strip() for line in
		owned_file.read_text(encoding="utf-8-sig").splitlines()
		if line.strip()
	] if owned_file.is_file() else []
	dirty_outside_owned = [
		path for path in dirty
		if not path_is_covered(path, owned + [f"tasks/{args.task}.md"])
	]
	result = {
		"dirty": dirty,
		"dirty_outside_owned": dirty_outside_owned,
		"owned_paths_present": owned_file.is_file(),
		"source_clean": not dirty,
		"submodules_dirty": submodules_dirty,
		"task": args.task,
	}
	if args.exe:
		exe = Path(args.exe).expanduser().resolve()
		result["exe_present"] = exe.is_file()
		if exe.is_file():
			portable = portable_root_for(exe, None)
			result["golden_account_present"] = (
				portable / PORTABLE_GOLDEN
			).is_dir()
			result["live_marker_present"] = (
				portable / PORTABLE_LIVE / PORTABLE_MARKER
			).exists()
	print(json.dumps(result, indent=2, sort_keys=True))


def validate_verify_result(lines, result_path, approved):
	if "Touched: none" not in lines:
		raise WorkspaceError(
			f"A verification task must report Touched: none: {result_path}"
		)
	findings = [
		line.split(":", 1)[1].strip() for line in lines
		if line.startswith("Finding:")
	]
	if len(findings) != 1 or findings[0] not in VALID_FINDINGS:
		raise WorkspaceError(
			"A verification task must record exactly one Finding: "
			+ " | ".join(sorted(VALID_FINDINGS))
			+ f": {result_path}"
		)
	finding = findings[0]
	if approved:
		if finding == "inconclusive":
			raise WorkspaceError(
				"An inconclusive verification is blocked, never approved: "
				f"{result_path}"
			)
		if finding == "deviation" and "Discovered: present" not in lines:
			raise WorkspaceError(
				"A verification that found a deviation must route it as a "
				f"discovered follow-up task: {result_path}"
			)
	elif finding != "inconclusive":
		raise WorkspaceError(
			"A verification blocks only when it could not measure, so a blocked "
			f"result must record Finding: inconclusive: {result_path}"
		)


def command_finish(args):
	config, slot = task_action_config(args, allow_project=True)
	ensure_clean(Path(config["source_root"]), "Telegram source checkout")
	kind = task_type(slot, args.task)
	approved = args.status == "approved"
	result_path = slot / task_relative_dir(args.task) / "work" / "result.md"
	if not result_path.is_file():
		raise WorkspaceError(f"Task result is missing: {result_path}")
	result = result_path.read_text(encoding="utf-8-sig")
	lines = result.splitlines()
	expected = "STATUS: DONE" if approved else "STATUS: BLOCKED"
	if expected not in lines:
		raise WorkspaceError(f"Task result does not contain {expected}: {result_path}")
	if approved and not any(
		line in ("Verdict: APPROVED", "Verdict: NOT_APPLICABLE")
		for line in lines
	):
		raise WorkspaceError(f"Task result does not contain an approved verdict: {result_path}")
	if "Checkout: clean-buildable" not in lines:
		raise WorkspaceError(f"Task result does not confirm a clean checkout: {result_path}")
	if kind == "verify":
		validate_verify_result(lines, result_path, approved)
	ensure_no_persisted_commit_hashes(result_path.parents[1])
	source_note = Path(config["source_root"]) / "tasks" / f"{args.task}.md"
	if source_note.is_file():
		ensure_no_persisted_commit_hashes(source_note)
	validate_source_state(config, args.task, approved, kind)
	path = state_path(slot, args.task)
	update_state(path, {
		"status": args.status,
		"phase": "complete" if args.status == "approved" else "blocked",
		"lease_until": None,
	})
	verb = "Approve" if args.status == "approved" else "Block"
	paths = [task_relative_dir(args.task)]
	project = load_state(slot, path)["project"]
	if project is not None:
		project_path = f"projects/{project}/project.md"
		if project_path in changed_paths(slot):
			ensure_no_persisted_commit_hashes(slot / project_path)
			paths.append(project_path)
	commit = commit_paths(
		config,
		paths,
		f"{verb} {args.task}",
	)
	delete_source_refs(
		config,
		args.task,
		retain_implementation=(args.status == "blocked"),
	)
	print(json.dumps({
		"task": args.task,
		"status": args.status,
		"published": bool(commit),
	}, indent=2, sort_keys=True))


def command_publish(args):
	config = worktree_config(args, create=True)
	published = publish_slot(config)
	print(json.dumps({"published": bool(published)}, indent=2, sort_keys=True))


def normalized_publish_path(value):
	path = PurePosixPath(value.replace("\\", "/"))
	minimum_parts = {
		"tasks": 5,
		"projects": 2,
		"receipts": 5,
	}
	if (
		path.is_absolute()
		or not path.parts
		or ".." in path.parts
		or path.parts[0] not in minimum_parts
		or len(path.parts) < minimum_parts.get(path.parts[0], 0)
		or (
			path.parts[0] == "projects"
			and len(path.parts) == 2
			and path.parts[1] == PROJECT_ARCHIVE_DIR
		)
	):
		raise WorkspaceError(f"Invalid inbox publication path: {value!r}")
	return path.as_posix()


def path_is_covered(path, roots):
	return any(path == root or path.startswith(root + "/") for root in roots)


def validate_receipt_text(text, metadata, label):
	if metadata["digest"] not in text:
		raise WorkspaceError(
			f"{label} does not contain the inbox digest: {metadata['digest']}"
		)


def command_inbox_publish(args):
	config = inbox_worktree_config(args, create=True)
	_, metadata = load_transaction(args.transaction)
	if metadata["checkout_tag"] != config["checkout_tag"]:
		raise WorkspaceError("The inbox transaction belongs to another checkout")
	if Path(metadata["ai_main"]).resolve() != Path(config["ai_main"]).resolve():
		raise WorkspaceError(
			"The inbox transaction belongs to another ai-tdesktop repository"
		)
	worktree = Path(config["inbox_worktree"])
	receipt = normalized_publish_path(args.receipt)
	if not receipt.startswith("receipts/"):
		raise WorkspaceError("The tracked receipt must be below receipts/")
	if not (worktree / receipt).is_file():
		raise WorkspaceError(
			f"Tracked receipt is missing from the inbox worktree: {receipt}"
		)
	validate_receipt_text(
		(worktree / receipt).read_text(encoding="utf-8-sig"),
		metadata,
		"Tracked receipt",
	)
	paths = sorted({normalized_publish_path(value) for value in args.paths})
	if not path_is_covered(receipt, paths):
		raise WorkspaceError(
			"The tracked receipt is not covered by an explicit publication path"
		)
	changes = changed_paths(worktree)
	unexpected = [path for path in changes if not path_is_covered(path, paths)]
	if unexpected:
		raise WorkspaceError(
			"Inbox worktree changes are outside the explicit publication paths: "
			+ ", ".join(unexpected)
		)
	unstaged_before = set(
		run_git(worktree, "diff", "--name-only").stdout.splitlines()
	)
	unstaged_before.update(run_git(
		worktree,
		"ls-files",
		"--others",
		"--exclude-standard",
	).stdout.splitlines())
	for path in paths:
		if (
			(worktree / path).exists()
			or any(path_is_covered(change, [path]) for change in unstaged_before)
		):
			run_git(worktree, "add", "-A", "--", path)
	unstaged = run_git(worktree, "diff", "--name-only").stdout.splitlines()
	untracked = run_git(
		worktree,
		"ls-files",
		"--others",
		"--exclude-standard",
	).stdout.splitlines()
	if unstaged or untracked:
		raise WorkspaceError(
			"Inbox worktree changes remain unstaged: "
			+ ", ".join(sorted(set(unstaged + untracked)))
		)
	committed = run_git(
		worktree,
		"diff",
		"--cached",
		"--quiet",
		check=False,
	).returncode != 0
	if committed:
		run_git(
			worktree,
			"commit",
			"-m",
			f"Process inbox for {config['checkout_tag']}",
		)
	published = publish_inbox(config)
	print(json.dumps({
		"committed": committed,
		"inbox_branch": config["inbox_branch"],
		"inbox_worktree": config["inbox_worktree"],
		"published": bool(published),
		"receipt": receipt,
	}, indent=2, sort_keys=True))


def rewrite_project_links(directory, pattern, replacement):
	for path in sorted(directory.rglob("*.md")):
		data = path.read_bytes()
		if data.startswith(b"\xef\xbb\xbf"):
			raise WorkspaceError(f"Refusing to preserve a UTF-8 BOM in {path}")
		text = data.decode("utf-8")
		replaced = pattern.sub(replacement, text)
		if replaced != text:
			path.write_bytes(replaced.encode("utf-8"))


def project_last_activity(states):
	activity = {}
	unfinished = set()
	for task in states.values():
		project = task["project"]
		if project is None:
			continue
		try:
			created = datetime.date.fromisoformat(task["created"])
		except ValueError as error:
			raise WorkspaceError(
				f"Invalid created date in {task['id']}: {task['created']!r}"
			) from error
		if project not in activity or created > activity[project]:
			activity[project] = created
		if task["status"] != "approved":
			unfinished.add(project)
	return activity, unfinished


def command_archive_stale(args):
	if args.days < 1:
		raise WorkspaceError("The archive threshold must be at least one day")
	config = worktree_config(args, create=True)
	sync_canonical(config)
	slot = Path(config["slot_worktree"])
	activity, unfinished = project_last_activity(load_states(slot))
	cutoff = datetime.date.today() - datetime.timedelta(days=args.days)
	projects = slot / "projects"
	candidates = sorted(
		entry.name for entry in projects.iterdir()
		if entry.is_dir() and entry.name != PROJECT_ARCHIVE_DIR
	) if projects.is_dir() else []
	archived = []
	published = False
	for slug in candidates:
		if slug in unfinished or slug not in activity or activity[slug] > cutoff:
			continue
		target = projects / PROJECT_ARCHIVE_DIR / slug
		if target.exists():
			raise WorkspaceError(f"The archive already contains a project: {slug}")
		target.parent.mkdir(exist_ok=True)
		run_git(
			slot,
			"mv",
			f"projects/{slug}",
			f"projects/{PROJECT_ARCHIVE_DIR}/{slug}",
		)
		rewrite_project_links(target, PROJECT_LINK_PATTERN, "](../../../")
		published = commit_paths(
			config,
			[f"projects/{PROJECT_ARCHIVE_DIR}/{slug}"],
			f"Archive {slug}",
		)
		archived.append(slug)
	print(json.dumps({
		"archived": archived,
		"days": args.days,
		"published": bool(published),
	}, indent=2, sort_keys=True))


def unarchive_project(config, worktree_key, slug):
	if slug == PROJECT_ARCHIVE_DIR or not TAG_PATTERN.fullmatch(slug):
		raise WorkspaceError(f"Invalid project slug: {slug!r}")
	worktree = Path(config[worktree_key])
	archived = worktree / "projects" / PROJECT_ARCHIVE_DIR / slug
	target = worktree / "projects" / slug
	if not archived.is_dir():
		raise WorkspaceError(f"No archived project: {slug}")
	if target.exists():
		raise WorkspaceError(f"The project is already live: {slug}")
	run_git(
		worktree,
		"mv",
		f"projects/{PROJECT_ARCHIVE_DIR}/{slug}",
		f"projects/{slug}",
	)
	rewrite_project_links(target, ARCHIVED_PROJECT_LINK_PATTERN, "](../../")
	run_git(worktree, "add", "--", f"projects/{slug}")
	archive_root = worktree / "projects" / PROJECT_ARCHIVE_DIR
	if archive_root.is_dir() and not any(archive_root.iterdir()):
		archive_root.rmdir()
	print(json.dumps({
		"project": slug,
		"published": False,
		"unarchived": True,
	}, indent=2, sort_keys=True))


def command_unarchive(args):
	config = worktree_config(args, create=True)
	unarchive_project(config, "slot_worktree", args.project)


def command_inbox_unarchive(args):
	config = inbox_worktree_config(args, create=True)
	unarchive_project(config, "inbox_worktree", args.project)


def payload_entries(inbox):
	return sorted(
		(path for path in inbox.iterdir() if path.name != "backup"),
		key=lambda path: path.name,
	)


def iter_payload_files(inbox):
	for entry in payload_entries(inbox):
		if entry.is_symlink():
			raise WorkspaceError(f"Inbox symlinks are not supported: {entry}")
		if entry.is_file():
			yield entry
		elif entry.is_dir():
			for path in sorted(entry.rglob("*")):
				if path.is_symlink():
					raise WorkspaceError(f"Inbox symlinks are not supported: {path}")
				if path.is_file():
					yield path


def payload_digest(inbox):
	digest = hashlib.sha256()
	for path in iter_payload_files(inbox):
		relative = path.relative_to(inbox).as_posix().encode("utf-8")
		digest.update(relative)
		digest.update(b"\0")
		with path.open("rb") as stream:
			while True:
				chunk = stream.read(1024 * 1024)
				if not chunk:
					break
				digest.update(chunk)
		digest.update(b"\0")
	return digest.hexdigest()


def copy_payload(inbox, destination):
	for entry in payload_entries(inbox):
		target = destination / entry.name
		if entry.is_symlink():
			raise WorkspaceError(f"Inbox symlinks are not supported: {entry}")
		if entry.is_dir():
			shutil.copytree(entry, target)
		else:
			shutil.copy2(entry, target)


def unique_backup_name(backup, base):
	candidate = base
	index = 2
	while (backup / candidate).exists() or (backup / f".processing-{candidate}").exists():
		candidate = f"{base}-{index:02d}"
		index += 1
	return candidate


def command_ensure(args):
	config = worktree_config(args, create=True)
	print(json.dumps(config, indent=2, sort_keys=True))


def command_inbox_ensure(args):
	config = inbox_worktree_config(args, create=True)
	print(json.dumps(config, indent=2, sort_keys=True))


def command_prepare(args):
	config = inbox_worktree_config(args, create=True)
	inbox = Path(config["inbox"])
	inbox_file = inbox / "inbox.md"
	backup = inbox / "backup"
	active = sorted(backup.glob(".processing-*"))
	if active:
		if len(active) != 1:
			raise WorkspaceError(
				"Several inbox transactions are active: "
				+ ", ".join(str(path) for path in active)
			)
		transaction, metadata = load_transaction(active[0])
		if metadata["checkout_tag"] != config["checkout_tag"]:
			raise WorkspaceError(
				"Active inbox transaction belongs to "
				f"{metadata['checkout_tag']}, not {config['checkout_tag']}"
			)
		if Path(metadata["ai_main"]).resolve() != Path(config["ai_main"]).resolve():
			raise WorkspaceError(
				"Active inbox transaction belongs to another ai-tdesktop repository"
			)
		current_digest = payload_digest(inbox)
		print(json.dumps({
			**config,
			"transaction": str(transaction),
			"digest": metadata["digest"],
			"resumed": True,
			"inbox_changed": current_digest != metadata["digest"],
		}, indent=2, sort_keys=True))
		return
	if not inbox_file.read_text(encoding="utf-8").strip():
		raise WorkspaceError(f"Inbox is empty: {inbox_file}")
	sync_inbox_canonical(config)

	stamp = datetime.datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
	name = unique_backup_name(backup, f"{stamp}-inbox")
	transaction = backup / f".processing-{name}"
	transaction.mkdir()
	digest = payload_digest(inbox)
	copy_payload(inbox, transaction)
	metadata = {
		"version": 1,
		"name": name,
		"digest": digest,
		"created_at": datetime.datetime.now().astimezone().isoformat(),
		**config,
	}
	(transaction / "transaction.json").write_text(
		json.dumps(metadata, indent=2, sort_keys=True) + "\n",
		encoding="utf-8",
	)
	print(json.dumps({
		**config,
		"transaction": str(transaction),
		"digest": digest,
		"resumed": False,
		"inbox_changed": False,
	}, indent=2, sort_keys=True))


def load_transaction(value):
	transaction = Path(value).expanduser().resolve()
	metadata_path = transaction / "transaction.json"
	if not metadata_path.is_file():
		raise WorkspaceError(f"Invalid transaction: {transaction}")
	metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
	backup = Path(metadata["inbox"]) / "backup"
	if transaction.parent.resolve() != backup.resolve():
		raise WorkspaceError(f"Transaction is outside the inbox backup: {transaction}")
	return transaction, metadata


def write_local_receipt(path, metadata, receipt, result):
	text = (
		f"Processed: {metadata['created_at']}\n"
		f"Checkout: {metadata['checkout_tag']}\n"
		f"Digest: {metadata['digest']}\n"
		f"Tracked receipt: {receipt}\n"
		f"Result: {result}\n"
	)
	(path / "receipt.txt").write_text(text, encoding="utf-8")


def clear_payload(inbox):
	for entry in payload_entries(inbox):
		if entry.is_dir() and not entry.is_symlink():
			shutil.rmtree(entry)
		else:
			entry.unlink()
	(inbox / "inbox.md").write_text("", encoding="utf-8")


def command_finalize(args):
	transaction, metadata = load_transaction(args.transaction)
	inbox = Path(metadata["inbox"])
	main = Path(metadata["ai_main"]).resolve()
	receipt_value = normalized_publish_path(args.receipt)
	if not receipt_value.startswith("receipts/"):
		raise WorkspaceError("The tracked receipt must be below receipts/")
	if git_root(main) != main:
		raise WorkspaceError(f"ai-tdesktop is not a worktree root: {main}")
	main_branch = run_git(main, "branch", "--show-current").stdout.strip()
	if main_branch != "master":
		raise WorkspaceError(
			f"The main ai-tdesktop worktree must be on master, not {main_branch!r}."
		)
	tracked = run_git(main, "show", f"HEAD:{receipt_value}", check=False)
	if tracked.returncode:
		raise WorkspaceError(
			f"Tracked receipt is missing from master HEAD: {main / receipt_value}"
		)
	validate_receipt_text(tracked.stdout, metadata, "Tracked receipt on master")
	current_digest = payload_digest(inbox)
	unchanged = current_digest == metadata["digest"]
	backup = inbox / "backup"
	name = metadata["name"] if unchanged else f"{metadata['name']}-processed-snapshot"
	final = backup / name
	if final.exists():
		name = unique_backup_name(backup, name)
		final = backup / name
	transaction.rename(final)
	if unchanged:
		clear_payload(inbox)
	write_local_receipt(
		final,
		metadata,
		receipt_value,
		"inbox-cleared" if unchanged else "inbox-changed-and-left-intact",
	)
	print(json.dumps({
		"backup": str(final),
		"cleared": unchanged,
		"published": True,
	}, indent=2, sort_keys=True))


def command_abort(args):
	transaction, metadata = load_transaction(args.transaction)
	backup = transaction.parent
	name = unique_backup_name(backup, f"{metadata['name']}-failed")
	final = backup / name
	transaction.rename(final)
	write_local_receipt(final, metadata, "not-created", "processing-failed")
	print(json.dumps({"backup": str(final), "cleared": False}, indent=2, sort_keys=True))


def add_common_arguments(parser):
	parser.add_argument("--source-root")
	parser.add_argument("--ai-main")
	parser.add_argument("--worktrees-root")


def parse_args():
	parser = argparse.ArgumentParser()
	subparsers = parser.add_subparsers(dest="command", required=True)

	ensure = subparsers.add_parser("ensure")
	add_common_arguments(ensure)
	ensure.set_defaults(handler=command_ensure)

	inbox_ensure = subparsers.add_parser("inbox-ensure")
	add_common_arguments(inbox_ensure)
	inbox_ensure.set_defaults(handler=command_inbox_ensure)

	prepare = subparsers.add_parser("prepare")
	add_common_arguments(prepare)
	prepare.set_defaults(handler=command_prepare)

	finalize = subparsers.add_parser("finalize")
	finalize.add_argument("--transaction", required=True)
	finalize.add_argument("--receipt", required=True)
	finalize.set_defaults(handler=command_finalize)

	abort = subparsers.add_parser("abort")
	abort.add_argument("--transaction", required=True)
	abort.set_defaults(handler=command_abort)

	queue = subparsers.add_parser("queue")
	add_common_arguments(queue)
	queue.set_defaults(handler=command_queue)

	resolve = subparsers.add_parser("resolve")
	add_common_arguments(resolve)
	resolve.add_argument("--name", required=True)
	resolve.set_defaults(handler=command_resolve)

	start = subparsers.add_parser("start")
	add_common_arguments(start)
	start.add_argument("--task", required=True)
	start.set_defaults(handler=command_start)

	retry = subparsers.add_parser("retry")
	add_common_arguments(retry)
	retry.add_argument("--task", required=True)
	retry.set_defaults(handler=command_retry)

	checkpoint = subparsers.add_parser("checkpoint")
	add_common_arguments(checkpoint)
	checkpoint.add_argument("--task", required=True)
	checkpoint.add_argument("--phase", required=True)
	checkpoint.set_defaults(handler=command_checkpoint)

	source_begin = subparsers.add_parser("source-begin")
	add_common_arguments(source_begin)
	source_begin.add_argument("--task", required=True)
	source_begin.set_defaults(handler=command_source_begin)

	source_mark_green = subparsers.add_parser("source-mark-green")
	add_common_arguments(source_mark_green)
	source_mark_green.add_argument("--task", required=True)
	source_mark_green.set_defaults(handler=command_source_mark_green)

	source_commit = subparsers.add_parser("source-commit")
	add_common_arguments(source_commit)
	source_commit.add_argument("--task", required=True)
	source_commit.add_argument("--subject", required=True)
	source_commit.add_argument("--mark-green", action="store_true")
	source_commit.set_defaults(handler=command_source_commit)

	source_verify_commit = subparsers.add_parser("source-verify-commit")
	add_common_arguments(source_verify_commit)
	source_verify_commit.add_argument("--task", required=True)
	source_verify_commit.add_argument("--ref", default="HEAD")
	source_verify_commit.set_defaults(handler=command_source_verify_commit)

<<<<<<< HEAD
=======
	source_prepare = subparsers.add_parser("source-prepare")
	add_common_arguments(source_prepare)
	source_prepare.set_defaults(handler=command_source_prepare)

	project_sync = subparsers.add_parser("project-sync")
	add_common_arguments(project_sync)
	project_sync.add_argument("--task", required=True)
	project_sync.add_argument("--onto", metavar="REMOTE/BRANCH")
	project_sync.add_argument("--base-fix", metavar="COMMIT")
	project_sync.set_defaults(handler=command_project_sync)

	project_push = subparsers.add_parser("project-push")
	add_common_arguments(project_push)
	project_push.add_argument("--task", required=True)
	project_push.add_argument("--rewritten", action="store_true")
	project_push.add_argument("--base-fix", metavar="COMMIT")
	project_push.add_argument("--base", metavar="REMOTE/BRANCH")
	project_push.set_defaults(handler=command_project_push)

	project_switch = subparsers.add_parser("project-switch")
	add_common_arguments(project_switch)
	project_switch.add_argument("--project", required=True)
	project_switch.add_argument("--task")
	project_switch.add_argument("--require", action="append", default=[])
	project_switch.set_defaults(handler=command_project_switch)

	wip_save = subparsers.add_parser("wip-save")
	add_common_arguments(wip_save)
	wip_save.add_argument("--reason", required=True)
	wip_save.set_defaults(handler=command_wip_save)

>>>>>>> v7.4.4
	source_preflight = subparsers.add_parser("source-preflight")
	add_common_arguments(source_preflight)
	source_preflight.add_argument("--task", required=True)
	source_preflight.add_argument("--exe")
	source_preflight.set_defaults(handler=command_source_preflight)

	build_lock_recover = subparsers.add_parser("build-lock-recover")
	build_lock_recover.add_argument("--source-root", required=True)
	build_lock_recover.add_argument("--build-root", required=True)
	build_lock_recover.add_argument("--exe", required=True)
	build_lock_recover.add_argument("--artifact", action="append", required=True)
	build_lock_recover.add_argument("--wait", type=float, default=10.0)
	build_lock_recover.set_defaults(handler=command_build_lock_recover)

	overlay_save = subparsers.add_parser("overlay-save")
	add_common_arguments(overlay_save)
	overlay_save.add_argument("--task", required=True)
	overlay_save.add_argument(
		"--restore",
		choices=("run", "green", "none"),
		default="run",
	)
	overlay_save.set_defaults(handler=command_overlay_save)

	overlay_apply = subparsers.add_parser("overlay-apply")
	add_common_arguments(overlay_apply)
	overlay_apply.add_argument("--task", required=True)
	overlay_apply.set_defaults(handler=command_overlay_apply)

	test_run = subparsers.add_parser("test-run")
	test_run.add_argument("--exe", required=True)
	test_run.add_argument("--run-dir", required=True)
	test_run.add_argument("--portable-root")
	test_run.add_argument("--deadline", type=float, default=120.0)
	test_run.add_argument("--quiet", type=float, default=60.0)
	test_run.add_argument("--grace", type=float, default=15.0)
	test_run.add_argument("--env", action="append")
	test_run.set_defaults(handler=command_test_run)

	test_cleanup = subparsers.add_parser("test-cleanup")
	test_cleanup.add_argument("--exe", required=True)
	test_cleanup.add_argument("--delete-exe", action="store_true")
	test_cleanup.set_defaults(handler=command_test_cleanup)

	test_account_reset = subparsers.add_parser("test-account-reset")
	test_account_reset.add_argument("--exe", required=True)
	test_account_reset.add_argument("--portable-root")
	test_account_reset.set_defaults(handler=command_test_account_reset)

	fence_create = subparsers.add_parser("fence-create")
	fence_create.add_argument("--file", required=True)
	fence_create.add_argument("--root", default=".")
	fence_create.add_argument("paths", nargs="*")
	fence_create.set_defaults(handler=command_fence_create)

	fence_check = subparsers.add_parser("fence-check")
	fence_check.add_argument("--file", required=True)
	fence_check.add_argument("--root", default=".")
	fence_check.set_defaults(handler=command_fence_check)

	finish = subparsers.add_parser("finish")
	add_common_arguments(finish)
	finish.add_argument("--task", required=True)
	finish.add_argument("--status", choices=("approved", "blocked"), required=True)
	finish.set_defaults(handler=command_finish)

	publish = subparsers.add_parser("publish")
	add_common_arguments(publish)
	publish.set_defaults(handler=command_publish)

	inbox_publish = subparsers.add_parser("inbox-publish")
	add_common_arguments(inbox_publish)
	inbox_publish.add_argument("--transaction", required=True)
	inbox_publish.add_argument("--receipt", required=True)
	inbox_publish.add_argument(
		"--path",
		action="append",
		dest="paths",
		required=True,
	)
	inbox_publish.set_defaults(handler=command_inbox_publish)

	archive_stale = subparsers.add_parser("archive-stale")
	add_common_arguments(archive_stale)
	archive_stale.add_argument("--days", type=int, default=90)
	archive_stale.set_defaults(handler=command_archive_stale)

	unarchive = subparsers.add_parser("unarchive")
	add_common_arguments(unarchive)
	unarchive.add_argument("--project", required=True)
	unarchive.set_defaults(handler=command_unarchive)

	inbox_unarchive = subparsers.add_parser("inbox-unarchive")
	add_common_arguments(inbox_unarchive)
	inbox_unarchive.add_argument("--project", required=True)
	inbox_unarchive.set_defaults(handler=command_inbox_unarchive)

	return parser.parse_args()


def main():
	args = parse_args()
	try:
		args.handler(args)
	except (OSError, ValueError, WorkspaceError) as error:
		print(f"error: {error}", file=sys.stderr)
		return 1
	return 0


if __name__ == "__main__":
	sys.exit(main())
