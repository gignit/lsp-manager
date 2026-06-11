# lsp-manager — Agent Notes

This repo installs LSP server binaries and wires them into Claude Code automatically.

## Key Entry Points

- `lsp-manager` — main CLI script (Python, shebang executable)
- `lib/engine.py` — core logic: server install, client integration, plugin management
- `lib/claude_plugin.py` — Claude Code marketplace and plugin management
- `lib/listing.py` — data helpers for list/status output (separate fetchers per data source)
- `lib/detect.py` — binary detection, path resolution, command execution
- `servers/*.yaml` — one file per LSP server definition (filename stem = plugin id)
- `clients/claude.yaml` — Claude Code client definition

## Commands

```bash
lsp-manager init [--dry-run] [-y|--yes] [--no-sudo] \
                 [--all | --pack NAME | --plugins id1,id2,...]
lsp-manager list [--enabled | --disabled | --failed]
lsp-manager status
lsp-manager doctor [--fix]
```

- `init` -- installs missing binaries then configures Claude Code plugins and the navigation rule. With no selection flag, installs the `standard` pack (see `packs.yaml`).
- `list` -- unified view of every known plugin: server definitions, installed plugins, and marketplace availability merged into a single output. Filters by enabled/disabled/failed state.
- `status` -- quick binary availability check for current project
- `doctor` -- health check: binaries, plugins, and rules. With `--fix`, surgically cleans up artifacts created by older lsp-manager versions (legacy local plugins superseded by upstream ones, old-generation files, pre-release hooks). Never deletes anything it cannot prove lsp-manager created.

## How It Works

1. `lsp-manager init` installs missing server binaries then configures Claude Code
2. Servers with an official Claude marketplace plugin use it directly
3. Servers whose binary is not in Claude's PATH get a local plugin written to
   `~/.local/share/lsp-manager/claude-marketplace/` with an absolute-path command
4. The local marketplace is registered in `~/.claude/settings.json` under `extraKnownMarketplaces`
5. Plugins are enabled in `~/.claude/settings.json` under `enabledPlugins`
6. Navigation rule is written to `~/.claude/rules/lsp-navigation.md`

## Server Definition Format

```yaml
id: gopls-lsp          # must match filename stem (servers/gopls-lsp.yaml)
binary: gopls
clients:
  claude:
    marketplace: claude-plugins-official  # defaults to claude-plugins-official
    plugin: gopls-lsp                     # defaults to id
    scope: user
```

For servers with no official plugin, use `lsp_config` instead:

```yaml
clients:
  claude:
    lsp_config:
      command: my-lang-server
      args: ["--stdio"]
      extensionToLanguage:
        .ext: language
```

## Adding a New Server

Copy `examples/servers/.example-server.yaml` to `servers/<plugin-name>.yaml`, set `id` to match the filename stem, then run `make install && lsp-manager init`.

## Data Model (lib/listing.py)

`cmd_list` uses three independent data fetchers that can be consumed separately:

- `get_server_entries(server_defs)` -- binary availability + version for servers in `servers/`
- `get_installed_plugins()` -- parses `claude plugin list` output into `InstalledPluginEntry` records (qualified_id, status, error)
- `get_all_marketplace_lsp_plugins()` -- reads `lspServers` entries from all known marketplaces
- `build_plugin_views(server_entries, installed, marketplace)` -- joins the above into `PluginView` records with `overall_status` (enabled/disabled) and `health` (ok/failed/ready/not installed/missing)

`lib/engine.py` also consumes `get_installed_plugins()` during `init` as a structured dict of `{qualified_id: status}` to check whether each plugin needs to be installed or enabled, instead of substring-matching the raw CLI output.
