"""Checksummed managed file writer for rules and configuration files."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import Optional


_CHECKSUM_PATTERN = re.compile(r'<!--\s*checksum:\s*([a-f0-9]+)\s*-->')


def compute_checksum(content: str) -> str:
    """Compute MD5 checksum of content for change detection."""
    return hashlib.md5(content.encode('utf-8')).hexdigest()[:16]


def read_existing_checksum(file_path: Path) -> Optional[str]:
    """Read the checksum from an existing managed file, or None."""
    if not file_path.exists():
        return None
    try:
        text = file_path.read_text(encoding='utf-8')
        match = _CHECKSUM_PATTERN.search(text)
        return match.group(1) if match else None
    except OSError:
        return None


def write_managed_file(
    file_path: Path,
    content: str,
    force: bool = False,
) -> bool:
    """Write content to a file with checksum, only if changed.

    Prepends a checksum comment to the content. If the file already
    exists with the same checksum, the write is skipped.

    Args:
        file_path: Target file path.
        content: Content to write (without checksum header).
        force: If True, write even if checksums match.

    Returns:
        True if the file was written, False if skipped (already up to date).
    """
    checksum = compute_checksum(content)

    if not force:
        existing = read_existing_checksum(file_path)
        if existing == checksum:
            return False

    file_path.parent.mkdir(parents=True, exist_ok=True)

    managed_content = f'<!-- checksum: {checksum} -->\n{content}'
    fd, tmp_path = tempfile.mkstemp(
        prefix=f'.{file_path.name}.', suffix='.tmp', dir=str(file_path.parent),
    )
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(managed_content)
        os.replace(tmp_path, file_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return True
