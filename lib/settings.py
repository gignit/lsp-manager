"""Read and write JSON settings files with idempotent merge support."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

import yaml


# ---------------------------------------------------------------------------
# lsp-manager config
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG = {
    # path_resolve_method: hard|soft|none
    #
    # Controls how lsp-manager resolves server binaries that are not in
    # the user's default PATH.
    #
    # hard: (default) Consume the marketplace plugin's LSP config into
    #       Claude Code's local settings with the full absolute path to
    #       the binary. Preferred because some systems have binaries in
    #       multiple locations and the one in PATH may be the wrong version.
    #
    # soft: Create a symbolic link from bin_dir (e.g. ~/.local/bin) to the
    #       correct binary location. If a binary with the same name appears
    #       earlier in PATH, the wrong binary would still be used.
    #
    # none: Install the server binaries but do not resolve any path issues.
    #       The user is responsible for adding the correct directories to
    #       their PATH.
    'path_resolve_method': 'hard',

    # bin_dir: directory for symbolic links when path_resolve_method is 'soft'.
    'bin_dir': '~/.local/bin',

    # sudo_command: command used for privilege escalation when installing
    # server binaries that require root access.
    'sudo_command': 'sudo',

    # install_timeout: maximum seconds to wait for a server binary install
    # command to complete.
    'install_timeout': 300,
}


def get_config_path() -> Path:
    """Return the path to the lsp-manager config file."""
    return Path(os.path.expanduser('~/.local/share/lsp-manager/config.yaml'))


def load_config() -> dict:
    """Load the lsp-manager config, merging with defaults."""
    config = dict(_DEFAULT_CONFIG)
    config_path = get_config_path()
    if config_path.exists():
        try:
            text = config_path.read_text(encoding='utf-8')
            user_config = yaml.safe_load(text) or {}
            config.update(user_config)
        except (OSError, yaml.YAMLError):
            pass
    return config


def save_default_config(config_path: Optional[Path] = None) -> bool:
    """Write the default config file with documentation comments.

    Returns True if the file was created, False if it already exists.
    """
    if config_path is None:
        config_path = get_config_path()
    if config_path.exists():
        return False

    content = """\
# lsp-manager configuration
# Location: ~/.local/share/lsp-manager/config.yaml

# path_resolve_method: hard | soft | none
#
# Controls how lsp-manager handles server binaries that are not found
# in the user's default PATH (e.g. gopls in ~/go/bin, Homebrew clangd
# in /opt/homebrew/opt/llvm/bin).
#
# hard: (default) Migrate the marketplace plugin's LSP configuration
#       into Claude Code's local settings with the full absolute path
#       to the binary, then uninstall the marketplace plugin. This is
#       preferred because some systems have the same binary installed
#       in multiple locations and the one found in PATH may not be the
#       correct or required version.
#
# soft: Create a symbolic link from bin_dir to the correct binary
#       location. Note: if a different binary with the same name
#       appears earlier in PATH, the wrong version will still be used.
#
# none: Install the server binaries but do not resolve any path
#       issues. The user is responsible for ensuring the correct
#       directories are in their PATH.
#
path_resolve_method: hard

# bin_dir: directory for symbolic links (only used when
# path_resolve_method is 'soft').
bin_dir: "~/.local/bin"

# sudo_command: command used for privilege escalation when installing
# server binaries that require root access (e.g. apt-get).
# Set to "doas" on systems that use doas instead of sudo.
sudo_command: "sudo"

# install_timeout: maximum time in seconds to wait for a server binary
# install command to complete. Increase for slow networks.
install_timeout: 300
"""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(content, encoding='utf-8')
    return True


def load_json(path: Path) -> dict:
    """Load a JSON file, returning empty dict if missing or invalid."""
    if not path.exists():
        return {}
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_json(path: Path, data: dict) -> None:
    """Save a dict to a JSON file atomically with 2-space indent.

    Writes to a sibling tempfile and renames into place so a crash mid-write
    cannot leave the target truncated or empty.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=f'.{path.name}.', suffix='.tmp', dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
            f.write('\n')
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
