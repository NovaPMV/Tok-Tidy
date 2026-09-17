"""Make Hugging Face downloads work on every Windows PC.

huggingface_hub stores downloads as symbolic links when it thinks Windows
allows them. On some PCs its check passes but creating the real link then
fails with "WinError 1314: A required privilege is not held by the client",
which aborts the download after the whole file has arrived.

TokTidy keeps its models in its own folder, so symlinks bring no benefit:
we switch them off and always *move* the finished download into place
(no extra disk space, and a download interrupted this way is recovered
on the next attempt without downloading again).
"""
from __future__ import annotations

import os

_applied = False


def disable_symlinks(force: bool = False) -> None:
    global _applied
    if _applied or (os.name != "nt" and not force):
        return
    try:
        from huggingface_hub import file_download as fd
    except Exception:
        return
    original = getattr(fd, "_create_symlink", None)
    if original is None:
        return

    def no_symlinks(*_args, **_kwargs) -> bool:
        return False

    def move_into_place(src, dst, new_blob=False):
        # Always move: the blob is only needed at its final location.
        return original(src, dst, new_blob=True)

    fd.are_symlinks_supported = no_symlinks
    fd._create_symlink = move_into_place
    _applied = True
