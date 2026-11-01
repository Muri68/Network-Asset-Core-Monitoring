import pytest


@pytest.fixture(autouse=True)
def pending_dir(tmp_path, monkeypatch):
    """Keep import previews out of the project's var/ folder."""
    from apps.dataio import views

    monkeypatch.setattr(views, "PENDING_DIR", tmp_path / "imports")
