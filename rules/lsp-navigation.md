# LSP Integration

- After editing files, check LSP diagnostics and fix type errors and missing imports immediately
- For precise symbol lookups (definition, references, hover), use the LSP tool
- For broad codebase exploration (architecture, data flow, module overview), use coder MCP tools if available
- Fall back to grep/Read only when both coder and LSP cannot answer
- Do not proceed to the next task if LSP diagnostics show errors in files you modified
