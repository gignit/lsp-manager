"""Data-gathering helpers for lsp-manager list/status output.

Each fetcher returns plain data structures that can be consumed independently.
The merge/view layer combines them into a unified PluginView per plugin name.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from . import detect


# ---------------------------------------------------------------------------
# Raw data types (one per data source)
# ---------------------------------------------------------------------------

class ServerEntry:
    """One server defined in servers/."""

    def __init__(self, sdef: dict) -> None:
        self.id: str = sdef.get('id', '')
        self.name: str = sdef.get('name', self.id)
        self.enabled: bool = sdef.get('enabled', True)
        self.binary: str = sdef.get('binary', '')
        self.check_command: str = sdef.get('check_command', '')
        self.binary_available: bool = False
        self.binary_version: Optional[str] = None
        # clients.claude config
        cc = sdef.get('clients', {}).get('claude', {})
        self.marketplace: str = cc.get('marketplace', 'claude-plugins-official')
        plugin_name = cc.get('plugin') or self.id
        self.plugin_id: Optional[str] = (
            f'{plugin_name}@{self.marketplace}'
            if (cc.get('marketplace') or cc.get('plugin'))
            else None
        )
        self.lsp_config: Optional[dict] = cc.get('lsp_config')

    def resolve_binary(self) -> None:
        """Check binary availability and version."""
        if not self.enabled or not self.binary:
            return
        self.binary_available = detect.is_available(self.binary)
        if self.binary_available and self.check_command:
            ver = detect.check_binary_version(self.check_command)
            if ver:
                self.binary_version = ver.split('\n')[0].strip()[:60]


class InstalledPluginEntry:
    """One entry from ``claude plugin list`` output."""

    def __init__(
        self,
        qualified_id: str,
        status: str,
        error: Optional[str] = None,
    ) -> None:
        self.qualified_id = qualified_id
        self.name, _, self.marketplace = qualified_id.rpartition('@')
        self.status = status   # 'enabled' | 'disabled' | 'failed'
        self.error = error


class MarketplacePluginEntry:
    """One LSP plugin entry from a marketplace manifest."""

    def __init__(
        self,
        name: str,
        marketplace: str,
        lsp_servers: dict,
    ) -> None:
        self.name = name
        self.marketplace = marketplace
        self.qualified_id = f'{name}@{marketplace}'
        self.lsp_servers = lsp_servers


# ---------------------------------------------------------------------------
# Unified view type (merged across all data sources)
# ---------------------------------------------------------------------------

class MarketplaceStatus:
    """Plugin status within a single marketplace."""

    def __init__(self, marketplace: str) -> None:
        self.marketplace = marketplace
        # 'enabled' | 'disabled' | 'failed' | 'staged' | 'available'
        self.status: str = 'available'
        self.error: Optional[str] = None


class PluginView:
    """Unified view of a single plugin across all data sources.

    Combines server definition, marketplace entries, and installed status
    into one structure for display.
    """

    def __init__(self, plugin_name: str) -> None:
        self.plugin_name = plugin_name
        self.server: Optional[ServerEntry] = None
        self.marketplace_statuses: list[MarketplaceStatus] = []

    @property
    def overall_status(self) -> str:
        """Roll up marketplace statuses to a single overall status.

        enabled  -- at least one marketplace has it enabled
        disabled -- not enabled anywhere (whether in servers/ or marketplace-only)
        """
        statuses = {ms.status for ms in self.marketplace_statuses}
        if 'enabled' in statuses:
            return 'enabled'
        return 'disabled'

    @property
    def health(self) -> str:
        """Health sub-label.

        ok            -- enabled with no errors
        failed        -- enabled but has an error
        ready         -- disabled, in servers/, binary installed, ready to enable
        not installed -- disabled, in servers/, binary not yet installed
        missing       -- disabled, marketplace-only, no servers/ definition
        """
        if self.overall_status == 'enabled':
            for ms in self.marketplace_statuses:
                if ms.status == 'failed':
                    return 'failed'
            return 'ok'
        # disabled
        if self.server:
            return 'ready' if self.server.binary_available else 'not installed'
        return 'missing'

    @property
    def active_marketplace(self) -> Optional[MarketplaceStatus]:
        """The marketplace that has the plugin enabled, if any."""
        for ms in self.marketplace_statuses:
            if ms.status == 'enabled':
                return ms
        return None


# ---------------------------------------------------------------------------
# Data fetchers (independent, no side effects)
# ---------------------------------------------------------------------------

def get_server_entries(server_defs: list[dict]) -> list[ServerEntry]:
    """Build ServerEntry list from server definitions, resolving binary status."""
    entries = []
    for sdef in server_defs:
        e = ServerEntry(sdef)
        e.resolve_binary()
        entries.append(e)
    return entries


def get_installed_plugins() -> list[InstalledPluginEntry]:
    """Parse ``claude plugin list`` output into InstalledPluginEntry objects."""
    code, stdout, _ = detect.run_command('claude plugin list', timeout=15)
    if code != 0 or not stdout:
        return []

    entries: list[InstalledPluginEntry] = []
    current_id: Optional[str] = None
    current_status: Optional[str] = None
    current_error: Optional[str] = None

    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith('❯ '):
            if current_id:
                entries.append(InstalledPluginEntry(
                    current_id, current_status or 'unknown', current_error,
                ))
            current_id = stripped[2:].strip()
            current_status = None
            current_error = None
        elif stripped.startswith('Status:'):
            text = stripped[7:].strip()
            if 'enabled' in text:
                current_status = 'enabled'
            elif 'disabled' in text:
                current_status = 'disabled'
            elif 'failed' in text:
                current_status = 'failed'
            else:
                current_status = text
        elif stripped.startswith('Error:'):
            current_error = stripped[6:].strip()

    if current_id:
        entries.append(InstalledPluginEntry(
            current_id, current_status or 'unknown', current_error,
        ))

    return entries


def get_marketplace_lsp_plugins(
    marketplace_name: str,
    marketplace_path: Path,
) -> list[MarketplacePluginEntry]:
    """Read LSP plugins from a single marketplace manifest.

    Only returns plugins that have a non-empty ``lspServers`` field.
    """
    manifest = marketplace_path / '.claude-plugin' / 'marketplace.json'
    if not manifest.exists():
        return []
    try:
        with open(manifest, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []

    entries = []
    for plugin in data.get('plugins', []):
        lsp_servers = plugin.get('lspServers', {})
        if not lsp_servers:
            continue
        entries.append(MarketplacePluginEntry(
            name=plugin['name'],
            marketplace=marketplace_name,
            lsp_servers=lsp_servers,
        ))
    return entries


def get_all_marketplace_lsp_plugins() -> list[MarketplacePluginEntry]:
    """Gather LSP plugins from all known marketplaces.

    Reads the official marketplace directory and any extras registered in
    ``~/.claude/settings.json`` under ``extraKnownMarketplaces``.
    """
    entries: list[MarketplacePluginEntry] = []

    official_path = Path(os.path.expanduser(
        '~/.claude/plugins/marketplaces/claude-plugins-official'
    ))
    if official_path.is_dir():
        entries.extend(get_marketplace_lsp_plugins('claude-plugins-official', official_path))

    settings_path = Path(os.path.expanduser('~/.claude/settings.json'))
    if settings_path.exists():
        try:
            with open(settings_path, encoding='utf-8') as f:
                settings = json.load(f)
            for mname, mdata in settings.get('extraKnownMarketplaces', {}).items():
                mpath_str = (mdata.get('source') or {}).get('path', '')
                if mpath_str:
                    mpath = Path(os.path.expanduser(mpath_str))
                    if mpath.is_dir():
                        entries.extend(get_marketplace_lsp_plugins(mname, mpath))
        except (OSError, json.JSONDecodeError):
            pass

    return entries


# ---------------------------------------------------------------------------
# Merge / view builder
# ---------------------------------------------------------------------------

def build_plugin_views(
    server_entries: list[ServerEntry],
    installed_plugins: list[InstalledPluginEntry],
    marketplace_plugins: list[MarketplacePluginEntry],
) -> list[PluginView]:
    """Merge all data sources into a unified sorted list of PluginView objects.

    Each PluginView represents one plugin name and carries:
    - the server definition (if in servers/)
    - a MarketplaceStatus per marketplace it appears in
    - the installed/enabled status per marketplace
    """
    from . import claude_plugin

    installed_map: dict[str, InstalledPluginEntry] = {
        e.qualified_id: e for e in installed_plugins
    }

    views: dict[str, PluginView] = {}

    def _get_or_create(name: str) -> PluginView:
        if name not in views:
            views[name] = PluginView(name)
        return views[name]

    # --- Attach server entries ---
    # For each server, determine what plugin name it maps to (official or local)
    for se in server_entries:
        # Determine the canonical plugin name:
        # use upstream plugin name if defined, else fall back to server id
        if se.plugin_id:
            pname = se.plugin_id.rsplit('@', 1)[0]
        elif se.lsp_config:
            pname = claude_plugin.plugin_name_for(se.id)
        else:
            pname = se.id

        view = _get_or_create(pname)
        view.server = se

    # --- Attach marketplace entries ---
    for mp in marketplace_plugins:
        view = _get_or_create(mp.name)
        ms = MarketplaceStatus(mp.marketplace)

        inst = installed_map.get(mp.qualified_id)
        if inst:
            ms.status = inst.status
            ms.error = inst.error
        else:
            # Check if an lsp-manager local plugin replaced the official one
            # (same plugin name, different marketplace)
            local_qid = f'{mp.name}@{claude_plugin.MARKETPLACE_NAME}'
            local_inst = installed_map.get(local_qid)
            if local_inst:
                # The local version is the active one; mark official as staged
                ms.status = 'staged'
            else:
                # In marketplace but not installed
                ms.status = 'not installed'

        view.marketplace_statuses.append(ms)

    # --- Add lsp-manager marketplace statuses for servers with lsp_config
    #     or hard-replaced plugins (their local plugin may not appear in
    #     official marketplace listing) ---
    for se in server_entries:
        if se.lsp_config:
            pname = claude_plugin.plugin_name_for(se.id)
        elif se.plugin_id:
            pname = se.plugin_id.rsplit('@', 1)[0]
        else:
            continue

        view = views.get(pname)
        if not view:
            continue

        local_qid = f'{pname}@{claude_plugin.MARKETPLACE_NAME}'
        # Only add lsp-manager status if not already present
        existing_marketplaces = {ms.marketplace for ms in view.marketplace_statuses}
        if claude_plugin.MARKETPLACE_NAME not in existing_marketplaces:
            inst = installed_map.get(local_qid)
            if inst:
                ms = MarketplaceStatus(claude_plugin.MARKETPLACE_NAME)
                ms.status = inst.status
                ms.error = inst.error
                view.marketplace_statuses.append(ms)

    return sorted(views.values(), key=lambda v: v.plugin_name)
