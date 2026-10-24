"""
Technique-agnostic export plumbing: native Save-As/Select-Folder dialogs,
figure rasterization, and output filename construction.

Fit-results CSVs live in results_csv.py — this module knows nothing about
peaks or spectra.
"""

import threading
from typing import Optional

from .native_dialog import existing_dir, run_dialog

#: Seconds one PNG render may take before it is abandoned. A healthy render
#: takes 0.1-1 s; this only has to be long enough to never fire on one.
RENDER_TIMEOUT_S = 120.0

# One render at a time: the persistent kaleido server is a process-wide
# singleton fed through one task queue and one return queue, so two Streamlit
# sessions rendering at once could each receive the other's image.
_RENDER_LOCK = threading.Lock()


def _ensure_render_server() -> None:
    """Start kaleido's persistent Chrome once per process, if it can start.

    Without it every `fig.to_image` launches and tears down its own headless
    Chrome. Measured on this machine (2026-10-24): one-shot renders took 1.2 s
    each, left ~4 temp folders apiece, and one of the first 15 never answered
    -- the 12 h QC test hang and the 43 min CI stall. A persistent server did
    500 of 500 at 0.07 s. If it cannot start (no Chrome), plotly's own one-shot
    path is used and raises the usual error.
    """
    try:
        import kaleido
        kaleido.start_sync_server(silence_warnings=True)
    except Exception:
        pass


def _abandon_render_server() -> None:
    """Forget a server whose Chrome stopped answering, so the next render
    starts a fresh one. Its thread is a daemon blocked on the dead Chrome and
    goes with the process; `stop_sync_server` would block on it too."""
    try:
        import kaleido
        kaleido._global_server._initialized = False  # private: no public way to drop a hung server
    except Exception:
        pass


def prompt_save_path(default_dir: Optional[str],
                     default_filename: str,
                     title: str = "Save File",
                     filetypes=(("CSV files", "*.csv"), ("All files", "*.*")),
                     default_extension: str = ".csv") -> Optional[str]:
    """
    Open a native OS "Save As" dialog and return the chosen path.

    Runs tkinter.filedialog.asksaveasfilename in a subprocess (see
    `native_dialog`), so the user can choose the
    folder AND type the filename. The dialog opens pre-pointed at ``default_dir``
    with ``default_filename`` pre-filled.

    Parameters
    ----------
    default_dir : Optional[str]
        Folder to open the dialog in. If None/empty/nonexistent, the OS default
        is used.
    default_filename : str
        Pre-filled filename in the dialog (user can edit).
    title : str
        Dialog window title.
    filetypes : tuple
        (label, pattern) pairs for the file-type filter.
    default_extension : str
        Extension appended if the user types a name without one.

    Returns
    -------
    Optional[str]
        Absolute path chosen by the user, or None if the dialog was cancelled.
    """
    path = run_dialog(
        "asksaveasfilename",
        title=title,
        initialdir=existing_dir(default_dir),
        initialfile=default_filename,
        defaultextension=default_extension,
        filetypes=[list(ft) for ft in filetypes],
    )
    # Empty string => user cancelled the dialog.
    return path or None


def prompt_save_folder(default_dir: Optional[str], title: str = "Select Folder") -> Optional[str]:
    """
    Open a native OS "Select Folder" dialog and return the chosen directory.

    Same subprocess-isolation pattern as `prompt_save_path` (tkinter does not
    coexist with Streamlit's own event loop in-process), but
    `askdirectory` in place of `asksaveasfilename`: a batch export writes N
    files, which needs a destination folder, not one destination filename.

    Parameters
    ----------
    default_dir : Optional[str]
        Folder to open the dialog in. If None/empty/nonexistent, the OS default
        is used.
    title : str
        Dialog window title.

    Returns
    -------
    Optional[str]
        Absolute path chosen by the user, or None if the dialog was cancelled.
    """
    path = run_dialog("askdirectory", title=title, initialdir=existing_dir(default_dir))
    return path or None


def export_figure_png(fig, width: int = 1200, height: int = 600, scale: float = 2.0) -> bytes:
    """
    Export Plotly figure to PNG bytes.

    Parameters
    ----------
    fig : plotly.graph_objects.Figure
        Figure to export.
    width : int, default=1200
        Image width in pixels.
    height : int, default=600
        Image height in pixels.
    scale : float, default=2.0
        Scale factor for high-DPI displays (2.0 = retina quality).

    Returns
    -------
    png_bytes : bytes
        PNG image as bytes.

    Notes
    -----
    Requires kaleido package: pip install kaleido

    No ``engine=`` argument is passed: Plotly 6 deprecated it and Plotly 7
    removed it, so naming the engine raises TypeError there. Plotly picks
    kaleido on its own when it is installed.

    Examples
    --------
    >>> png = export_figure_png(fig)
    >>> with open('plot.png', 'wb') as f:
    ...     f.write(png)
    """
    box = {}

    def render():
        try:
            box["png"] = fig.to_image(format='png', width=width, height=height, scale=scale)
        except Exception as e:  # handed back to the caller's thread below
            box["error"] = e

    with _RENDER_LOCK:
        _ensure_render_server()
        worker = threading.Thread(target=render, daemon=True)
        worker.start()
        worker.join(RENDER_TIMEOUT_S)
        if worker.is_alive():
            _abandon_render_server()
            raise RuntimeError(
                f"PNG export timed out after {RENDER_TIMEOUT_S:.0f} s: the Chrome renderer "
                f"stopped answering. Try again; a fresh renderer is started next time."
            )
    if "error" in box:
        raise RuntimeError(
            f"PNG export failed: {box['error']}. "
            f"Make sure kaleido is installed: pip install kaleido"
        ) from box["error"]
    return box["png"]


def export_figure_html(fig) -> str:
    """
    Export Plotly figure to interactive HTML.

    Parameters
    ----------
    fig : plotly.graph_objects.Figure
        Figure to export.

    Returns
    -------
    html_string : str
        Standalone HTML file content.

    Notes
    -----
    The HTML file is fully self-contained (includes Plotly.js library).
    Can be opened in any web browser with full interactivity.

    Examples
    --------
    >>> html = export_figure_html(fig)
    >>> with open('plot.html', 'w') as f:
    ...     f.write(html)
    """
    html_string = fig.to_html(
        include_plotlyjs='cdn',  # Use CDN for smaller file size
        full_html=True,
        config={'displayModeBar': True, 'displaylogo': False}
    )
    return html_string


def create_filename(base_name: str, suffix: str, extension: str) -> str:
    """
    Create safe filename for export.

    Parameters
    ----------
    base_name : str
        Base filename (e.g., spectrum filename without .txt).
    suffix : str
        Suffix to add (e.g., "fit", "preview").
    extension : str
        File extension (e.g., "csv", "png", "html").

    Returns
    -------
    filename : str
        Safe filename.

    Examples
    --------
    >>> create_filename("sample_raman.txt", "fit", "csv")
    'sample_raman_fit.csv'
    """
    # Remove original extension if present
    if base_name.endswith('.txt'):
        base_name = base_name[:-4]

    # Create new filename
    return f"{base_name}_{suffix}.{extension}"
