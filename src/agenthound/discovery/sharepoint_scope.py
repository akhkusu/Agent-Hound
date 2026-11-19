"""Keep document/site content separate from SharePoint implementation files."""

from __future__ import annotations

from urllib.parse import unquote

_SYSTEM_LIBRARY_ROOTS = {
    "_catalogs", "formservertemplates", "iwconvertedforms", "style library",
}


def library_exclusion_reason(library_path: str, *, hidden: bool = False) -> str | None:
    if hidden:
        return "hidden_library"
    first = unquote(library_path).strip("/").split("/", 1)[0].casefold()
    return "system_library" if first in _SYSTEM_LIBRARY_ROOTS else None


def is_library_forms_path(relative_path: str) -> bool:
    return unquote(relative_path).strip("/").split("/", 1)[0].casefold() == "forms"
