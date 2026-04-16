# Rules Definition Schema

Rules files are markdown documents placed in a client's rules directory
(e.g., `.claude/rules/`) to provide behavioral instructions to the AI.

## Format

Rules files are plain markdown. lsp-manager adds a checksum comment at
the top for change detection:

```markdown
<!-- checksum: abc123def456 -->
# Rule Title

- Rule 1
- Rule 2
```

## Managed Updates

When `lsp-manager init` runs, it compares the checksum of the desired
content against the checksum in the existing file. If they match, the
file is left untouched. If they differ (or the file doesn't exist),
the file is rewritten with the updated content and new checksum.
