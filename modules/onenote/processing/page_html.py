"""Render a `Payload` to the multipart body the OneNote API takes.

Pure: no network, no clock. The caller passes the date, so the same payload
renders the same bytes in a test.

The OneNote API accepts HTML plus binary parts. An image or attachment in the
HTML points at a part by name (`name:<part>`), and each part is sent beside the
HTML in the same multipart request. The same parts serve both a new page (POST
to a section, HTML in a part called "Presentation") and an append (PATCH to a
page's content, a JSON command list in a part called "Commands").
"""

import html
import json
from dataclasses import dataclass

from modules.onenote.processing.artifacts import Payload


@dataclass(frozen=True)
class Part:
    """One binary part of the multipart body."""

    name: str
    data: bytes
    content_type: str


def section_html(payload: Payload, date: str, material_note: str = "") -> tuple[str, tuple[Part, ...]]:
    """One dated section — a heading, notes, images, tables, attachments.

    Returns the HTML fragment and the binary parts it refers to. Part names
    are prefixed with the artifact kind so two sections in one request could
    never collide.
    """
    prefix = payload.kind.lower().replace(" ", "")
    parts: list[Part] = []
    out = [f"<h2>{html.escape(payload.kind)} — {html.escape(date)}</h2>"]

    if material_note:
        out.append(f"<p>{html.escape(material_note)}</p>")
    for note in payload.notes:
        out.append(f"<p>{html.escape(note)}</p>")

    for i, image in enumerate(payload.images):
        name = f"{prefix}img{i}"
        parts.append(Part(name, image.data, "image/png"))
        out.append(f"<p><b>{html.escape(image.caption)}</b></p>")
        out.append(f'<img src="name:{name}" alt="{html.escape(image.caption)}" />')

    for caption, frame in payload.tables:
        out.append(f"<p><b>{html.escape(caption)}</b></p>")
        out.append(frame.to_html(index=False, border=1))

    for i, attachment in enumerate(payload.attachments):
        name = f"{prefix}att{i}"
        parts.append(Part(name, attachment.data, attachment.content_type))
        out.append(
            f'<object data-attachment="{html.escape(attachment.filename)}" '
            f'data="name:{name}" type="{attachment.content_type}" />'
        )

    return "<div>" + "".join(out) + "</div>", tuple(parts)


def new_page_html(title: str, body: str) -> str:
    """A whole page: the wafer ID as its title, then the first section."""
    return (
        "<!DOCTYPE html><html><head>"
        f"<title>{html.escape(title)}</title>"
        f"</head><body>{body}</body></html>"
    )


def append_commands(body: str) -> str:
    """The PATCH command list that appends one section to the page body."""
    return json.dumps([{"target": "body", "action": "append", "content": body}])
