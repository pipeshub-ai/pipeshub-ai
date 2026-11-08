from app.agent_loop_lib.sandbox.confinement import _bubblewrap_wrap


def test_working_dir_bind_comes_after_tmpfs_so_it_is_not_hidden() -> None:
    # The working dir lives under /tmp; a tmpfs mounted after the bind hides it.
    args = _bubblewrap_wrap(["true"], "/tmp/work", allow_network=False)
    assert args.index("--tmpfs") < args.index("--bind")
