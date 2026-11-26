import hashlib

from app.web.miniapp import STATIC_ASSETS, STATIC_INDEX, miniapp_html


def test_miniapp_assets_use_content_versions() -> None:
    html = miniapp_html()
    for name in STATIC_ASSETS:
        digest = hashlib.sha256((STATIC_INDEX.parent / name).read_bytes()).hexdigest()[:16]
        assert f'/static/{name}?v={digest}"' in html
