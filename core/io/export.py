"""
Technique-agnostic export plumbing: native Save-As/Select-Folder dialogs,
figure rasterization, and output filename construction.

Fit-results CSVs live in results_csv.py — this module knows nothing about
peaks or spectra.
"""

from typing import Optional

from .native_dialog import existing_dir, run_dialog


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
    try:
        png_bytes = fig.to_image(
            format='png',
            width=width,
            height=height,
            scale=scale,
        )
        return png_bytes
    except Exception as e:
        raise RuntimeError(
            f"PNG export failed: {e}. "
            f"Make sure kaleido is installed: pip install kaleido"
        ) from e


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
