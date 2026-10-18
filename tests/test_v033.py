"""v0.03.3: LM Studio, proveedores de chat y validación de URL. Sin red."""
import pytest

from brain_mcp import lmstudio
from brain_mcp.llm_providers import canonical, display_label, parse_ref


def test_lmstudio_url_validation():
    with pytest.raises(lmstudio.LmStudioError) as e:
        lmstudio.save({"url": "ftp://127.0.0.1:1234"})
    assert e.value.code == "bad_url"
    with pytest.raises(lmstudio.LmStudioError) as e:
        lmstudio.save({"url": "https://evil.example/v1"})
    assert e.value.code == "bad_url_host"
    cfg = lmstudio.save({"url": "http://127.0.0.1:1234/v1", "enabled": True})
    assert cfg["url"] == "http://127.0.0.1:1234"


def test_model_refs():
    assert parse_ref("llama3.2") == ("ollama", "llama3.2")
    assert parse_ref("ollama:llama3.2") == ("ollama", "llama3.2")
    assert parse_ref("lmstudio:my-model") == ("lmstudio", "my-model")
    assert canonical("ollama", "x") == "ollama:x"
    assert display_label("lmstudio", "qwen") == "LM Studio · qwen"
