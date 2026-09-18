import pytest

from servant.registry import REGISTRY
from servant.sdk import Tier, tool


def test_tool_registers_with_derived_schema():
    @tool(name="demo.add", tier=Tier.READ, params={"a": "first"})
    def demo_add(ctx, a: int, b: float = 1.5) -> str:
        """Add two numbers."""
        return str(a + b)

    spec = REGISTRY.get("demo.add")
    schema = spec.to_openai_schema()["function"]

    assert spec.description == "Add two numbers."
    assert schema["parameters"]["properties"]["a"]["type"] == "integer"
    assert schema["parameters"]["properties"]["b"]["type"] == "number"
    assert schema["parameters"]["required"] == ["a"]  # b has a default


def test_name_defaults_to_dotted_function_name():
    @tool(tier=Tier.READ)
    def files_touch(ctx) -> str:
        """Touch a file."""

    assert "files.touch" in REGISTRY


def test_first_argument_must_be_ctx():
    with pytest.raises(TypeError, match="must be 'ctx'"):
        @tool(tier=Tier.READ)
        def bad(path: str) -> str:
            """Missing ctx."""


def test_varargs_rejected():
    with pytest.raises(TypeError, match=r"\*args"):
        @tool(tier=Tier.READ)
        def bad(ctx, *args) -> str:
            """No."""


def test_duplicate_names_rejected():
    @tool(name="dupe.tool", tier=Tier.READ)
    def one(ctx) -> str:
        """First."""

    with pytest.raises(ValueError, match="already registered"):
        @tool(name="dupe.tool", tier=Tier.READ)
        def two(ctx) -> str:
            """Second."""
