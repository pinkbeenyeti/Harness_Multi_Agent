import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agents" / "skills" / "custom-multi-agent" / "scripts"))
import knot_manager as knot


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOT_VAULT", str(tmp_path))
    inbox, wiki = knot.setup_vault(tmp_path)
    return inbox, wiki, tmp_path / "archive"


def test_markdown_ingest_archives_original(vault, monkeypatch):
    inbox, wiki, archive = vault
    source = inbox / "note.md"
    source.write_text("# Note", encoding="utf-8")
    monkeypatch.setattr(knot.os, "remove", lambda *_: pytest.fail("os.remove called"))

    knot.ingest_documents()

    assert not source.exists()
    assert (wiki / "note.md").read_text(encoding="utf-8") == "# Note"
    assert (archive / "note.md").read_text(encoding="utf-8") == "# Note"


@pytest.mark.parametrize("name", [
    "apiKEY.md", "client_secret.txt", ".env", "auth_token.json",
    "cert.pem", "user.id_rsa", "USER.ID_RSA.BAK",
])
def test_sensitive_names_are_rejected(vault, name):
    inbox, wiki, archive = vault
    source = inbox / name
    source.write_text("private", encoding="utf-8")

    knot.ingest_documents()

    assert source.exists()
    assert not list(wiki.glob(f"{source.stem}*.md"))
    assert not list(archive.iterdir())


def test_extension_and_size_guards_precede_llm(vault, monkeypatch):
    inbox, wiki, _ = vault
    (inbox / "binary.exe").write_bytes(b"x")
    (inbox / "large.txt").write_bytes(b"x" * (knot.MAX_FILE_SIZE + 1))
    calls = []
    monkeypatch.setattr(
        knot, "call_llm_for_wiki",
        lambda *args: calls.append(args))

    knot.ingest_documents()

    assert calls == []
    assert not list(wiki.glob("*.md"))


def test_one_mib_boundary_is_allowed(vault, monkeypatch):
    inbox, wiki, archive = vault
    source = inbox / "limit.txt"
    source.write_bytes(b"x" * knot.MAX_FILE_SIZE)
    monkeypatch.setattr(knot, "call_llm_for_wiki", lambda *_: None)

    knot.ingest_documents()

    assert (wiki / "limit.md").exists()
    assert (archive / "limit.txt").stat().st_size == knot.MAX_FILE_SIZE


def test_wiki_and_archive_collisions_are_numbered(vault):
    inbox, wiki, archive = vault
    for directory, suffix in ((wiki, ".md"), (archive, ".md")):
        (directory / f"note{suffix}").write_text("old", encoding="utf-8")
        (directory / f"note_1{suffix}").write_text("old", encoding="utf-8")
    (inbox / "note.md").write_text("new", encoding="utf-8")

    knot.ingest_documents()

    assert (wiki / "note_2.md").read_text(encoding="utf-8") == "new"
    assert (archive / "note_2.md").read_text(encoding="utf-8") == "new"


def test_llm_failure_keeps_inbox_source(vault, monkeypatch):
    inbox, wiki, _ = vault
    source = inbox / "note.txt"
    source.write_text("body", encoding="utf-8")
    monkeypatch.setattr(
        knot, "call_llm_for_wiki",
        lambda *_: (_ for _ in ()).throw(RuntimeError("LLM failed")))

    knot.ingest_documents()

    assert source.exists()
    assert not (wiki / "note.md").exists()


def test_archive_failure_does_not_publish_wiki(vault, monkeypatch):
    inbox, wiki, _ = vault
    source = inbox / "note.md"
    source.write_text("body", encoding="utf-8")
    monkeypatch.setattr(
        knot.shutil, "move",
        lambda *_: (_ for _ in ()).throw(OSError("move failed")))

    knot.ingest_documents()

    assert source.exists()
    assert not (wiki / "note.md").exists()


def test_publish_failure_preserves_archived_source(vault, monkeypatch):
    inbox, wiki, archive = vault
    source = inbox / "note.md"
    source.write_text("body", encoding="utf-8")
    monkeypatch.setattr(
        knot.os, "replace",
        lambda *_: (_ for _ in ()).throw(OSError("replace failed")))

    knot.ingest_documents()

    assert not source.exists()
    assert (archive / "note.md").read_text(encoding="utf-8") == "body"
    assert not (wiki / "note.md").exists()
