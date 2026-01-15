"""Basic tests to verify DAKB package structure."""

import pytest


def test_dakb_import() -> None:
    """Test that dakb package can be imported."""
    import dakb
    assert dakb is not None


def test_dakb_version() -> None:
    """Test that dakb has a version."""
    from dakb import __version__
    assert __version__ == "1.2.1"


def test_embeddings_module_exists() -> None:
    """Test that embeddings module exists."""
    from dakb import embeddings
    assert embeddings is not None


def test_gateway_module_exists() -> None:
    """Test that gateway module exists."""
    from dakb import gateway
    assert gateway is not None


def test_mcp_module_exists() -> None:
    """Test that mcp module exists."""
    from dakb import mcp
    assert mcp is not None


def test_db_module_exists() -> None:
    """Test that db module exists."""
    from dakb import db
    assert db is not None


class TestPackageStructure:
    """Test package structure and imports."""

    def test_embeddings_has_run(self) -> None:
        """Test embeddings module has run function."""
        from dakb.embeddings import run
        assert callable(run)

    def test_gateway_has_app(self) -> None:
        """Test gateway module has FastAPI app."""
        from dakb.gateway.main import app
        assert app is not None
