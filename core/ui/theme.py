"""
The app's shared look: the Nexstrom logo, the CSS the theme options can't
express, and the page header every page opens with (v5.44.0; Nexstrom's
colours and logo since v5.45.0).

Colours, fonts and radii live in `.streamlit/config.toml`. A few things don't
fit there. Streamlit draws a bordered container transparent, so on the grey
page background the cards would be grey too; `apply_theme()` paints them
white. Headings take Nexstrom's heading navy, which no theme option sets. The
logo is `st.logo`, which has to be called on every run. And the header — an
uppercase eyebrow naming the nav section and page, the title, one line saying
what the page is for — is the same three lines on every page, so it is
written once, in `page_header()`.

Called from app.py, which runs before every page, so no page needs to apply
the theme itself.
"""

import streamlit as st

from core.paths import PROJECT_ROOT

#: Nexstrom's palette (nexstrom.com), for the few places that colour inline HTML.
INK = "#0B1220"
MUTED = "#344B68"
ACCENT = "#2F72BF"
NAVY = "#073A6C"
BORDER = "#D3DFEC"
TINT = "#EAF2F8"

#: The logo in white, for the navy sidebar; the mark alone is shown when the
#: sidebar is collapsed. assets/nexstrom_logo.png is the original navy one.
_ASSETS = PROJECT_ROOT / "assets"
LOGO = _ASSETS / "nexstrom_logo_white.png"
LOGO_MARK = _ASSETS / "nexstrom_mark_white.png"

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

/* Headings in Nexstrom's heading navy; the sidebar keeps its own text colour. */
[data-testid="stMain"] h1, [data-testid="stMain"] h2,
[data-testid="stMain"] h3, [data-testid="stMain"] h4 {{ color: {NAVY}; }}

/* Captions: Streamlit fades them to ~60% of the text colour, which on this
   background read as too faint (v5.45.0). Solid slate instead, a contrast
   of about 8:1 on white. */
[data-testid="stMain"] [data-testid="stCaptionContainer"] {{ color: {MUTED}; opacity: 1; }}
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] {{ color: #C9D6E6; opacity: 1; }}

/* Sidebar navigation: section labels uppercase and muted, page names and
   the footer 2px above Streamlit's 13px so the nav reads at a glance. */
[data-testid="stNavSectionHeader"] {{
    font-size: 14px; text-transform: uppercase; letter-spacing: 0.08em;
    color: #C9D6E6;
}}
[data-testid="stSidebarNavLink"] {{ min-height: 42px; }}
[data-testid="stSidebarNavLink"] p {{ font-size: 15px; }}
[data-testid="stSidebarNavLink"] [data-testid="stIconMaterial"] {{ font-size: 18px; }}
[data-testid="stSidebar"] [data-testid="stCaptionContainer"],
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p {{ font-size: 15px; }}

/* The eyebrow above each page title. */
.nx-eyebrow {{
    font-size: 13px; color: {MUTED}; letter-spacing: 0.04em;
    text-transform: uppercase; margin-bottom: -0.5rem;
}}
.nx-blurb {{ color: {MUTED}; margin-top: -0.5rem; }}
</style>
"""


def apply_theme() -> None:
    """Show the logo and inject the shared CSS. Call once per run, before the
    page runs."""
    st.logo(str(LOGO), size="large", icon_image=str(LOGO_MARK))
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
