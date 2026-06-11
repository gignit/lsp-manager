"""Core engine for processing server and client definitions."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import yaml

from . import claude_plugin, detect, listing, rules, settings


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_yaml_or_json(path: Path) -> dict:
    """Load a YAML or JSON file depending on extension."""
    if not path.exists():
        return {}
    text = path.read_text(encoding='utf-8')
    if path.suffix in ('.yaml', '.yml'):
        return yaml.safe_load(text) or {}
    elif path.suffix == '.json':
        return json.loads(text)
    return {}


def _read_marketplace_lsp_config(plugin_id: str) -> Optional[dict]:
    """Read LSP server config from a Claude Code marketplace plugin.

    Args:
        plugin_id: Plugin identifier in 'name@marketplace' format
                   (e.g. 'clangd-lsp@claude-plugins-official').

    Returns:
        The lspServers dict from the marketplace entry, or None if not found.
    """
    if '@' not in plugin_id:
        return None

    plugin_name, marketplace_id = plugin_id.rsplit('@', 1)
    marketplace_path = Path(os.path.expanduser(
        f'~/.claude/plugins/marketplaces/{marketplace_id}'
        f'/.claude-plugin/marketplace.json'
    ))

    if not marketplace_path.exists():
        return None

    try:
        data = json.loads(marketplace_path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        return None

    for plugin in data.get('plugins', []):
        if plugin.get('name') == plugin_name:
            return plugin.get('lspServers')

    return None


import re as _re

# ``id`` fields are used to build on-disk paths (plugin stub dirs,
# marketplace names, etc.). Restrict them to a safe character set to
# prevent path-traversal or shell-metacharacter surprises from hand-edited
# or third-party definition files.
_SAFE_ID_RE = _re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')


def _is_safe_id(value: str) -> bool:
    """Return True if ``value`` is a safe identifier for path construction."""
    return bool(value) and _SAFE_ID_RE.match(value) is not None


def load_definitions(directory: Path, skip_dotfiles: bool = True) -> list[dict]:
    """Load all YAML/JSON definition files from a directory.

    Files starting with '.' are skipped by default (private/examples).
    Files with enabled: false are included but flagged.

    Definitions whose ``id`` field fails the safe-identifier check
    (alphanumeric plus ``.``, ``_``, ``-``; no ``..`` or slashes) are
    rejected with a warning written to stderr. This prevents a malicious
    or malformed YAML from constructing paths outside the marketplace
    root when that ``id`` is later joined into a filesystem path.
    """
    import sys as _sys

    defs = []
    if not directory.is_dir():
        return defs
    for path in sorted(directory.iterdir()):
        if skip_dotfiles and path.name.startswith('.'):
            continue
        if path.suffix in ('.yaml', '.yml', '.json'):
            d = _load_yaml_or_json(path)
            if not d:
                continue
            # Validate id for any def that declares one (server/client).
            ident = d.get('id')
            if ident is not None and not _is_safe_id(str(ident)):
                print(
                    f'warning: skipping {path.name}: unsafe id {ident!r} '
                    f'(must match {_SAFE_ID_RE.pattern})',
                    file=_sys.stderr,
                )
                continue
            d['_source_file'] = str(path)
            defs.append(d)
    return defs


# ---------------------------------------------------------------------------
# Server processing
# ---------------------------------------------------------------------------

class ServerResult:
    """Result of processing a single server definition."""

    def __init__(self, server_id: str, name: str):
        self.server_id = server_id
        self.name = name
        self.binary_was_installed = False
        self.binary_available = False
        self.binary_version: Optional[str] = None
        self.skipped = False
        self.skip_reason: Optional[str] = None
        self.error: Optional[str] = None
        self.install_method: Optional[str] = None  # 'local', 'sudo', 'manual'
        self.manual_commands: list[str] = []
        # Path issue info (set by process_server if binary is not in
        # the user's original PATH or the wrong version is found).
        # Keys: correct_path, found_path, issue ('not_in_path'|'wrong_binary')
        self.path_issue: Optional[dict] = None

    @property
    def ok(self) -> bool:
        return self.binary_available and self.error is None


def process_server(
    server_def: dict,
    dry_run: bool = False,
    config: Optional[dict] = None,
    auto_yes: bool = False,
    no_sudo: bool = False,
) -> ServerResult:
    """Install and verify a single LSP server binary.

    Tries user-local install first. If that fails and the install
    requires root, prompts the user before trying sudo (unless auto_yes
    is True). If no_sudo is True, servers requiring sudo are skipped
    instead of prompting. If no TTY is available and auto_yes is False,
    the server is skipped with an error directing the user to --yes.

    Args:
        server_def: Server definition dict loaded from YAML/JSON.
        dry_run: If True, report what would be done without executing.
        config: lsp-manager config dict (from settings.load_config()).
        auto_yes: If True, approve sudo prompts automatically.
        no_sudo: If True, skip servers that would require sudo.

    Returns:
        ServerResult with status information.
    """
    if config is None:
        config = settings.load_config()

    sudo_command = config.get('sudo_command', 'sudo')
    install_timeout = config.get('install_timeout', 300)

    server_id = server_def.get('id', 'unknown')
    name = server_def.get('name', server_id)
    result = ServerResult(server_id, name)

    # Check enabled flag
    if not server_def.get('enabled', True):
        result.skipped = True
        result.skip_reason = 'disabled'
        return result

    binary = server_def.get('binary', '')
    if not binary:
        result.error = 'no binary specified'
        return result

    check_command = server_def.get('check_command', '')

    # Ensure env_path is in PATH before checking binary availability,
    # so binaries installed to non-standard locations (e.g. $HOME/go/bin)
    # are found without needing to re-install.
    install_config = _resolve_install_config(server_def)
    env_path = None
    if install_config:
        env_path_raw = install_config.get('env_path')
        if env_path_raw:
            env_path = detect.expand_env_path(env_path_raw)
            detect.ensure_path_contains(env_path_raw)

    # Check if already installed and functional
    if detect.is_available(binary):
        if check_command:
            result.binary_version = detect.check_binary_version(check_command)
            if result.binary_version is None:
                # Binary exists but check command failed (e.g. rustup proxy
                # stub without the actual component installed). Fall through
                # to the install step to fix it.
                pass
            else:
                result.binary_available = True
                result.path_issue = detect.check_path_issue(binary, env_path)
                return result
        else:
            result.binary_available = True
            result.path_issue = detect.check_path_issue(binary, env_path)
            return result

    # Resolve OS-specific install config (may already be resolved above
    # for env_path, but needed here for install commands)
    if not install_config:
        result.error = f'no install configuration for {detect.get_os()}'
        return result

    install_command = install_config.get('command')
    if not install_command:
        result.error = f'no install command for {detect.get_os()}'
        return result

    # Check prerequisites
    requires = install_config.get('requires')
    if requires and not detect.is_available(requires):
        result.error = f'prerequisite not available: {requires}'
        result.manual_commands = [
            f'# Install {requires} first, then:',
            install_command,
        ]
        return result

    if dry_run:
        result.install_method = 'dry-run'
        return result

    # Try local install
    code, stdout, stderr = detect.run_command(
        install_command, timeout=install_timeout,
    )
    if code == 0:
        # Re-check binary availability after install
        if detect.is_available(binary):
            result.binary_available = True
            result.binary_was_installed = True
            result.install_method = 'local'
            if check_command:
                result.binary_version = detect.check_binary_version(check_command)
            result.path_issue = detect.check_path_issue(binary, env_path)
            return result

    # Check if it's a permission issue (needs sudo)
    needs_sudo_flag = install_config.get('needs_sudo', False)
    if not needs_sudo_flag and ('permission denied' in stderr.lower() or
                                'eacces' in stderr.lower()):
        needs_sudo_flag = True

    if needs_sudo_flag:
        sudo_cmd = f'{sudo_command} {install_command}'

        if no_sudo:
            result.skipped = True
            result.skip_reason = 'requires sudo (--no-sudo)'
            return result

        confirm = detect.confirm_sudo(
            name, sudo_command, install_command, auto_yes=auto_yes,
        )
        if confirm == 'no_tty':
            result.error = (
                f'requires sudo but no TTY available -- '
                f'rerun with --yes to approve automatically'
            )
            result.manual_commands = [
                '# Run with --yes to approve sudo automatically:',
                'lsp-manager init --yes',
                '# Or run the command directly:',
                sudo_cmd,
            ]
            return result
        if confirm == 'no':
            result.skipped = True
            result.skip_reason = 'requires sudo (declined)'
            return result

        code, stdout, stderr = detect.run_command(
            sudo_cmd, timeout=install_timeout,
        )
        if code == 0 and detect.is_available(binary):
            result.binary_available = True
            result.binary_was_installed = True
            result.install_method = 'sudo'
            if check_command:
                result.binary_version = detect.check_binary_version(check_command)
            result.path_issue = detect.check_path_issue(binary, env_path)
            return result
        # Sudo also failed
        result.error = f'install failed (tried {sudo_command}): {stderr[:200]}'
        result.manual_commands = [
            '# Run the following as root:',
            sudo_cmd,
        ]
    else:
        result.error = f'install failed: {stderr[:200]}'
        result.manual_commands = [install_command]

    return result


def preferred_env_path(server_def: dict) -> Optional[str]:
    """Return the expanded preferred binary directory for a server, if any.

    This is the install config's ``env_path`` for the current platform --
    the directory a correctly installed binary is expected in (e.g.
    ``$HOME/go/bin``). Lets doctor evaluate PATH issues the same way
    init does.
    """
    cfg = _resolve_install_config(server_def) or {}
    env_path = cfg.get('env_path')
    return detect.expand_env_path(env_path) if env_path else None


def _resolve_install_config(server_def: dict) -> Optional[dict]:
    """Resolve OS-specific install config, merging with defaults."""
    install = server_def.get('install', {})
    if not install:
        return None

    current_os = detect.get_os()
    default = install.get('default', {})
    override = install.get(current_os, {})

    # Merge: override takes precedence
    merged = {**default}
    for k, v in override.items():
        if v is not None:
            merged[k] = v

    return merged if merged.get('command') else None


# ---------------------------------------------------------------------------
# Client processing
# ---------------------------------------------------------------------------

class ClientResult:
    """Result of processing a single client definition."""

    def __init__(self, client_id: str, name: str):
        self.client_id = client_id
        self.name = name
        self.available = False
        self.skipped = False
        self.skip_reason: Optional[str] = None
        self.plugins_installed: list[str] = []
        self.plugins_skipped: list[str] = []
        self.plugins_failed: list[str] = []
        self.plugins_uninstalled: list[str] = []
        self.lsp_configs: list[str] = []         # hard path configs written
        self.lsp_configs_skipped: list[str] = []  # already up to date
        self.symlinks_created: list[str] = []     # soft path symlinks
        self.path_warnings: list[str] = []        # warnings for soft/none modes
        self.rules_written = False
        self.rules_skipped = False
        self.error: Optional[str] = None


def process_client(
    client_def: dict,
    server_defs: list[dict],
    server_results: list[ServerResult],
    base_dir: Path,
    project_dir: Optional[Path] = None,
    dry_run: bool = False,
    config: Optional[dict] = None,
) -> ClientResult:
    """Set up a client for all successfully installed servers.

    Args:
        client_def: Client definition dict loaded from YAML/JSON.
        server_defs: Original server definition dicts (for reading client config).
        server_results: List of ServerResult from server processing.
        base_dir: Base directory of lsp-manager installation (for loading
                  rules templates).
        project_dir: Current project directory (for writing project-level
                     config). Uses CWD if None.
        dry_run: If True, report what would be done without executing.
        config: lsp-manager config dict (from settings.load_config()).

    Returns:
        ClientResult with status information.
    """
    if config is None:
        config = settings.load_config()

    client_id = client_def.get('id', 'unknown')
    name = client_def.get('name', client_id)
    result = ClientResult(client_id, name)

    # Check enabled
    if not client_def.get('enabled', True):
        result.skipped = True
        result.skip_reason = 'disabled'
        return result

    # Check if client binary is available
    client_binary = client_def.get('binary', '')
    if not client_binary or not detect.is_available(client_binary):
        result.skipped = True
        result.skip_reason = f'{client_binary or "binary"} not in PATH'
        return result

    result.available = True
    if project_dir is None:
        project_dir = Path.cwd()

    # Build lookup: server_id -> (server_def, server_result)
    result_map = {sr.server_id: sr for sr in server_results}

    # Install plugins and inject LSP configs for each server
    plugins_config = client_def.get('plugins', {})

    _setup_server_integrations(
        result, client_id, plugins_config,
        server_defs, result_map, dry_run, config,
    )

    # Write rules
    rules_config = client_def.get('rules', {})
    if rules_config:
        _write_rules(result, rules_config, base_dir, project_dir, dry_run)

    return result


def _setup_server_integrations(
    result: ClientResult,
    client_id: str,
    plugins_config: dict,
    server_defs: list[dict],
    result_map: dict[str, ServerResult],
    dry_run: bool,
    config: dict,
) -> None:
    """Install plugins and, where needed, generate local LSP plugins.

    Claude Code only reads ``lspServers`` from a plugin's own
    ``<plugin_root>/.claude-plugin/plugin.json``. The top-level
    ``lspServers`` key in ``~/.claude/settings.json`` is silently ignored
    at runtime. So when lsp-manager needs to override the command path
    (hard mode) or inject an inline ``lsp_config`` (no upstream plugin),
    it generates a local plugin under a lsp-manager-managed marketplace
    and installs that plugin via ``claude plugin install``.

    Path resolution modes (``config['path_resolve_method']``):
      hard: Generate a local plugin whose ``plugin.json`` declares the
            LSP server with an absolute-path ``command``. Uninstall any
            upstream plugin it replaces.
      soft: Install the upstream plugin and create a symlink in
            ``bin_dir`` pointing at the correct binary.
      none: Install the upstream plugin only; warn about the path issue.

    Servers with inline ``lsp_config`` (no upstream plugin) always use
    the local-plugin mechanism regardless of resolve_method, because
    writing to settings.json does not work.
    """
    resolve_method = config.get('path_resolve_method', 'hard')
    bin_dir = config.get('bin_dir', '~/.local/bin')

    install_cmd_template = plugins_config.get('install_command', '') if plugins_config else ''
    enable_cmd_template = plugins_config.get('enable_command', '') if plugins_config else ''
    uninstall_cmd_template = plugins_config.get('uninstall_command', '') if plugins_config else ''
    list_cmd = plugins_config.get('list_command', '') if plugins_config else ''
    default_scope = plugins_config.get('default_scope', 'user') if plugins_config else 'user'

    # Cached ``claude plugin list`` result for idempotency checks.
    # Map of qualified_id (e.g. 'gopls-lsp@claude-plugins-official') -> status
    # ('enabled' | 'disabled' | 'failed' | ...). Structured parsing avoids
    # false positives from substring matches between overlapping plugin
    # names (e.g. 'gopls-lsp' vs 'gopls-lsp-extended').
    installed_plugins: dict[str, str] = {}
    if not dry_run:
        for entry in listing.get_installed_plugins():
            installed_plugins[entry.qualified_id] = entry.status

    # Lookup: server_id -> server definition, for per-server scope overrides
    # when processing local plugins.
    server_defs_by_id: dict[str, dict] = {
        s.get('id', ''): s for s in server_defs if s.get('id')
    }

    # Planned local plugins: server_id -> lspServers dict. These are
    # collected across all servers and applied in a single marketplace
    # pass at the end.
    local_plugin_specs: dict[str, dict] = {}
    # Upstream plugins that should be uninstalled because a local plugin
    # replaces them.
    upstream_to_remove: list[str] = []
    # Upstream plugins to install normally.
    upstream_to_install: list[tuple[str, dict]] = []
    # Local '@lsp-manager' plugins to retire once their upstream
    # replacement is confirmed healthy: (local_qid, upstream_plugin_id).
    local_retirements: list[tuple[str, str]] = []

    for sdef in server_defs:
        server_id = sdef.get('id', '')
        binary = sdef.get('binary', '')
        if not sdef.get('enabled', True):
            continue

        # Only process servers whose binary is functional.
        sr = result_map.get(server_id)
        if not sr or not sr.ok:
            continue

        client_config = sdef.get('clients', {}).get(client_id, {})
        if not client_config:
            continue

        # Support new format: marketplace + plugin (defaults to server id)
        # as well as old format: plugin: "name@marketplace"
        # marketplace defaults to claude-plugins-official if not specified.
        marketplace = client_config.get('marketplace')
        if marketplace or client_config.get('plugin'):
            plugin_name = client_config.get('plugin') or server_id
            marketplace = marketplace or 'claude-plugins-official'
            plugin_id = f'{plugin_name}@{marketplace}'
        else:
            plugin_id = None

        lsp_config_inline = client_config.get('lsp_config')
        path_issue = sr.path_issue  # local alias for type narrowing
        has_path_issue = path_issue is not None

        # -------------------------------------------------------------
        # Case A: upstream plugin + path issue
        # -------------------------------------------------------------
        if plugin_id and path_issue is not None:
            correct_path = path_issue['correct_path']
            issue_type = path_issue['issue']
            found_path = path_issue.get('found_path')

            if resolve_method == 'hard':
                marketplace_lsp = _read_marketplace_lsp_config(plugin_id)
                if marketplace_lsp:
                    # Rewrite every LSP server entry from the upstream
                    # marketplace to use the correct absolute path.
                    rewritten = {}
                    for lsp_key, lsp_cfg in marketplace_lsp.items():
                        cfg = dict(lsp_cfg)
                        cfg['command'] = correct_path
                        rewritten[lsp_key] = cfg
                    local_plugin_specs[server_id] = rewritten
                    upstream_to_remove.append(plugin_id)
                else:
                    # Cannot read upstream marketplace -> fall back to
                    # installing the upstream plugin as-is and warn.
                    upstream_to_install.append((plugin_id, client_config))
                    result.path_warnings.append(_path_issue_warning(
                        server_id, issue_type, found_path, correct_path,
                    ))

            elif resolve_method == 'soft':
                upstream_to_install.append((plugin_id, client_config))
                if not dry_run:
                    link = detect.create_symlink(binary, correct_path, bin_dir)
                    if link:
                        result.symlinks_created.append(
                            f'{binary} -> {correct_path}')
                result.path_warnings.append(_path_issue_warning(
                    server_id, issue_type, found_path, correct_path,
                    soft=True,
                ))

            else:  # 'none'
                upstream_to_install.append((plugin_id, client_config))
                result.path_warnings.append(_path_issue_warning(
                    server_id, issue_type, found_path, correct_path,
                ))

            # Inline lsp_config is orthogonal -- fall through to Case C
            # so tests with both fields still work.

        # -------------------------------------------------------------
        # Case B: upstream plugin, no path issue -> install it directly
        # -------------------------------------------------------------
        elif plugin_id and not has_path_issue:
            upstream_to_install.append((plugin_id, client_config))
            # If a previously generated local plugin shadows this server
            # (an official plugin appeared upstream, or a PATH issue got
            # resolved), plan to retire the local copy -- but only after
            # the upstream install is confirmed below, so a failed
            # upstream install never leaves the server with no plugin.
            local_qid = claude_plugin.plugin_qualified_id(server_id, plugin_id)
            if local_qid != plugin_id and local_qid in installed_plugins:
                local_retirements.append((local_qid, plugin_id))

        # -------------------------------------------------------------
        # Case C: inline lsp_config (no upstream plugin)
        # -------------------------------------------------------------
        if lsp_config_inline:
            cfg = dict(lsp_config_inline)
            pi = path_issue
            if pi is not None:
                cfg['command'] = pi['correct_path']
            elif resolve_method == 'hard':
                # Snapshot an absolute command from the install-time PATH.
                # Deliberately not realpath-resolved: symlinked locations
                # like /opt/homebrew/bin/<tool> are stable across package
                # upgrades, their Cellar/node_modules targets are not.
                found = detect.which(cfg.get('command') or binary)
                if found:
                    cfg['command'] = found
            # Key the server entry by server_id for readability.
            local_plugin_specs[server_id] = {server_id: cfg}

    # ----- Apply upstream-plugin installs -----
    for plugin_id, cc in upstream_to_install:
        _install_plugin(
            result, plugin_id, install_cmd_template,
            enable_cmd_template, cc, default_scope,
            installed_plugins, dry_run,
        )

    # ----- Apply upstream-plugin removals (only if currently installed) -----
    for plugin_id in upstream_to_remove:
        if plugin_id not in installed_plugins:
            continue
        if dry_run:
            result.plugins_uninstalled.append(f'{plugin_id} (dry-run)')
            continue
        if uninstall_cmd_template:
            cmd = uninstall_cmd_template.format(plugin=plugin_id)
            code, _, _ = detect.run_command(cmd, timeout=30)
            if code == 0:
                result.plugins_uninstalled.append(plugin_id)

    # ----- Retire local plugins shadowed by a healthy upstream -----
    failed_ids = {f.split(':', 1)[0] for f in result.plugins_failed}
    for local_qid, upstream_pid in local_retirements:
        if upstream_pid in failed_ids:
            continue  # upstream install/enable failed -- keep the local plugin
        if dry_run:
            result.plugins_uninstalled.append(f'{local_qid} (dry-run)')
            continue
        if uninstall_cmd_template:
            cmd = uninstall_cmd_template.format(plugin=local_qid)
            code, _, _ = detect.run_command(cmd, timeout=30)
            if code == 0:
                result.plugins_uninstalled.append(local_qid)
                claude_plugin.remove_marketplace_entry(local_qid.rsplit('@', 1)[0])

    # ----- Apply local plugin marketplace -----
    if local_plugin_specs:
        _apply_local_plugins(
            result, local_plugin_specs, server_defs, installed_plugins,
            default_scope, server_defs_by_id, dry_run,
        )


def _path_issue_warning(
    server_id: str,
    issue_type: str,
    found_path: Optional[str],
    correct_path: str,
    soft: bool = False,
) -> str:
    """Render a human-readable path-issue warning string."""
    if issue_type == 'wrong_binary':
        suffix = 'Symlink may not help. ' if soft else ''
        return (
            f'{server_id}: wrong binary in PATH ({found_path}), '
            f'correct: {correct_path}. {suffix}'
            f'Recommend: path_resolve_method: hard'
        )
    return (
        f'{server_id}: not in PATH, add: {os.path.dirname(correct_path)}'
    )


def _apply_local_plugins(
    result: ClientResult,
    local_plugin_specs: dict[str, dict],
    server_defs: list[dict],
    installed_plugins: dict[str, str],
    default_scope: str,
    server_defs_by_id: Optional[dict[str, dict]] = None,
    dry_run: bool = False,
) -> None:
    """Generate + register + install local plugins for managed servers."""
    server_ids = sorted(local_plugin_specs.keys())
    if server_defs_by_id is None:
        server_defs_by_id = {s.get('id', ''): s for s in server_defs}

    # Build a lookup of server_id -> upstream plugin_id (name@marketplace).
    def _resolve_plugin_id(sid: str, cc: dict) -> str:
        marketplace = cc.get('marketplace')
        if marketplace:
            pname = cc.get('plugin') or sid
            return f'{pname}@{marketplace}'
        return cc.get('plugin', '')

    upstream_plugin_ids: dict[str, str] = {
        sid: pid
        for sdef in server_defs
        for sid in [sdef.get('id', '')]
        for pid in [_resolve_plugin_id(sid, sdef.get('clients', {}).get('claude', {}))]
        if sid and pid
    }

    if dry_run:
        for sid in server_ids:
            correct_cmd = next(
                iter(local_plugin_specs[sid].values())
            ).get('command', '?')
            result.lsp_configs.append(
                f'{sid} -> local plugin ({correct_cmd}) (dry-run)')
            result.plugins_installed.append(
                f'{claude_plugin.plugin_qualified_id(sid, upstream_plugin_ids.get(sid))} (dry-run)')
        return

    # 1. Ensure stub directories exist and are populated with upstream files.
    for sid in server_ids:
        claude_plugin.ensure_plugin_stub_dir(sid, upstream_plugin_ids.get(sid))

    # 2. Write/refresh marketplace.json with lspServers inline.
    changed = claude_plugin.ensure_marketplace_manifest(
        server_ids, local_plugin_specs, upstream_plugin_ids,
    )
    for sid in server_ids:
        correct_cmd = next(
            iter(local_plugin_specs[sid].values())
        ).get('command', '?')
        if changed:
            result.lsp_configs.append(f'{sid} -> local plugin ({correct_cmd})')
        else:
            result.lsp_configs_skipped.append(f'{sid} (local plugin)')

    # 3. Register (or update) the marketplace with Claude Code.
    ok, msg = claude_plugin.register_marketplace()
    if not ok:
        result.error = f'claude marketplace {msg}'
        return

    # 4. Install each managed plugin if not already installed.
    #    Per-server scope override (if set in the YAML) takes priority
    #    over the client default.
    for sid in server_ids:
        qualified = claude_plugin.plugin_qualified_id(sid, upstream_plugin_ids.get(sid))
        if qualified in installed_plugins:
            result.plugins_skipped.append(qualified)
            continue
        sdef = server_defs_by_id.get(sid, {})
        cc = sdef.get('clients', {}).get('claude', {})
        scope = cc.get('scope', default_scope)
        ok, msg = claude_plugin.install_plugin(
            sid, upstream_plugin_ids.get(sid), scope=scope,
        )
        if ok:
            result.plugins_installed.append(qualified)
        else:
            result.plugins_failed.append(f'{qualified}: {msg}')


def _install_plugin(
    result: ClientResult,
    plugin_id: str,
    install_cmd_template: str,
    enable_cmd_template: str,
    client_config: dict,
    default_scope: str,
    installed_plugins: dict[str, str],
    dry_run: bool,
) -> None:
    """Install and enable a marketplace plugin.

    If the plugin is already installed, checks whether it is disabled
    and enables it if needed.
    """
    if not install_cmd_template:
        return
    scope = client_config.get('scope', default_scope)
    if plugin_id in installed_plugins:
        # Already installed -- check its status and enable if disabled.
        _check_and_enable_plugin(
            result, plugin_id, enable_cmd_template,
            installed_plugins, dry_run,
        )
    elif dry_run:
        result.plugins_installed.append(f'{plugin_id} (dry-run)')
    else:
        cmd = install_cmd_template.format(plugin=plugin_id, scope=scope)
        code, stdout, stderr = detect.run_command(cmd, timeout=60)
        if code != 0:
            result.plugins_failed.append(f'{plugin_id}: {stderr[:100]}')
            return
        # Install succeeded. Attempt to enable in case it installed as
        # disabled; surface enable failures rather than silently dropping
        # them (a plugin that is installed but disabled is non-functional).
        if enable_cmd_template:
            enable_cmd = enable_cmd_template.format(plugin=plugin_id)
            ec, _, e_stderr = detect.run_command(enable_cmd, timeout=15)
            if ec != 0:
                result.plugins_failed.append(
                    f'{plugin_id}: installed but enable failed: {e_stderr[:100]}',
                )
                return
        result.plugins_installed.append(plugin_id)


def _check_and_enable_plugin(
    result: ClientResult,
    plugin_id: str,
    enable_cmd_template: str,
    installed_plugins: dict[str, str],
    dry_run: bool,
) -> None:
    """Check if a plugin is disabled and enable it if needed."""
    status = installed_plugins.get(plugin_id, 'unknown')
    is_disabled = status == 'disabled'

    if is_disabled and enable_cmd_template:
        if dry_run:
            result.plugins_installed.append(f'{plugin_id} (enable, dry-run)')
        else:
            cmd = enable_cmd_template.format(plugin=plugin_id)
            code, _, stderr = detect.run_command(cmd, timeout=15)
            if code == 0:
                result.plugins_installed.append(f'{plugin_id} (enabled)')
            else:
                result.plugins_failed.append(
                    f'{plugin_id}: enable failed: {stderr[:100]}')
    else:
        result.plugins_skipped.append(plugin_id)


def _write_rules(
    result: ClientResult,
    rules_config: dict,
    base_dir: Path,
    project_dir: Path,
    dry_run: bool,
) -> None:
    """Write rules file to the target directory.

    If the rules path starts with ~ it is treated as a global (home-relative)
    path.  Otherwise it is relative to project_dir.
    """
    rules_path_str = rules_config.get('path', '')
    source_rel = rules_config.get('source', '')
    if not rules_path_str or not source_rel:
        return

    source_path = base_dir / source_rel
    if not source_path.exists():
        result.error = f'rules source not found: {source_path}'
        return

    content = source_path.read_text(encoding='utf-8')
    target_dir = Path(detect.expand_env_path(rules_path_str))
    if not target_dir.is_absolute():
        target_dir = project_dir / target_dir
    target_file = target_dir / source_path.name

    if dry_run:
        result.rules_written = True
        return

    written = rules.write_managed_file(target_file, content)
    if written:
        result.rules_written = True
    else:
        result.rules_skipped = True
