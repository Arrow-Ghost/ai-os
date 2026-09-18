from servant.audit import AuditLog
from servant.governance import Redactor
from servant.memory import Memory


def test_chain_verifies(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    for i in range(5):
        log.write("act", "run-1", step=i)
    assert log.verify()[0] is True


def test_edited_record_is_detected(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    log.write("act", "run-1", tool="files.trash")
    log.write("act", "run-1", tool="notes.save")

    lines = path.read_text().splitlines()
    lines[0] = lines[0].replace("files.trash", "files.list")  # rewrite history
    path.write_text("\n".join(lines) + "\n")

    ok, message = AuditLog(path).verify()
    assert ok is False and "chain broken" in message


def test_deleted_middle_record_is_detected(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    for i in range(3):
        log.write("act", "run-1", step=i)

    lines = path.read_text().splitlines()
    path.write_text(lines[0] + "\n" + lines[2] + "\n")  # drop the middle one

    ok, message = AuditLog(path).verify()
    assert ok is False and "chain broken" in message


def test_tail_truncation_is_NOT_detected(tmp_path):
    """Known limit, documented on purpose.

    A hash chain proves nothing was edited or removed from the middle. It
    cannot prove nothing was removed from the end -- the shortened file is
    still internally consistent. Detecting that needs an external anchor
    (a counter or a copy off the machine). Do not claim more than this.
    """
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    log.write("act", "run-1", step=0)
    log.write("act", "run-1", step=1)

    lines = path.read_text().splitlines()
    path.write_text(lines[0] + "\n")  # chop the last record

    assert AuditLog(path).verify()[0] is True


def test_audit_redacts_before_writing(tmp_path, config):
    log = AuditLog(tmp_path / "audit.jsonl", redactor=Redactor.from_config(config))
    log.write("act", "run-1", args={"key": "sk-abcdefghijklmnopqrst"})
    assert "sk-abcdefghij" not in (tmp_path / "audit.jsonl").read_text()


def test_memory_roundtrip(tmp_path):
    m = Memory(tmp_path / "m.db")
    m.remember("user.name", "Sarthak")
    assert m.recall("user.name") == "Sarthak"
    assert m.recall("missing", "fallback") == "fallback"

    m.add_episode("run-1", "user", "tidy downloads")
    assert m.recent_episodes(5)[0]["content"] == "tidy downloads"

    m.record_outcome("files.trash", True)
    m.record_outcome("files.trash", False)
    assert m.success_rate("files.trash") == 0.5
    assert m.success_rate("never.run") is None
