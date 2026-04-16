# lsp-manager

Installs LSP server binaries and wires them into Claude Code automatically -- with a single command.

```bash
lsp-manager init
```

MIT-licensed. Tested on Linux. macOS support is implemented but not yet
widely tested (see [Platforms](#platforms)).

## Prerequisites

| Requirement | Why | Install |
|-------------|-----|---------|
| Python 3.10+ | Runtime for lsp-manager itself | macOS: `brew install python`. Linux: your package manager (already bundled on most distros). |
| PyYAML | Parses server/client definitions | `python3 -m pip install --user pyyaml` or `apt install python3-yaml` |
| Claude Code CLI (`claude`) | lsp-manager registers marketplaces and plugins through it | `npm install -g @anthropic-ai/claude-code` (official installer also available from Anthropic) |

Per-server prerequisites (Go, Node, Ruby, .NET, Homebrew, etc.) are declared
inline in each `servers/*.yaml` and checked before install.

## Install

```bash
git clone https://github.com/gignit/lsp-manager.git
cd lsp-manager
make install
```

`make install` copies the tool to `~/.local/share/lsp-manager/` and drops a
symlink at `~/.local/bin/lsp-manager`. If `~/.local/bin` is not already on
your `PATH`, the installer prints the snippet to add:

```bash
# bash / zsh
export PATH="$HOME/.local/bin:$PATH"

# fish
fish_add_path $HOME/.local/bin
```

Uninstall with `make uninstall`.

## Usage

```bash
# Install the standard pack and configure Claude Code (default)
lsp-manager init

# Install every server defined under servers/
lsp-manager init --all

# Install a named pack
lsp-manager init --pack standard

# Install specific plugins by id (comma-separated)
lsp-manager init --plugins gopls-lsp,pyright-lsp

# Preview what init would do without making changes
lsp-manager init --dry-run

# Show all known servers and install status
lsp-manager list

# Show status for current project
lsp-manager status

# Health check: verify binaries, plugins, and rules
lsp-manager doctor
```

`lsp-manager init` with no selection flag installs the `standard` pack. Pass
`--all`, `--pack NAME`, or `--plugins id1,id2` to override.

## The `standard` Pack

These servers are installed by `lsp-manager init` with no arguments.

| Server | Language | Plugin |
|--------|----------|--------|
| gopls | Go | `gopls-lsp@claude-plugins-official` |
| typescript-language-server | TypeScript/JavaScript | `typescript-lsp@claude-plugins-official` |
| pyright | Python | `pyright-lsp@claude-plugins-official` |
| rust-analyzer | Rust | `rust-analyzer-lsp@claude-plugins-official` |
| clangd | C/C++ | `clangd-lsp@claude-plugins-official` |
| tailwindcss-language-server | CSS/HTML/JSX | `tailwindcss-lsp@lsp-manager` (local) |

Pack contents are defined in `packs.yaml`. Additional server definitions ship
in `servers/` (C#, Java, Kotlin, Lua, PHP, Ruby, Swift) and can be installed
via `--all` or `--plugins`.

## How It Works

lsp-manager manages three things:

1. **Server binaries** -- Installs language server binaries to user-local paths. No root required for most servers.

2. **Client plugins** -- Enables LSP plugins in Claude Code. For servers whose binary lands outside Claude's PATH (e.g. `~/go/bin`), lsp-manager generates a local plugin with an absolute-path command so they work without any PATH configuration.

3. **Navigation rule** -- Writes `~/.claude/rules/lsp-navigation.md`, a short rule reminding Claude to check LSP diagnostics after edits.

## Platforms

| Platform | Status |
|----------|--------|
| Linux | Tested on Ubuntu. Most servers use `apt-get` on Linux; distros without `apt` will need to install server prereqs manually before running `lsp-manager init`. |
| macOS (Intel + Apple Silicon) | Supported. Install commands use Homebrew. If Homebrew is missing, install it first: <https://brew.sh>. Paths that differ between Intel (`/usr/local`) and Apple Silicon (`/opt/homebrew`) are resolved at runtime via `$(brew --prefix ...)`. Not yet tested as widely as Linux -- please open an issue if something breaks. |
| Windows | Not supported. |

## Security

- **Server YAML files in `servers/` run shell commands during install.** The bundled definitions in this repo are reviewed, but anything you drop into `servers/` will be executed by your user. Only add server definitions from sources you trust.
- **`rust-analyzer-lsp` runs the upstream rustup installer** (`curl https://sh.rustup.rs | sh`). If that is not acceptable for your environment, skip `rust-analyzer-lsp` and install rust-analyzer manually before running init.
- **Several servers can require sudo** (the `apt-get` Linux installs). Use `--yes` to pre-approve sudo non-interactively, or `--no-sudo` to skip any server that would need it.
- **Server ids are validated** against `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$` before being joined into filesystem paths, so a malformed `id:` field cannot escape the marketplace directory.

## Plugin Marketplace Selection

Claude Code activates LSP servers by reading `lspServers` entries from registered marketplace plugins. lsp-manager registers a second marketplace alongside the official one when needed.

For each server, the decision:

| Condition | Result |
|-----------|--------|
| Binary in Claude's PATH | Use official marketplace plugin directly |
| Binary not in PATH + `path_resolve_method: hard` (default) | Generate local plugin with absolute path, replace official |
| Binary not in PATH + `path_resolve_method: soft` | Use official plugin + create symlink in `bin_dir` |
| No official plugin exists (e.g. Tailwind CSS) | Always generate local plugin |

### path_resolve_method

Configure in `~/.local/share/lsp-manager/config.yaml`:

```yaml
path_resolve_method: hard   # hard (default) | soft | none
```

### Files Written

| Path | Purpose |
|------|---------|
| `~/.claude/settings.json` | `enabledPlugins` and `extraKnownMarketplaces` entries |
| `~/.claude/rules/lsp-navigation.md` | Rule to check LSP diagnostics after edits |
| `~/.local/share/lsp-manager/claude-marketplace/.claude-plugin/marketplace.json` | Local marketplace manifest with inline `lspServers` |
| `~/.local/share/lsp-manager/claude-marketplace/plugins/<name>/` | Stub directory per local plugin |
| `~/.local/share/lsp-manager/config.yaml` | lsp-manager config (created on first run) |

## Adding Servers

The filename stem must match the `id` field. Copy the example:

```bash
cp examples/servers/.example-server.yaml servers/my-lang-lsp.yaml
```

```yaml
# servers/my-lang-lsp.yaml
id: my-lang-lsp
name: My Language Server
enabled: true
binary: my-lang-server
check_command: "my-lang-server --version"

install:
  default:
    command: "npm install -g my-lang-server"

clients:
  claude:
    marketplace: claude-plugins-official  # defaults to claude-plugins-official
    plugin: my-lang-lsp                   # defaults to id
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
        .mylang: mylanguage
```

## Adding Clients

See `examples/clients/.example-client.yaml` for a template.

## Directory Structure

```
servers/     -- LSP server definitions (filename = plugin id)
clients/     -- AI tool client definitions
rules/       -- Behavioral rule templates
examples/    -- Templates for new definitions
lib/         -- Python library modules
packs.yaml   -- Named plugin packs (used by `init --pack NAME`)
```

## AI Agent Prompts

- **"Show me what LSP plugins are available in the Claude marketplace and which ones are configured"**
- **"Install lsp-manager and run init"**
- **"Install the gopls LSP server"**
- **"Add support for Ruby LSP"**
- **"Why isn't Go LSP working in Claude Code"**
