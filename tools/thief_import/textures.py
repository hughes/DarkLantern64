"""Read terrain images from a user's installed Thief CRF archives.

CRFs are ZIP archives. Only requested images are decoded, never executable
content; archive paths are identifiers and are never extracted to disk.
"""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

from PIL import Image


class TextureArchive:
    def __init__(self, install):
        self.install = Path(install)
        self.archive = zipfile.ZipFile(self.install / "RES/fam.crf")
        self.names = {name.replace("\\", "/").casefold(): name
                      for name in self.archive.namelist()}

    def close(self):
        self.archive.close()

    def read(self, family, name):
        stem = f"{family}/{name}".strip("/").replace("\\", "/").casefold()
        if stem.startswith("fam/"):
            stem = stem[4:]
        entry = next((self.names[key] for key in
                      (stem, stem + ".pcx", stem + ".gif", stem + ".png", stem + ".tga")
                      if key in self.names), None)
        if entry is None:
            raise ValueError(f"Missing Thief texture {family}/{name} in fam.crf")
        info = self.archive.getinfo(entry)
        if info.file_size > 16 * 1024 * 1024:
            raise ValueError(f"Oversized Thief texture {entry}")
        with Image.open(io.BytesIO(self.archive.read(entry))) as source:
            source.load()
            image = source.convert("RGB")
        return image, entry


def texture_id(index, family, name):
    suffix = re.sub(r"[^a-z0-9_-]", "-", f"{family}-{name}".lower()).strip("-")
    return f"mat-thief-{index:03d}-{suffix}"[:64]
