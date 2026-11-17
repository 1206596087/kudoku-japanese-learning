#!/usr/bin/env python3
"""Download upstream dictionary sources into the ignored local cache."""

from __future__ import annotations

import shutil
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
CACHE = ROOT / ".cache"
SOURCES = {
    "JMdict.gz": "https://www.edrdg.org/pub/Nihongo/JMdict.gz",
    "kanjidic2.xml.gz": "https://www.edrdg.org/pub/Nihongo/kanjidic2.xml.gz",
    "JmdictFurigana.json.zip": "https://github.com/Doublevil/JmdictFurigana/releases/download/2.3.1%2B2026-09-25/JmdictFurigana.json.zip",
}


def main() -> None:
    CACHE.mkdir(exist_ok=True)
    for name, url in SOURCES.items():
        destination = CACHE / name
        temporary = destination.with_suffix(destination.suffix + ".part")
        print(f"Downloading {name} …")
        request = Request(url, headers={"User-Agent": "SentenceDriveJP/1.0"})
        with urlopen(request, timeout=120) as response, temporary.open("wb") as output:
            shutil.copyfileobj(response, output)
        if temporary.stat().st_size < 100_000:
            temporary.unlink(missing_ok=True)
            raise RuntimeError(f"Downloaded file is unexpectedly small: {name}")
        temporary.replace(destination)
        print(f"  {destination.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
