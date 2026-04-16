"""OS detection, binary availability, and language detection utilities."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Optional

# Snapshot of the user's PATH before lsp-manager modifies it.
# Used to determine if a binary is already accessible without our help.
_ORIGINAL_PATH = os.environ.get('PATH', '')


def get_os() -> str:
    """Return normalized OS name: 'darwin', 'linux', or 'windows'."""
    system = platform.system().lower()
    if system == 'darwin':
        return 'darwin'
    elif system == 'linux':
        return 'linux'
    elif system == 'windows':
        return 'windows'
    return system


def get_arch() -> str:
    """Return normalized architecture: 'amd64', 'arm64', etc."""
    machine = platform.machine().lower()
    if machine in ('x86_64', 'amd64'):
        return 'amd64'
    elif machine in ('aarch64', 'arm64'):
        return 'arm64'
    return machine


def which(binary: str) -> Optional[str]:
    """Find a binary in PATH. Returns the full path or None."""
    return shutil.which(binary)


def is_available(binary: str) -> bool:
    """Check if a binary is available in PATH."""
    return which(binary) is not None


def run_command(
    command: str,
    env: Optional[dict] = None,
    cwd: Optional[str] = None,
    capture: bool = True,
    timeout: int = 300,
) -> tuple[int, str, str]:
    """Run a shell command and return (exit_code, stdout, stderr).

    Args:
        command: Shell command string to execute.
        env: Optional environment variables (merged with current env).
        cwd: Optional working directory.
        capture: If True, capture stdout/stderr. If False, inherit terminal.
        timeout: Timeout in seconds (default 5 minutes).

    Returns:
        Tuple of (exit_code, stdout, stderr). stdout/stderr are empty strings
        when capture=False.
    """
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)

    try:
        result = subprocess.run(
            command,
            shell=True,
            env=merged_env,
            cwd=cwd,
            capture_output=capture,
            text=True,
            timeout=timeout,
        )
        stdout = result.stdout.strip() if capture else ''
        stderr = result.stderr.strip() if capture else ''
        return result.returncode, stdout, stderr
    except subprocess.TimeoutExpired:
        return 124, '', f'Command timed out after {timeout}s: {command}'
    except Exception as e:
        return 1, '', str(e)


def check_binary_version(check_command: str) -> Optional[str]:
    """Run a version check command and return output, or None on failure."""
    code, stdout, stderr = run_command(check_command, timeout=10)
    if code == 0:
        return stdout or stderr  # some tools print version to stderr
    return None


_CMD_SUBST_PATTERN = None  # lazy-compiled in expand_env_path


def expand_env_path(path_str: str) -> str:
    """Expand environment variables and $(command) substitutions in a path.

    Supports three expansion forms, applied in order:
      - ``~`` and ``~user`` (via ``os.path.expanduser``)
      - ``$VAR`` / ``${VAR}`` environment variables (via ``os.path.expandvars``)
      - ``$(command)`` shell command substitution. The command is run with
        a 5-second timeout; its trimmed stdout replaces the expression.
        If the command fails or times out, the expression is replaced with
        an empty string (the caller will typically see ``os.path.isfile``
        return False and fall back gracefully).

    Command substitution is needed for paths that depend on runtime state,
    e.g. ``$(brew --prefix llvm)/bin`` which differs between Intel and
    Apple Silicon Macs.
    """
    import re as _re

    global _CMD_SUBST_PATTERN
    if _CMD_SUBST_PATTERN is None:
        # Non-greedy, no nesting, no escapes. Good enough for path-like values.
        _CMD_SUBST_PATTERN = _re.compile(r'\$\(([^)]+)\)')

    def _run_subst(match) -> str:
        cmd = match.group(1).strip()
        try:
            proc = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=5,
            )
            if proc.returncode != 0:
                return ''
            return proc.stdout.strip()
        except (subprocess.TimeoutExpired, OSError):
            return ''

    expanded = _CMD_SUBST_PATTERN.sub(_run_subst, path_str)
    return os.path.expandvars(os.path.expanduser(expanded))


def ensure_path_contains(directory: str) -> None:
    """Add a directory to PATH if not already present (for current process)."""
    directory = expand_env_path(directory)
    current = os.environ.get('PATH', '')
    if directory not in current.split(os.pathsep):
        os.environ['PATH'] = directory + os.pathsep + current


def check_path_issue(binary: str, env_path: Optional[str] = None) -> Optional[dict]:
    """Check if a binary has a PATH issue that needs resolution.

    Determines whether the correct binary is accessible from the user's
    original PATH (before lsp-manager modifications). Returns a dict
    describing the issue, or None if no issue exists.

    Args:
        binary: Name of the binary to check.
        env_path: The preferred directory from the server definition's
                  env_path field (expanded). If set, the binary at this
                  location is considered the "correct" one.

    Returns:
        None if the correct binary is in the user's original PATH.
        Otherwise a dict with:
            correct_path: absolute path to the correct binary
            found_path: path found in original PATH (or None if not found)
            issue: 'not_in_path' or 'wrong_binary'
    """
    if env_path is None:
        # No preferred location -- just check if the binary is in PATH at all
        found = shutil.which(binary, path=_ORIGINAL_PATH)
        if found:
            return None
        # Not in original PATH -- find it using augmented PATH
        actual = which(binary)
        if not actual:
            return None
        return {
            'correct_path': os.path.realpath(actual),
            'found_path': None,
            'issue': 'not_in_path',
        }

    # Server def specifies a preferred location
    correct = os.path.join(env_path, binary)
    if not os.path.isfile(correct):
        return None
    correct = os.path.realpath(correct)

    found = shutil.which(binary, path=_ORIGINAL_PATH)
    if found is None:
        return {
            'correct_path': correct,
            'found_path': None,
            'issue': 'not_in_path',
        }

    found_real = os.path.realpath(found)
    if found_real != correct:
        return {
            'correct_path': correct,
            'found_path': found_real,
            'issue': 'wrong_binary',
        }

    return None


def create_symlink(binary: str, target_path: str, bin_dir: str) -> Optional[str]:
    """Create a symlink in bin_dir pointing to target_path.

    Args:
        binary: Name of the binary (used as the symlink name).
        target_path: Absolute path to the real binary.
        bin_dir: Directory to create the symlink in.

    Returns:
        The symlink path if created, or None if not needed/possible.
    """
    bin_dir = expand_env_path(bin_dir)
    target_path = os.path.realpath(target_path)
    link_path = os.path.join(bin_dir, binary)

    # Symlink already exists and points to the right place
    if os.path.islink(link_path) and os.path.realpath(link_path) == target_path:
        return None

    os.makedirs(bin_dir, exist_ok=True)

    # Remove stale link if it exists
    if os.path.islink(link_path):
        os.unlink(link_path)
    elif os.path.exists(link_path):
        # A real file exists -- don't overwrite it
        return None

    os.symlink(target_path, link_path)
    return link_path


def confirm_sudo(
    server_name: str,
    sudo_command: str,
    install_command: str,
    auto_yes: bool = False,
) -> str:
    """Ask the user to confirm running a command with sudo.

    Prints a clear description of what will be run and prompts for y/n.

    Args:
        server_name: Human-readable name of the server being installed.
        sudo_command: The privilege-escalation command (e.g. 'sudo').
        install_command: The install command that will be prefixed with sudo.
        auto_yes: If True, skip the prompt and return 'yes' automatically.

    Returns:
        'yes' if approved, 'no' if the user declined,
        'no_tty' if no TTY is available and auto_yes is False.
    """
    import sys

    if auto_yes:
        print(
            f'  [sudo] Installing {server_name} with elevated privileges '
            f'(--yes): {sudo_command} {install_command}',
        )
        return 'yes'

    if not sys.stdin.isatty():
        print(
            f'error: installing {server_name} requires elevated privileges '
            f'({sudo_command}) but no TTY is available.',
            file=sys.stderr,
        )
        print(
            '  Re-run with --yes to allow sudo automatically, '
            'or --no-sudo to skip servers that require it.',
            file=sys.stderr,
        )
        return 'no_tty'

    print()
    print(f'  Installing {server_name} requires elevated privileges.')
    print(f'  Command: {sudo_command} {install_command}')
    print()

    while True:
        try:
            answer = input('  Run with sudo? [y/n]: ').strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 'no'

        if answer in ('y', 'yes'):
            return 'yes'
        elif answer in ('n', 'no'):
            print(f'  Skipped: {server_name} (sudo declined)')
            return 'no'
        else:
            print("  Please enter 'y' or 'n'.")


def ensure_server_paths_in_path(server_defs: list[dict]) -> None:
    """Add all env_path entries from server definitions to the current PATH.

    This ensures that binaries installed to non-standard locations
    (like $GOPATH/bin) are found by which() during doctor and status checks.
    """
    current_os = get_os()
    for sdef in server_defs:
        install = sdef.get('install', {})
        default = install.get('default', {})
        override = install.get(current_os, {})
        for cfg in (default, override):
            env_path = cfg.get('env_path')
            if env_path:
                ensure_path_contains(env_path)
