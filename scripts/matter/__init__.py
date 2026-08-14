"""Matter (hq.getmatter.com) -> Article Archive sync.

The archive's source of truth is Markdown + YAML frontmatter in the vault; the
Parquet index and the Streamlit dashboard are built from it. This package pulls
Adam's Matter reading into that same shape so the Instapaper era (~2008-2025)
and the Matter era read as one continuous history.

Runtime constraint: everything on the sync path uses only the standard library
plus `requests` and `PyYAML`, because the nightly launchd job runs under
/opt/homebrew/bin/python3 (the interpreter holding the TCC grant), which does
not have `python-frontmatter` or `pyarrow` installed. Those two are optional
enhancements, never required.
"""

__all__ = [
    "api",
    "credentials",
    "errors",
    "mapping",
    "normalize",
    "state",
    "sync",
    "vaultindex",
]
