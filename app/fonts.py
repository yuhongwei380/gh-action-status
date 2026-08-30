from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.error import URLError
from urllib.request import Request, urlopen


OFFICIAL_FONT_ROOT = "https://fonts.alibabadesign.com/AlibabaPuHuiTi-3"
DOWNLOAD_HEADERS = {
    "Referer": "https://www.alibabafonts.com/",
    "User-Agent": "Runner-Beacon-Font-Bootstrap/1.0",
}


@dataclass(frozen=True)
class FontSource:
    filename: str
    sha256: str
    size: int

    @property
    def url(self) -> str:
        family = self.filename.removesuffix(".woff2")
        return f"{OFFICIAL_FONT_ROOT}/{family}/{self.filename}"


FONT_SOURCES = {
    source.filename: source
    for source in (
        FontSource(
            "AlibabaPuHuiTi-3-55-Regular.woff2",
            "1cb8418d80b01ec08cb6f2d64b6244aaaf1bb80dc35491b66ddba16c5e24f444",
            5_256_740,
        ),
        FontSource(
            "AlibabaPuHuiTi-3-65-Medium.woff2",
            "628a0d5be684bae0e7a8e3ad15b7c4f623fd15b3375082624f778326e343467a",
            5_469_328,
        ),
        FontSource(
            "AlibabaPuHuiTi-3-75-SemiBold.woff2",
            "75e2ba042e1c8ad8cf3dd5bf26f25984ffeca6c4ebb86977c8f8e1abeeb2652f",
            5_481_108,
        ),
        FontSource(
            "AlibabaPuHuiTi-3-85-Bold.woff2",
            "5947ecd5447c34865d036740f38afbf4364b3cf7bb3d399aba0f169b59c6ec35",
            5_560_840,
        ),
    )
}


class FontDownloadError(RuntimeError):
    pass


class FontStore:
    def __init__(
        self,
        cache_dir: Path,
        bundled_dir: Path | None = None,
        sources: dict[str, FontSource] | None = None,
    ) -> None:
        self.cache_dir = cache_dir
        self.bundled_dir = bundled_dir
        self.sources = sources if sources is not None else FONT_SOURCES
        self._locks = {filename: asyncio.Lock() for filename in self.sources}

    async def ensure(self, filename: str) -> Path:
        source = self.sources.get(filename)
        if source is None:
            raise KeyError(filename)

        cached = self.cache_dir / filename
        bundled = self.bundled_dir / filename if self.bundled_dir else None
        for candidate in (cached, bundled):
            if candidate is not None and self._is_valid(candidate, source):
                return candidate

        async with self._locks[filename]:
            for candidate in (cached, bundled):
                if candidate is not None and self._is_valid(candidate, source):
                    return candidate
            return await asyncio.to_thread(self._download, source, cached)

    @staticmethod
    def _is_valid(path: Path, source: FontSource) -> bool:
        try:
            if not path.is_file() or path.stat().st_size != source.size:
                return False
            digest = hashlib.sha256()
            with path.open("rb") as font_file:
                while chunk := font_file.read(1024 * 1024):
                    digest.update(chunk)
            return digest.hexdigest() == source.sha256
        except OSError:
            return False

    def _download(self, source: FontSource, target: Path) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        digest = hashlib.sha256()
        total = 0

        try:
            request = Request(source.url, headers=DOWNLOAD_HEADERS)
            with urlopen(request, timeout=60) as response, NamedTemporaryFile(
                mode="wb", dir=target.parent, prefix=f".{source.filename}.", suffix=".tmp", delete=False
            ) as temporary:
                temp_path = Path(temporary.name)
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > source.size:
                        raise FontDownloadError(f"Font download exceeded expected size: {source.filename}")
                    digest.update(chunk)
                    temporary.write(chunk)

            if total != source.size or digest.hexdigest() != source.sha256:
                raise FontDownloadError(f"Font integrity check failed: {source.filename}")
            os.replace(temp_path, target)
            temp_path = None
            return target
        except FontDownloadError:
            raise
        except (OSError, URLError) as exc:
            raise FontDownloadError(f"Unable to download official font: {source.filename}") from exc
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def download_all(self) -> list[Path]:
        downloaded: list[Path] = []
        for source in self.sources.values():
            target = self.cache_dir / source.filename
            if not self._is_valid(target, source):
                self._download(source, target)
            downloaded.append(target)
        return downloaded


def main() -> None:
    parser = argparse.ArgumentParser(description="Download verified Alibaba PuHuiTi webfonts.")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    paths = FontStore(args.output).download_all()
    for path in paths:
        print(path)


if __name__ == "__main__":
    main()
