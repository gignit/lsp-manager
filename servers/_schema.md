# Server Definition Schema

Server definitions describe an LSP server binary: how to install it,
how to verify it, and how to configure it for each AI coding client.

## Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string | yes | Unique identifier for this server |
| `name` | string | yes | Human-readable display name |
| `enabled` | boolean | no | Whether this server is active (default: true) |
| `binary` | string | yes | Name of the executable to check in PATH |
| `check_command` | string | no | Command to verify the binary works (e.g., version check) |
| `install` | object | yes | Installation configuration |
| `install.default` | object | yes | Default install config (any OS) |
| `install.darwin` | object | no | macOS-specific overrides |
| `install.linux` | object | no | Linux-specific overrides |
| `install.*.requires` | string | no | Prerequisite binary (e.g., "go", "node") |
| `install.*.command` | string | no | Shell command to install the binary |
| `install.*.env_path` | string | no | Directory to add to PATH after install |
| `install.*.needs_sudo` | boolean | no | If true, try sudo if local install fails |
| `clients` | object | no | Per-client configuration |
| `clients.<id>.plugin` | string | no | Official marketplace plugin ID |
| `clients.<id>.scope` | string | no | Plugin install scope (default: "user") |
| `clients.<id>.lsp_config` | object | no | Direct LSP config for clients without official plugins |

## Install Resolution

The engine merges `install.default` with `install.<current_os>`. OS-specific
values override defaults. If `command` is null after merging, the server
cannot be auto-installed on the current OS.

## Client Integration

Each server can define integration for multiple clients under `clients`.
The client ID must match a file in the `clients/` directory (without extension).

- If `plugin` is set: the engine runs the client's plugin install command
- If `lsp_config` is set: the engine injects the config directly into the client's settings file
- Both can be set if a client needs both a plugin and custom config
