# Client Definition Schema

Client definitions describe an AI coding tool and how to configure LSP
integration for it.

## Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string | yes | Unique identifier for this client |
| `name` | string | yes | Human-readable display name |
| `enabled` | boolean | no | Whether this client is active (default: true) |
| `binary` | string | yes | CLI binary name to detect in PATH |
| `detect_command` | string | no | Command to verify the client works |
| `plugins` | object | no | Plugin management configuration |
| `plugins.install_command` | string | yes | Command template to install a plugin (`{plugin}` and `{scope}` are substituted) |
| `plugins.list_command` | string | yes | Command to list installed plugins |
| `plugins.default_scope` | string | no | Default scope for plugin installs (default: "user") |
| `rules` | object | no | Rules file configuration |
| `rules.path` | string | yes | Directory path for rules files (relative to project root) |
| `rules.source` | string | yes | Source file path (relative to lsp-manager base dir) |

## Plugin Command Templates

The `install_command` supports these placeholders:
- `{plugin}`: replaced with the plugin ID from the server definition
- `{scope}`: replaced with the scope from the server definition or the default
