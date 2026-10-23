"""``streamlit run``, with the OpenBLAS kernel pinned first.

Streamlit imports numpy before it executes app.py, so the pin in
``core/__init__.py`` comes too late under a bare ``streamlit run``. Importing
``core`` here (as ``python -m core.launch`` does) pins it before Streamlit
loads. Arguments are passed through: ``python -m core.launch app.py --server.port 8501``.
"""

import sys

from streamlit.web import cli

if __name__ == "__main__":
    sys.argv = ["streamlit", "run", *sys.argv[1:]]
    sys.exit(cli.main())
