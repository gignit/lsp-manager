"""Claude Code local-plugin marketplace management.

Claude Code reads ``lspServers`` from the ``lspServers`` field inline in
``marketplace.json`` for each plugin entry. lsp-manager maintains a
persistent local marketplace under
``~/.local/share/lsp-manager/claude-marketplace/``. Each managed server
gets its own stub subdirectory (``plugins/<name>/``) which Claude requires
to exist as the ``source`` target; the directory contains a copy of the
upstream ``LICENSE`` and ``README.md`` if available, otherwise a generated
``README.md`` describing the server.

The ``lspServers`` definition with the correct absolute-path ``command``
lives inline in ``marketplace.json`` -- there is no per-plugin
``plugin.json``.

The marketplace is registered with Claude Code via
``claude plugin marketplace add <path>`` and individual plugins are then
installed via ``claude plugin install <name>@<marketplace>``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from . import detect


# Name that will show up in ``claude plugin marketplace list``. Must not
# impersonate an official Anthropic marketplace and must not contain path
# separators.
MARKETPLACE_NAME = 'lsp-manager'

# Stable on-disk location for the marketplace root.
MARKETPLACE_ROOT = Path(
    os.path.expanduser('~/.local/share/lsp-manager/claude-marketplace')
)


def get_marketplace_root() -> Path:
    """Return the absolute path to the lsp-manager local marketplace."""
    return MARKETPLACE_ROOT


def plugin_name_for(server_id: str, upstream_plugin_id: Optional[str] = None) -> str:
    """Return the managed plugin name for a given server id.

    Uses the upstream plugin name if available (e.g. 'gopls-lsp'),
    otherwise falls back to '<server_id>-lsp'.
    """
    if upstream_plugin_id and '@' in upstream_plugin_id:
        return upstream_plugin_id.rsplit('@', 1)[0]
    return server_id


def plugin_qualified_id(server_id: str, upstream_plugin_id: Optional[str] = None) -> str:
    """Return the fully qualified install id (``name@marketplace``)."""
    return f'{plugin_name_for(server_id, upstream_plugin_id)}@{MARKETPLACE_NAME}'


def _plugin_root(plugin_name: str) -> Path:
    return get_marketplace_root() / 'plugins' / plugin_name


def _marketplace_manifest_path() -> Path:
    return get_marketplace_root() / '.claude-plugin' / 'marketplace.json'



def _read_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Deterministic output so idempotent diffs work.
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write('\n')


def _json_equal(a: Optional[dict], b: Optional[dict]) -> bool:
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# ---------------------------------------------------------------------------
# Manifest management
# ---------------------------------------------------------------------------

def ensure_marketplace_manifest(
    server_ids: list[str],
    lsp_servers_map: dict[str, dict],
    upstream_plugin_ids: Optional[dict[str, str]] = None,
) -> bool:
    """Create or refresh the top-level marketplace.json.

    Each plugin entry includes its ``lspServers`` definition inline so
    Claude Code can read the absolute-path command directly from the
    marketplace manifest. No per-plugin ``plugin.json`` is written.

    Args:
        server_ids: ids of servers that should have managed plugins.
        lsp_servers_map: mapping of server_id -> lspServers dict.
        upstream_plugin_ids: mapping of server_id -> upstream plugin id,
            used to derive the plugin name.

    Returns:
        True if the manifest changed on disk.
    """
    manifest_path = _marketplace_manifest_path()
    plugins = []
    for sid in sorted(server_ids):
        pname = plugin_name_for(sid, (upstream_plugin_ids or {}).get(sid))
        entry = {
            'name': pname,
            'description': (
                f'LSP server for {sid} configured by lsp-manager with '
                f'an absolute-path command'
            ),
            'version': '1.0.0',
            'author': {'name': 'lsp-manager'},
            'source': f'./plugins/{pname}',
        }
        if sid in lsp_servers_map:
            entry['lspServers'] = lsp_servers_map[sid]
        plugins.append(entry)

    desired = {
        'name': MARKETPLACE_NAME,
        'owner': {'name': 'lsp-manager'},
        'plugins': plugins,
    }

    current = _read_json(manifest_path)
    if _json_equal(current, desired):
        return False

    _write_json(manifest_path, desired)
    return True


def ensure_plugin_stub_dir(server_id: str, upstream_plugin_id: Optional[str] = None) -> None:
    """Create the plugin stub directory, copying files from the upstream plugin if available.

    Claude Code requires the ``source`` directory referenced in
    ``marketplace.json`` to exist. This creates the stub and populates it
    by recursively copying whatever files exist in the upstream plugin's
    source directory, so the user has useful documentation available.
    If no upstream plugin exists or its directory is not found, the stub
    is created empty.

    Args:
        server_id: lsp-manager server id (e.g. ``'gopls'``).
        upstream_plugin_id: upstream plugin id in ``name@marketplace``
            format (e.g. ``'gopls-lsp@claude-plugins-official'``), used
            to derive the plugin name and locate source files to copy.
    """
    import shutil

    pname = plugin_name_for(server_id, upstream_plugin_id)
    stub_dir = _plugin_root(pname)
    stub_dir.mkdir(parents=True, exist_ok=True)

    if not upstream_plugin_id or '@' not in upstream_plugin_id:
        return

    plugin_name, marketplace_id = upstream_plugin_id.rsplit('@', 1)
    upstream_dir = Path(os.path.expanduser(
        f'~/.claude/plugins/marketplaces/{marketplace_id}/plugins/{plugin_name}'
    ))

    if not upstream_dir.is_dir():
        return

    for item in upstream_dir.rglob('*'):
        if item.is_file():
            rel = item.relative_to(upstream_dir)
            dest = stub_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest)


def prune_unmanaged_plugin_dirs(
    server_ids: set[str],
    upstream_plugin_ids: Optional[dict[str, str]] = None,
) -> list[str]:
    """Remove plugin stub directories not listed in ``server_ids``.

    Only removes directories whose name ends in ``-lsp`` to avoid
    touching anything the user may have placed there manually.

    Returns the list of plugin names that were pruned.
    """
    plugins_dir = get_marketplace_root() / 'plugins'
    if not plugins_dir.is_dir():
        return []

    expected = {
        plugin_name_for(sid, (upstream_plugin_ids or {}).get(sid))
        for sid in server_ids
    }
    pruned: list[str] = []
    for child in plugins_dir.iterdir():
        if not child.is_dir():
            continue
        if child.name in expected:
            continue
        # Only prune dirs that follow the lsp-manager naming convention.
        if not child.name.endswith('-lsp'):
            continue
        _rmtree(child)
        pruned.append(child.name)
    return pruned


def _rmtree(path: Path) -> None:
    import shutil
    shutil.rmtree(path, ignore_errors=True)


# ---------------------------------------------------------------------------
# Claude CLI integration
# ---------------------------------------------------------------------------

def is_marketplace_registered() -> bool:
    """Return True if the lsp-manager marketplace is known to Claude."""
    code, stdout, _ = detect.run_command(
        'claude plugin marketplace list', timeout=15,
    )
    if code != 0:
        return False
    return MARKETPLACE_NAME in stdout


def register_marketplace() -> tuple[bool, str]:
    """Register (or refresh) the local marketplace with Claude Code.

    If already registered, triggers an update instead of a re-add.

    Returns:
        (success, message)
    """
    root = get_marketplace_root()
    if not _marketplace_manifest_path().exists():
        return False, f'marketplace manifest missing at {root}'

    if is_marketplace_registered():
        code, _, stderr = detect.run_command(
            f'claude plugin marketplace update {MARKETPLACE_NAME}',
            timeout=30,
        )
        if code == 0:
            return True, 'updated'
        return False, f'update failed: {stderr[:200]}'

    code, _, stderr = detect.run_command(
        f'claude plugin marketplace add {root}', timeout=30,
    )
    if code == 0:
        return True, 'added'
    return False, f'add failed: {stderr[:200]}'


def list_installed_plugins() -> str:
    """Return the raw output of ``claude plugin list`` (empty on error)."""
    code, stdout, _ = detect.run_command('claude plugin list', timeout=15)
    return stdout if code == 0 else ''


def install_plugin(
    server_id: str,
    upstream_plugin_id: Optional[str] = None,
    scope: str = 'user',
) -> tuple[bool, str]:
    """Install a managed local plugin. Returns (success, message)."""
    plugin_id = plugin_qualified_id(server_id, upstream_plugin_id)
    cmd = f'claude plugin install {plugin_id} --scope {scope}'
    code, _, stderr = detect.run_command(cmd, timeout=60)
    if code == 0:
        return True, 'installed'
    return False, stderr[:200]


def uninstall_plugin(server_id: str) -> tuple[bool, str]:
    """Uninstall a managed local plugin. Returns (success, message)."""
    plugin_id = plugin_qualified_id(server_id)
    cmd = f'claude plugin uninstall {plugin_id}'
    code, _, stderr = detect.run_command(cmd, timeout=30)
    if code == 0:
        return True, 'uninstalled'
    return False, stderr[:200]
