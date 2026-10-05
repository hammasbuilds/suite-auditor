

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


def test_a_blocked_call_is_inconclusive_not_agreement(tmp_path):
    """One blocked argument set must not come back as "all agree".

    The guard was added first and this hole came with it. Blocked rows were dropped from
    the comparison, and the give-up branch only fired when NOTHING had been exercised.
    With a single argument set - the original returning a value, the mutant trying to
    delete a file - that left no rows at all, and compare() answered
    "agree: 1 of 0 inputs exercised it, all agree".

    Nothing was established there. An argument set whose two sides never both returned
    cannot support agreement or a gap, and saying "agree" is the clean bill of health
    this whole module exists to refuse.
    """
    from suite_auditor.differential import compare

    canary = tmp_path / "keep.txt"
    canary.write_text("important", encoding="utf-8")

    old = "\n".join(
        [
            "def purge(path, dry_run=True):",
            "    if dry_run:",
            '        return "would delete"',
            "    os.remove(path)",
            '    return "deleted"',
            "",
        ]
    )
    # An ordinary mutation operator: negate the condition. Under the unguarded proof
    # step this became a real os.remove on a real path.
    mutant = old.replace("    if dry_run:", "    if not dry_run:")

    outcome = compare(
        header="import os",
        old=old,
        new=mutant,
        func="purge",
        argsets=[f"({str(canary)!r},)"],
        timeout=60,
    )

    assert outcome["status"] == "inconclusive", outcome
    assert "leave the process" in outcome["detail"]
    assert "os.remove" in outcome["detail"]
    assert canary.exists(), "the proof step deleted the file it was asked about"
    assert canary.read_text(encoding="utf-8") == "important"
