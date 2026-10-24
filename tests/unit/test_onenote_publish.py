"""OneNote publishing: the HTML a payload becomes, and the Graph calls made.

No network: Graph is a fake session that records requests and answers from a
script, so these pin *which* calls are made and in what order.
"""

import json

import pandas as pd
import pytest

from modules.onenote.io import graph_client
from modules.onenote.processing import artifacts, page_html


def _payload():
    return artifacts.Payload(
        kind=artifacts.QC_REPORT,
        wafer_id="HADH51",
        images=(artifacts.Image("Summary", b"png0"), artifacts.Image("OM <raw>", b"png1")),
        attachments=(artifacts.Attachment("HADH51_QC_Report.xlsx", b"xlsx", "application/x"),),
        tables=(("Violations", pd.DataFrame({"a": [1]})),),
        notes=("Recipe: R1",),
    )


def test_section_html_refers_to_every_part_it_returns():
    body, parts = page_html.section_html(_payload(), "2026-10-13 15:00", "Material OK")

    assert [p.name for p in parts] == ["qcreportimg0", "qcreportimg1", "qcreportatt0"]
    for part in parts:
        assert f'"name:{part.name}"' in body
    assert "QC Report — 2026-10-13 15:00" in body
    assert "Material OK" in body and "Recipe: R1" in body
    assert "OM &lt;raw&gt;" in body          # captions are escaped
    assert 'data-attachment="HADH51_QC_Report.xlsx"' in body
    assert "<table" in body


def test_new_page_is_titled_with_the_wafer():
    document = page_html.new_page_html("HADH51", "<div>x</div>")
    assert "<title>HADH51</title>" in document and "<div>x</div>" in document


def test_append_is_one_body_append_command():
    assert json.loads(page_html.append_commands("<div>x</div>")) == [
        {"target": "body", "action": "append", "content": "<div>x</div>"}
    ]


def test_config_is_unconfigured_until_ids_are_filled(tmp_path):
    path = tmp_path / "onenote.json"
    assert not graph_client.load_config(path).configured

    path.write_text(json.dumps({"client_id": "c", "tenant_id": "t", "unknown": 1}))
    config = graph_client.load_config(path)
    assert config.configured and config.notebook == "Wafer QC"


def test_committed_config_carries_no_stray_keys():
    raw = json.loads(graph_client.CONFIG_PATH.read_text(encoding="utf-8"))
    assert set(raw) <= set(graph_client.OneNoteConfig.__dataclass_fields__)


class _Response:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body or {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


class _Session:
    """Answers GETs from `listings` by URL prefix; POST/PATCH return an id."""

    def __init__(self, listings):
        self.headers = {}
        self.listings = listings
        self.calls = []

    def request(self, method, url, timeout=None, **kwargs):
        self.calls.append((method, url.split("?")[0], kwargs))
        if method == "GET":
            for prefix, value in self.listings.items():
                if url.split("?")[0].endswith(prefix):
                    return _Response(body={"value": value})
            return _Response(404, {"error": {"message": "nope"}})
        return _Response(201, {"id": "new"})


def test_first_upload_creates_notebook_section_and_page():
    session = _Session({"/notebooks": [], "/sections": [], "/pages": []})
    client = graph_client.OneNoteClient("tok", session=session)

    outcome = graph_client.publish_section(client, "Wafer QC", "HA1P01", "HADH51", "<div/>", ())

    assert outcome == "created"
    assert session.headers["Authorization"] == "Bearer tok"
    posts = [(m, u.rsplit("/", 1)[-1]) for m, u, _ in session.calls if m == "POST"]
    assert posts == [("POST", "notebooks"), ("POST", "sections"), ("POST", "pages")]
    files = session.calls[-1][2]["files"]
    assert b"<title>HADH51</title>" in files["Presentation"][1]


def test_later_upload_appends_to_the_existing_page():
    session = _Session({
        "/notebooks": [{"id": "nb", "displayName": "Wafer QC"}],
        "/nb/sections": [{"id": "sec", "displayName": "HA1P01"}],
        "/sec/pages": [{"id": "pg0", "title": "HADH50"}, {"id": "pg1", "title": "HADH51"}],
    })
    client = graph_client.OneNoteClient("tok", session=session)
    part = page_html.Part("p0", b"png", "image/png")

    outcome = graph_client.publish_section(client, "Wafer QC", "HA1P01", "HADH51", "<div/>", (part,))

    assert outcome == "appended"
    method, url, kwargs = session.calls[-1]
    assert (method, url.endswith("/pages/pg1/content")) == ("PATCH", True)
    assert set(kwargs["files"]) == {"Commands", "p0"}
    assert not [c for c in session.calls if c[0] == "POST"]


def test_graph_errors_carry_graph_s_message():
    session = _Session({})
    client = graph_client.OneNoteClient("tok", session=session)
    try:
        client.notebook_id("Wafer QC")
    except graph_client.GraphError as e:
        assert "404" in str(e) and "nope" in str(e)
    else:
        raise AssertionError("expected GraphError")


@pytest.mark.parametrize("content", ["[]", '"text"', "42", "null"])
def test_config_that_is_valid_json_but_not_an_object_is_unconfigured(tmp_path, content):
    path = tmp_path / "onenote.json"
    path.write_text(content, encoding="utf-8")
    assert not graph_client.load_config(path).configured
