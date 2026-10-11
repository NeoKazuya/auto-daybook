"""The served UI loads against the real app: token, assets, module graph, events."""

import re
from pathlib import Path

from fastapi.testclient import TestClient

from daybook.web import create_app, wire

STATIC = Path(__file__).parents[2] / "src/daybook/web/static"


def client() -> TestClient:
    return TestClient(create_app(mock=True, token="t0k", origin="http://testserver"))


def test_index_carries_exactly_one_token():
    # The UI reads the first daybook-token meta; a second, empty one would hide the
    # real token and leave the page in demo mode.
    with client() as c:
        page = c.get("/").text
    tags = re.findall(r'<meta name="daybook-token"[^>]*>', page)
    assert tags == ['<meta name="daybook-token" content="t0k">']


def test_index_assets_are_served():
    with client() as c:
        page = c.get("/").text
        refs = re.findall(r'(?:href|src)="(/static/[^"]+)"', page)
        assert refs, "index.html should load its CSS and JS from /static/"
        for ref in refs:
            assert c.get(ref).status_code == 200, ref


def test_ui_loads_nothing_from_other_servers():
    # Daybook promises everything runs on this machine, offline included.
    css = (STATIC / "css/daybook.css").read_text()
    with client() as c:
        page = c.get("/").text
        assert not re.search(r"(?:href|src)=\"https?://", page)
        assert "http" not in "".join(re.findall(r"url\(([^)]*)\)", css))
        for ref in re.findall(r'url\("\.\./(fonts/[^"]+)"\)', css):
            assert c.get(f"/static/{ref}").status_code == 200, ref


def test_every_module_import_resolves():
    imports = re.compile(r"""(?:from\s+|import\(\s*)['"](\.{1,2}/[^'"]+)['"]""")
    for module in (STATIC / "js").rglob("*.js"):
        for spec in imports.findall(module.read_text()):
            assert (module.parent / spec).resolve().is_file(), f"{module.name}: {spec}"


def test_ui_listens_for_every_wire_event():
    api = (STATIC / "js/api.js").read_text()
    listed = re.search(r"const EVENTS = \[(.*?)\];", api, re.S)
    assert listed
    names = set(re.findall(r"'(\w+)'", listed.group(1)))
    assert names == set(wire.EVENT_MODELS)
