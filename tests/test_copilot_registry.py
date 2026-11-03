from contextlib import nullcontext
from types import SimpleNamespace

from agenthound.discovery import copilot_registry


def test_registry_reads_only_known_flags_and_distinguishes_access_failure(monkeypatch):
    reads = []

    def open_key(hive, path):
        if path.endswith("Auth"):
            raise PermissionError("Denied")
        if path.endswith("UserEligibility"):
            raise FileNotFoundError()
        return nullcontext(path)

    def value(key, name):
        reads.append(name)
        # A mistyped boolean must never be echoed as a secret-bearing string.
        return ("secret", 1) if name == "oneAuthNativeGates_oneAuthNativeEnabled" else (0, 4)

    registry = SimpleNamespace(
        HKEY_CURRENT_USER=1, REG_DWORD=4, OpenKey=open_key,
        QueryInfoKey=lambda key: (2, 100, 0), QueryValueEx=value,
    )
    monkeypatch.setattr(copilot_registry.importlib, "import_module", lambda _: registry)
    result = copilot_registry.inventory_copilot_registry()
    assert result["keys"][0]["status"] == "unreadable"
    assert result["keys"][-1]["status"] == "missing"
    assert result["keys"][1]["cached_feature_booleans"] == {"isAutopilotNodeEnabled": False}
    assert len(reads) == 3
    assert "secret" not in str(result)


def test_registry_unavailable_platform_is_explicit(monkeypatch):
    def unavailable(_):
        raise ImportError()

    monkeypatch.setattr(copilot_registry.importlib, "import_module", unavailable)
    assert copilot_registry.inventory_copilot_registry() == {
        "status": "unsupported_platform", "keys": [],
    }
