"""
The app's shared look: the sidebar's app name and maker, the CSS the theme
options can't express, and the page header every page opens with (v5.44.0;
Nexstrom's colours since v5.45.0; app name on top since v5.53.0).

Colours, fonts and radii live in `.streamlit/config.toml`. A few things don't
fit there. Streamlit draws a bordered container transparent, so on the grey
page background the cards would be grey too; `apply_theme()` paints them
white. Headings take Nexstrom's heading navy, which no theme option sets. And
the header — an uppercase eyebrow naming the nav section and page, the title,
one line saying what the page is for — is the same three lines on every page,
so it is written once, in `page_header()`.

The sidebar names the app first and the company last (v5.53.0): NexAnalyzer
and its version at the top, "A product of" and the Nexstrom logo at the foot
(`sidebar_footer()`). Nexstrom is the company, NexAnalyzer the app, and until
then the sidebar showed only the company's logo where the app's name belongs.
The name is drawn by CSS into the sidebar header rather than by `st.logo`:
`st.logo` takes only an image, and a rendered wordmark would not be in IBM
Plex unless the font were shipped too.

Called from app.py, which runs before every page, so no page needs to apply
the theme itself.
"""

import base64

import streamlit as st

from core.paths import PROJECT_ROOT
from core.version import APP_NAME, __version__

#: Nexstrom's palette (nexstrom.com), for the few places that colour inline HTML.
INK = "#0B1220"
MUTED = "#344B68"
ACCENT = "#2F72BF"
NAVY = "#073A6C"
BORDER = "#D3DFEC"
TINT = "#EAF2F8"

#: The logo in white, for the navy sidebar's foot. assets/nexstrom_logo.png is
#: the original navy one.
LOGO = PROJECT_ROOT / "assets" / "nexstrom_logo_white.png"

_CSS = f"""
<style>
/* The app's name and version at the top of the sidebar, in the header row
   beside the collapse button: the name fills the first line with the button,
   and the version wraps under it as a pill. */
[data-testid="stSidebarHeader"] {{
    flex-wrap: wrap; align-items: center; row-gap: 6px; height: auto;
    padding-top: 1.25rem; padding-bottom: 0.75rem;
}}
[data-testid="stSidebarHeader"]::before {{
    content: "{APP_NAME}"; order: 0; flex: 0 0 calc(100% - 48px);
    font-size: 26px; font-weight: 600; line-height: 1.15;
    letter-spacing: 0.01em; color: #FFFFFF;
}}
[data-testid="stSidebarCollapseButton"] {{ order: 1; }}
[data-testid="stSidebarHeader"]::after {{
    content: "v{__version__}"; order: 2;
    font-family: "IBM Plex Mono", monospace; font-size: 12px; color: #FFFFFF;
    background: #0D4277; border-radius: 999px; padding: 2px 8px;
}}

/* The maker at the foot of the sidebar (sidebar_footer()). */
[data-testid="stSidebarContent"] {{ display: flex; flex-direction: column; }}
[data-testid="stSidebarUserContent"] {{ margin-top: auto; }}
.nx-maker {{
    display: flex; flex-direction: column; gap: 8px;
    padding-top: 16px; border-top: 1px solid #1A4A7A;
}}
.nx-maker span {{
    font-size: 12px; text-transform: uppercase; letter-spacing: 0.08em;
    color: #C9D6E6;
}}
.nx-maker img {{ height: 18px; width: auto; align-self: flex-start; }}

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
    """Inject the shared CSS. Call once per run, before the page runs."""
    st.html(_CSS)


def sidebar_footer() -> None:
    """"A product of" and the Nexstrom logo, at the foot of the sidebar.

    Inlined as a data URI: `st.html` can't address a file on disk, and
    `st.image` would add a full-screen button to a logo.
    """
    logo = base64.b64encode(LOGO.read_bytes()).decode("ascii")
    st.sidebar.html(
        '<div class="nx-maker"><span>A product of</span>'
        f'<img src="data:image/png;base64,{logo}" alt="Nexstrom"></div>'
    )


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
