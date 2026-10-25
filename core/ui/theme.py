"""
The app's shared look: the CSS the theme options can't express, and the page
header every page opens with (v5.44.0).

Colours, fonts and radii live in `.streamlit/config.toml`. Two things don't
fit there. Streamlit draws a bordered container transparent, so on the grey
page background the cards would be grey too; `apply_theme()` paints them
white. And the header — an uppercase eyebrow naming the nav section and page,
the title, one line saying what the page is for — is the same three lines on
every page, so it is written once, in `page_header()`.

Called from app.py, which runs before every page, so no page needs to apply
the theme itself.
"""

import streamlit as st

#: The design's palette, for the few places that colour inline HTML.
INK = "#15181C"
MUTED = "#5A616B"
ACCENT = "#1D5FB8"
BORDER = "#DDE1E6"

_CSS = f"""
<style>
/* Cards (see card()): white on the grey page. Streamlit 1.64 puts nothing
   on a bordered container that CSS could select by, so cards are found by
   the key prefix card() gives them. */
[class*="st-key-nxcard-"] {{
    background: #FFFFFF; border-radius: 10px;
}}
/* Expanders read as cards too. */
[data-testid="stExpander"] details {{ background: #FFFFFF; border-radius: 10px; }}

/* Sidebar navigation: section labels small, uppercase, muted. */
[data-testid="stNavSectionHeader"] {{
    font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em;
    color: #A9AFB8;
}}
[data-testid="stSidebarNavLink"] {{ min-height: 40px; }}

/* The eyebrow above each page title. */
.nx-eyebrow {{
    font-size: 12px; color: {MUTED}; letter-spacing: 0.04em;
    text-transform: uppercase; margin-bottom: -0.5rem;
}}
.nx-blurb {{ color: {MUTED}; margin-top: -0.5rem; }}
</style>
"""


def apply_theme() -> None:
    """Inject the shared CSS. Call once per run, before the page runs."""
    st.html(_CSS)


def card(name: str, **kwargs):
    """A bordered container drawn as a white card. `name` must be unique on
    the page; it becomes the widget key `nxcard-<name>`, which the CSS above
    selects. Use it wherever a page would write `st.container(border=True)`."""
    return st.container(border=True, key=f"nxcard-{name}", **kwargs)


def page_header(eyebrow: str, title: str, blurb: str | None = None) -> None:
    """The eyebrow ("ANALYSIS · SPECTRA"), the title, and one line under it."""
    st.html(f'<div class="nx-eyebrow">{eyebrow}</div>')
    st.title(title)
    if blurb:
        st.html(f'<p class="nx-blurb">{blurb}</p>')
