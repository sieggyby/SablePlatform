"""Crontab management for scheduled sable-platform workflows."""
from __future__ import annotations

import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass

# Every cron entry managed by sable-platform carries this marker in its comment.
_MARKER = "# sable-platform"
_ENTRY_RE = re.compile(
    r"^(?P<schedule>(?:\S+\s+){4}\S+)\s+"
    r"(?P<command>.+?)\s+"
    r"# sable-platform:(?P<org>[^:\s]+):(?P<workflow>[^:\s]+)$"
)

# Strict identifier pattern for org and workflow names — blocks shell injection,
# crontab content injection, and preserves the :org:workflow marker parsing contract.
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")

# Named schedule shortcuts.
SCHEDULE_PRESETS: dict[str, str] = {
    "hourly": "0 * * * *",
    "daily": "0 6 * * *",
    "twice-weekly": "0 6 * * 1,4",      # Monday + Thursday 06:00 UTC
    "weekly-monday": "0 22 * * 1",
    "weekly-tuesday": "0 22 * * 2",
    "weekly-wednesday": "0 22 * * 3",
    "weekly-thursday": "0 22 * * 4",
    "weekly-friday": "0 22 * * 5",
    "weekly-saturday": "0 22 * * 6",
    "weekly-sunday": "0 22 * * 0",
}


@dataclass
class WorkflowPreset:
    """A bundled cron preset: schedule + command template + org/workflow identifiers."""
    schedule: str
    workflow: str
    description: str
    command_template: str  # {cli_bin} placeholder replaced at install time


# Bundled presets — `sable-platform cron add --preset <name> --org <org>` installs these.
WORKFLOW_PRESETS: dict[str, WorkflowPreset] = {
    "backup": WorkflowPreset(
        schedule="0 3 * * *",  # daily 03:00 UTC
        workflow="backup",
        description="Daily SQLite backup at 03:00 UTC",
        command_template="{cli_bin} backup",
    ),
    "alert_check": WorkflowPreset(
        schedule="0 */4 * * *",  # every 4 hours
        workflow="alert_check",
        description="Evaluate alerts every 4 hours",
        command_template="{cli_bin} alerts evaluate --all-orgs",
    ),
    "gc": WorkflowPreset(
        schedule="0 4 * * 0",  # Sunday 04:00 UTC
        workflow="gc",
        description="Weekly data retention GC on Sundays at 04:00 UTC",
        command_template="{cli_bin} gc",
    ),
}


def _validate_identifier(value: str, label: str) -> None:
    """Reject values that could inject shell commands or break crontab parsing."""
    if not value:
        raise ValueError(f"{label} must not be empty")
    if "\n" in value or "\r" in value:
        raise ValueError(f"{label} must not contain newlines")
    if not _SAFE_ID_RE.match(value):
        raise ValueError(
            f"{label} must contain only alphanumeric, underscore, or hyphen characters: {value!r}"
        )


@dataclass
class CronEntry:
    schedule: str
    command: str
    org: str
    workflow: str

    def to_line(self) -> str:
        return f"{self.schedule} {self.command} {_MARKER}:{self.org}:{self.workflow}"


def _find_cli_binary() -> str:
    """Locate the sable-platform binary."""
    path = shutil.which("sable-platform")
    if path:
        return path
    raise FileNotFoundError(
        "sable-platform not found on PATH. Install with: pip install -e ."
    )


def _read_crontab() -> str:
    """Read current user crontab.  Returns empty string if none exists."""
    result = subprocess.run(
        ["crontab", "-l"], capture_output=True, text=True
    )
    if result.returncode != 0:
        # "no crontab for user" is not an error for our purposes.
        return ""
    return result.stdout


def _write_crontab(content: str) -> None:
    """Write content as the user's crontab.

    Raises subprocess.CalledProcessError if crontab rejects the input.
    """
    subprocess.run(
        ["crontab", "-"], input=content, text=True, check=True
    )


def _parse_entries(crontab_content: str) -> list[CronEntry]:
    """Parse sable-platform entries from raw crontab text."""
    entries: list[CronEntry] = []
    for line in crontab_content.splitlines():
        m = _ENTRY_RE.match(line.strip())
        if m:
            entries.append(CronEntry(
                schedule=m.group("schedule").strip(),
                command=m.group("command").strip(),
                org=m.group("org"),
                workflow=m.group("workflow"),
            ))
    return entries


def list_entries() -> list[CronEntry]:
    """Return all sable-platform cron entries."""
    return _parse_entries(_read_crontab())


def add_entry(
    org: str,
    workflow: str,
    schedule: str,
    *,
    extra_args: str = "",
) -> CronEntry:
    """Add a cron entry for a sable-platform workflow.

    Args:
        org: Org ID (alphanumeric, underscore, hyphen only).
        workflow: Workflow name (alphanumeric, underscore, hyphen only).
        schedule: Cron expression (5 fields) or a preset name from ``SCHEDULE_PRESETS``.
        extra_args: Additional CLI arguments appended after ``--org``.
            Shell-quoted automatically.

    Returns:
        The created CronEntry.

    Raises:
        ValueError: If inputs are invalid or an entry for this org+workflow already exists.
        FileNotFoundError: If sable-platform is not on PATH.
        subprocess.CalledProcessError: If crontab write fails.
    """
    # Validate identifiers — blocks shell injection + crontab content injection.
    _validate_identifier(org, "org")
    _validate_identifier(workflow, "workflow")

    # Resolve preset names.
    resolved = SCHEDULE_PRESETS.get(schedule, schedule)

    # Validate cron expression (5 fields).
    fields = resolved.split()
    if len(fields) != 5:
        raise ValueError(
            f"Invalid cron schedule (need 5 fields): {resolved!r}"
        )

    # Single read — used for both duplicate check and append (avoids TOCTOU).
    current = _read_crontab()

    # Check for duplicates against the content we just read.
    for existing in _parse_entries(current):
        if existing.org == org and existing.workflow == workflow:
            raise ValueError(
                f"Entry already exists for {org}:{workflow} — remove it first"
            )

    cli_bin = _find_cli_binary()
    # Shell-quote all dynamic values in the command.
    cmd = f"{shlex.quote(cli_bin)} workflow run {shlex.quote(workflow)} --org {shlex.quote(org)}"
    if extra_args:
        # Split and re-quote each arg to neutralize any shell metacharacters.
        for arg in shlex.split(extra_args):
            cmd += f" {shlex.quote(arg)}"

    entry = CronEntry(schedule=resolved, command=cmd, org=org, workflow=workflow)

    # Ensure trailing newline so crontab is valid.
    if current and not current.endswith("\n"):
        current += "\n"
    new_content = current + entry.to_line() + "\n"
    _write_crontab(new_content)

    return entry


def add_preset(preset_name: str, org: str) -> CronEntry:
    """Install a bundled workflow preset as a cron entry.

    Args:
        preset_name: Key in ``WORKFLOW_PRESETS``.
        org: Org ID (used for the cron marker; some presets ignore it in the command).

    Returns:
        The created CronEntry.

    Raises:
        ValueError: If preset name is unknown, org is invalid, or entry already exists.
    """
    if preset_name not in WORKFLOW_PRESETS:
        available = ", ".join(sorted(WORKFLOW_PRESETS))
        raise ValueError(f"Unknown preset '{preset_name}'. Available: {available}")

    _validate_identifier(org, "org")

    wp = WORKFLOW_PRESETS[preset_name]
    cli_bin = _find_cli_binary()
    command = wp.command_template.format(cli_bin=shlex.quote(cli_bin))

    current = _read_crontab()
    for existing in _parse_entries(current):
        if existing.org == org and existing.workflow == wp.workflow:
            raise ValueError(
                f"Entry already exists for {org}:{wp.workflow} — remove it first"
            )

    entry = CronEntry(schedule=wp.schedule, command=command, org=org, workflow=wp.workflow)

    if current and not current.endswith("\n"):
        current += "\n"
    new_content = current + entry.to_line() + "\n"
    _write_crontab(new_content)

    return entry


def remove_entry(org: str, workflow: str) -> bool:
    """Remove the cron entry for a given org+workflow.

    Uses regex matching (not substring) to avoid false positives on entries
    that coincidentally contain the marker text.

    Returns True if an entry was removed, False if not found.
    """
    _validate_identifier(org, "org")
    _validate_identifier(workflow, "workflow")

    current = _read_crontab()
    lines = current.splitlines()
    marker_suffix = f"{_MARKER}:{org}:{workflow}"
    new_lines = [line for line in lines if not line.rstrip().endswith(marker_suffix)]

    if len(new_lines) == len(lines):
        return False

    # Write empty string to clear crontab when all entries are removed.
    _write_crontab("\n".join(new_lines) + "\n" if new_lines else "")
    return True
