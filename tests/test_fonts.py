import hashlib

import pytest

from app.fonts import FontSource, FontStore


@pytest.mark.asyncio
async def test_font_store_downloads_and_verifies_font(tmp_path):
    content = b"verified-webfont"
    source = FontSource(
        filename="test.woff2",
        sha256=hashlib.sha256(content).hexdigest(),
        size=len(content),
    )
    source_url = "data:application/octet-stream;base64,dmVyaWZpZWQtd2ViZm9udA=="

    class TestFontSource(FontSource):
        @property
        def url(self) -> str:
            return source_url

    test_source = TestFontSource(source.filename, source.sha256, source.size)
    store = FontStore(tmp_path, sources={test_source.filename: test_source})

    path = await store.ensure(test_source.filename)

    assert path.read_bytes() == content
    assert await store.ensure(test_source.filename) == path


@pytest.mark.asyncio
async def test_font_store_rejects_unknown_font(tmp_path):
    store = FontStore(tmp_path, sources={"known.woff2": FontSource("known.woff2", "0" * 64, 1)})

    with pytest.raises(KeyError):
        await store.ensure("unknown.woff2")
