

def test_proof_step_cannot_touch_the_machine(tmp_path):
    """The proof call must not be able to write, delete, spawn or connect.

    Proving a mutant means running the real function on the inputs the tests used. When
    those inputs are real paths - which is normal for a file tool - that used to run the
    side effects too: auditing a `purge(path)` whose test passes a real path DELETED that
    path, and the report still said "no provable gap found". A clean bill of health and a
    lost file in the same run.

    The guard is exercised through the same _call the runner uses, so this fails if the
    audit hook is removed or stops covering one of these events.
    """
    import os
    import socket
    import subprocess
    import sys
    from pathlib import Path

    module = Path(__file__).parent.parent / "src" / "suite_auditor" / "differential.py"
    src = module.read_text(encoding="utf-8")
    block = src[src.index("class _SideEffect(BaseException):") : src.index("def _equal(x, y):")]
    ns = {"sys": sys, "_NOEQ": object(), "_render": lambda v: (repr(v), v), "_norm": lambda s: s}
    exec(block, ns)  # noqa: S102 - running the shipped guard is the point
    call, guard = ns["_call"], ns["_GUARD"]

    probe = tmp_path / "keep.txt"
    probe.write_text("keep me", encoding="utf-8")

    # Reading must still work: real functions read, and blocking that would make the
    # proof step useless rather than safe.
    assert call(lambda: 1 + 1, (), {})[0] == ("ok", "2")
    assert call(lambda: probe.read_text(encoding="utf-8"), (), {})[0][0] == "ok"

    def swallows_it() -> str:
        try:
            os.remove(probe)
        except Exception:
            return "caught it myself"
        return "deleted"

    for label, fn in [
        ("delete", lambda: os.remove(probe)),
        ("write", lambda: probe.write_text("clobbered", encoding="utf-8")),
        ("spawn", lambda: subprocess.run([sys.executable, "-c", "1"], check=False)),
        ("connect", lambda: socket.create_connection(("example.com", 80), 1)),
        # A function that wraps its own side effect in `except Exception` must not be
        # able to hide the block - that is why _SideEffect derives from BaseException.
        ("self-swallowed", swallows_it),
    ]:
        (kind, _detail), _ = call(fn, (), {})
        assert kind == "impure", f"{label} was not blocked: got {kind}"

    assert probe.exists(), "the guarded calls changed the filesystem"
    assert probe.read_text(encoding="utf-8") == "keep me"
    assert guard[0] is False, "the guard was left on, which would affect the tool's own I/O"
