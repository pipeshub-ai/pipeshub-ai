"""The materialiser child (run in-process against a fake storage) and the pool (real spawned processes)."""

from __future__ import annotations

import ast
import asyncio
import base64
import hashlib
import os
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

import pytest

from app.connectors.sources.localKB.push import images as I
from app.connectors.sources.localKB.push import materialiser as M
from app.connectors.sources.localKB.push import materialiser_child as C
from app.connectors.sources.localKB.push.contract import PushRefusal

from .fake_storage import FakeStorage, etag_of

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
JPEG = b"\xff\xd8\xff\xe0" + b"j" * 100
NONCE = "ab" * 16
CHILD_SRC = Path(C.__file__)

needs_proc = pytest.mark.skipif(not os.path.exists("/proc/self/limits"), reason="not applicable on this platform: no /proc")


def doc_id(n: int) -> str:
    return f"{n:024x}"


@pytest.fixture
async def store() -> AsyncIterator[object]:
    s = await FakeStorage().start()
    yield s
    await s.stop()


def make_task(tmp_path, store, refs, occurrences=None, cap=50 << 20, text=b"", **over) -> object:
    """A sentinel file with ``occurrences[i]`` tokens of each ref, and the task for it."""
    occurrences = occurrences or [1] * len(refs)
    body = text
    for i, n in enumerate(occurrences):
        body += b",".join([I.sentinel_token(NONCE, i)] * n) + b";"
    sentinel = tmp_path / "sentinel.json"
    sentinel.write_bytes(body)
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    return {
        "sentinel_path": str(sentinel), "out_path": str(tmp_path / "out.json"), "work_dir": str(work), "nonce": NONCE,
        "refs": refs, "storage_url": store.url, "token": "t", "cap_bytes": cap, "allow_loopback": True,
        "listed_origins": [], "request_timeout_s": 10, **over,
    }


def ref_for(store, i, data, *, vendor="local", declared=True, object_url=None, **kw) -> object:
    store.add(doc_id(i), data, vendor=vendor, **kw)
    return {"id": doc_id(i), "etag": etag_of(data), "vendor": vendor, "size": len(data) if declared else None, "object_url": object_url}


async def go(task) -> object:
    try:
        return await C.run_task(task)
    except C.Refused as r:
        return r


class TestRefusalReasons:
    """A failed fetch says why (status or error class), never the URL or its query string."""

    async def test_a_download_status_is_in_the_message(self, tmp_path, store) -> None:
        ref = ref_for(store, 1, PNG)
        store.mode[doc_id(1)] = "500"
        r = await go(make_task(tmp_path, store, [ref]))
        assert isinstance(r, C.Refused) and r.reason == "IMAGE_FETCH_FAILED" and "HTTP 500" in r.message

    async def test_a_connection_failure_names_its_class_and_not_the_url(self, tmp_path, store) -> None:
        url = "http://127.0.0.1:1/org/key.png?X-Amz-Signature=secret"
        ref = ref_for(store, 1, PNG, vendor="s3", object_url="http://127.0.0.1:1/org/key.png")
        store.signed_override[doc_id(1)] = url
        r = await go(make_task(tmp_path, store, [ref]))
        assert isinstance(r, C.Refused) and "ClientConnectorError" in r.message
        assert "secret" not in r.message and "127.0.0.1" not in r.message and "X-Amz" not in r.message


class TestSplice:
    async def test_output_has_inlined_images_size_and_rev_match_the_bytes(self, tmp_path, store) -> None:
        refs = [ref_for(store, 1, PNG), ref_for(store, 2, JPEG)]
        task = make_task(tmp_path, store, refs, [2, 1])
        out = await go(task)
        data = Path(task["out_path"]).read_bytes()
        assert out["size"] == len(data) and out["content_rev"] == hashlib.sha256(data).hexdigest()[:16]
        assert data.count(b'"data:image/png;base64,' + base64.b64encode(PNG) + b'"') == 2
        assert data.count(b'"data:image/jpeg;base64,' + base64.b64encode(JPEG) + b'"') == 1
        assert b"pb-img" not in data

    async def test_text_that_looks_like_a_sentinel_is_left_alone(self, tmp_path, store) -> None:
        trap = I.sentinel_token("0" * 32, 0)
        task = make_task(tmp_path, store, [ref_for(store, 1, PNG)], text=trap + b"|")
        await go(task)
        data = Path(task["out_path"]).read_bytes()
        assert data.startswith(trap + b"|") and b"data:image/png" in data

    async def test_lowercase_data_prefix(self, tmp_path, store) -> None:
        task = make_task(tmp_path, store, [ref_for(store, 1, PNG)])
        await go(task)
        assert b'"data:image/' in Path(task["out_path"]).read_bytes() and b"DATA:" not in Path(task["out_path"]).read_bytes()


class TestMagic:
    @pytest.mark.parametrize("data", [b"just text", b"II*\x00" + b"x" * 50, b"\x00\x00\x01\x00" + b"x" * 50, b"\x00\x00\x00\x18ftypheic" + b"x" * 20, b"<svg xmlns='http://www.w3.org/2000/svg'/>", b"RIFF\x00\x00\x00\x00WAVEfmt "], ids=["non-image", "tiff", "ico", "heic", "svg", "wrong-subtype"])
    async def test_rejects(self, tmp_path, store, data) -> None:
        r = await go(make_task(tmp_path, store, [ref_for(store, 1, data)]))
        assert isinstance(r, C.Refused) and (r.status, r.reason) == (422, "IMAGE_FETCH_FAILED")

    async def test_subtype_comes_from_the_bytes_not_the_response_header(self, tmp_path, store) -> None:
        task = make_task(tmp_path, store, [ref_for(store, 1, JPEG, extra_headers={"Content-Type": "image/png"})])
        await go(task)
        assert b"data:image/jpeg;base64," in Path(task["out_path"]).read_bytes()


class TestRunningTotals:
    async def test_lying_info_aborts_at_the_budget_written(self, tmp_path, store) -> None:
        big = PNG + b"x" * (3 << 20)
        ref = ref_for(store, 1, big, declared=False)  # info said nothing; the body decides
        r = await go(make_task(tmp_path, store, [ref], [16], cap=10 << 20))
        assert isinstance(r, C.Refused) and (r.status, r.extra["limit"], r.extra["limitBytes"]) == (413, "resolved", 10 << 20)

    async def test_output_file_never_exceeds_the_cap(self, tmp_path, store) -> None:
        big = PNG + b"x" * (3 << 20)
        task = make_task(tmp_path, store, [ref_for(store, 1, big)], [16], cap=10 << 20)
        await go(task)
        assert os.path.getsize(task["out_path"]) <= 10 << 20

    async def test_temp_disk_never_exceeds_36_mib(self, tmp_path, store) -> None:
        refs = [ref_for(store, i, PNG + b"x" * (15 << 20), declared=False) for i in range(1, 4)]
        r = await go(make_task(tmp_path, store, refs))
        assert isinstance(r, C.Refused) and r.reason == "IMAGE_FETCH_FAILED"
        assert sum(f.stat().st_size for f in (tmp_path / "work").iterdir()) <= 36 << 20

    async def test_gzip_content_encoding_is_refused(self, tmp_path, store) -> None:
        ref = ref_for(store, 1, PNG, extra_headers={"Content-Encoding": "gzip"})
        r = await go(make_task(tmp_path, store, [ref]))
        assert isinstance(r, C.Refused) and r.reason == "IMAGE_FETCH_FAILED"

    async def test_body_one_byte_over_20_mib_is_cut_mid_stream(self, tmp_path, store) -> None:
        ref = ref_for(store, 1, PNG + b"x" * ((20 << 20) - len(PNG) + 1), declared=False)
        r = await go(make_task(tmp_path, store, [ref]))
        assert isinstance(r, C.Refused) and r.reason == "IMAGE_FETCH_FAILED"
        assert os.path.getsize(tmp_path / "work" / (doc_id(1) + ".img")) <= (20 << 20) + 64 * 1024

    async def test_exactly_20_mib_is_accepted(self, tmp_path, store) -> None:
        ref = ref_for(store, 1, PNG + b"x" * ((20 << 20) - len(PNG)), declared=False)
        assert not isinstance(await go(make_task(tmp_path, store, [ref])), C.Refused)

    async def test_trusted_size_mismatch_is_image_fetch_failed(self, tmp_path, store) -> None:
        ref = ref_for(store, 1, PNG)
        ref["size"] = len(PNG) + 5
        r = await go(make_task(tmp_path, store, [ref]))
        assert isinstance(r, C.Refused) and r.reason == "IMAGE_FETCH_FAILED"

    async def test_size_checked_whenever_object_size_exists(self, tmp_path, store) -> None:
        ref = ref_for(store, 1, PNG)
        ref["size"] = 3  # an untrusted answer still carries the size it saw
        assert isinstance(await go(make_task(tmp_path, store, [ref])), C.Refused)


class TestBinding:
    async def test_ifmatch_query_for_local(self, tmp_path, store) -> None:
        ref = ref_for(store, 1, PNG)
        store.objects[doc_id(1)].data = PNG + b"changed"  # replaced after the verified read
        r = await go(make_task(tmp_path, store, [ref]))
        assert isinstance(r, C.Refused) and r.reason == "IMAGE_FETCH_FAILED" and "send the push again" in r.message

    async def test_ifmatch_header_for_s3_and_azure(self, tmp_path, store) -> None:
        data = PNG
        ref = ref_for(store, 1, data, vendor="s3", object_url=f"{store.url}/obj/{doc_id(1)}")
        task = make_task(tmp_path, store, [ref])
        assert not isinstance(await go(task), C.Refused)
        assert store.last_signed_headers["If-Match"] == f'"{etag_of(data)}"'
        assert "Authorization" not in store.last_signed_headers

    async def test_if_match_412_is_mapped_by_status(self, tmp_path, store) -> None:
        ref = ref_for(store, 1, PNG, vendor="s3", object_url=f"{store.url}/obj/{doc_id(1)}")
        store.objects[doc_id(1)].data = PNG + b"z"
        r = await go(make_task(tmp_path, store, [ref]))
        assert isinstance(r, C.Refused) and "send the push again" in r.message

    async def test_response_etag_mismatch_is_image_fetch_failed(self, tmp_path, store) -> None:
        """A store that ignores If-Match and serves other bytes must fail loudly (the response ETag decides)."""
        ref = ref_for(store, 1, PNG, vendor="s3", object_url=f"{store.url}/obj/{doc_id(1)}", ignore_if_match=True)
        store.objects[doc_id(1)].data = b"\xff\xd8\xff" + b"x" * (len(PNG) - 3)  # same size and a valid magic number: only the ETag tells
        r = await go(make_task(tmp_path, store, [ref]))
        assert isinstance(r, C.Refused) and r.reason == "IMAGE_FETCH_FAILED"

    @pytest.mark.parametrize("field", ["etag", "vendor"])
    async def test_download_refused_when_unbindable(self, tmp_path, store, field) -> None:
        ref = ref_for(store, 1, PNG)
        ref[field] = None
        r = await go(make_task(tmp_path, store, [ref]))
        assert isinstance(r, C.Refused) and r.reason == "IMAGE_FETCH_FAILED" and store.count("download") == 0


GOOD = "https://bucket.s3.eu-west-1.amazonaws.com/org/key.png"


class TestSignedUrlPinning:
    @pytest.mark.parametrize("signed", [
        "https://169.254.169.254/org/key.png", "https://evil.example.com/org/key.png",
        "https://bucket.s3.eu-west-1.amazonaws.com:8443/org/key.png", "https://bucket.s3.eu-west-1.amazonaws.com/org/other.png",
        "https://user:pw@bucket.s3.eu-west-1.amazonaws.com/org/key.png", "https://bucket.s3.eu-west-1.amazonaws.com\\@evil.com/org/key.png",
        "https://[fe80::1%25eth0]/org/key.png", "https://bucket.s3.eu-west-1.amazonaws.com./org/key.png",
        "https://BUCKET.s3.eu-west-1.amazonaws.com/org/key.png", "https://bücket.s3.eu-west-1.amazonaws.com/org/key.png",
        "http://bucket.s3.eu-west-1.amazonaws.com/org/key.png",
    ])
    def test_refused(self, signed) -> None:
        with pytest.raises(C.Refused):
            C.check_signed_url(signed, GOOD, [])

    def test_the_same_object_with_a_query_is_accepted(self) -> None:
        assert C.check_signed_url(GOOD + "?X-Amz-Signature=abc", GOOD, []) is False

    @pytest.mark.parametrize("host", ["169.254.169.254", "127.0.0.1", "localhost", "10.0.0.5", "192.168.1.1", "[fe80::1]"])
    def test_refused_when_stored_equals_signed_equals_bad_host(self, host) -> None:
        url = f"https://{host}/org/key.png"
        with pytest.raises(C.Refused):
            C.check_signed_url(url, url, [])

    def test_listed_origin_admits_a_loopback_only_through_the_test_seam(self) -> None:
        with pytest.raises(ValueError):
            C.parse_origin_list(["http://127.0.0.1:9000"])
        assert C.parse_origin_list(["http://127.0.0.1:9000"], allow_loopback_http=True)

    @pytest.mark.parametrize("entry", ["https://*.example.com", "https://minio.example.com/bucket", "https://u:p@minio.example.com", "http://minio.example.com"])
    def test_allowlist_validation(self, entry) -> None:
        with pytest.raises(ValueError):
            C.parse_origin_list([entry])

    def test_path_style_origin_path_must_end_with_the_stored_key_path(self) -> None:
        listed = C.parse_origin_list(["https://minio.example.com"])
        stored = "https://bucket.s3.eu-west-1.amazonaws.com/org/key.png"
        assert C.check_signed_url("https://minio.example.com/bucket/org/key.png", stored, listed) is True
        with pytest.raises(C.Refused):
            C.check_signed_url("https://minio.example.com/bucket/org/other.png", stored, listed)

    def test_strict_equality_for_unlisted_origins(self) -> None:
        with pytest.raises(C.Refused):
            C.check_signed_url("https://bucket.s3.eu-west-1.amazonaws.com/bucket/org/key.png", GOOD, [])

    def test_key_with_spaces_and_bang_quote_parens_star_matches_after_decoding(self) -> None:
        stored = "https://b.s3.amazonaws.com/o/a%20b!'()*.png"  # Node's encodeURIComponent leaves !'()* as is
        signed = "https://b.s3.amazonaws.com/o/a%20b%21%27%28%29%2A.png?X-Amz-Signature=1"  # the SDK presigner escapes them
        assert C.check_signed_url(signed, stored, []) is False

    def test_percent_5c_in_a_key_is_not_refused_as_a_backslash(self) -> None:
        url = "https://b.s3.amazonaws.com/o/a%5Cb.png"
        assert C.check_signed_url(url, url, []) is False

    async def test_non_public_peer_after_dns_is_refused_unless_listed(self) -> None:
        resolver = C.PinnedResolver(admit_non_public=False, override={"bucket.example.com": "127.0.0.1"})
        with pytest.raises(C.Refused):
            await resolver.resolve("bucket.example.com", 443)
        listed = C.PinnedResolver(admit_non_public=True, override={"bucket.example.com": "127.0.0.1"})
        assert (await listed.resolve("bucket.example.com", 443))[0]["hostname"] == "bucket.example.com"

    async def test_refused_with_zero_connections_and_logged_once_with_the_prefix(self, tmp_path, store, capfd) -> None:
        ref = ref_for(store, 1, PNG, vendor="s3", object_url="https://bucket.s3.eu-west-1.amazonaws.com/org/key.png")
        store.signed_override[doc_id(1)] = "https://169.254.169.254/org/key.png"
        r = await go(make_task(tmp_path, store, [ref], allow_loopback=False))
        assert isinstance(r, C.Refused) and store.count("signed") == 0
        assert capfd.readouterr().err.count(C.LOG_PREFIX) == 1

    async def test_proxy_environment_is_ignored(self, tmp_path, store, monkeypatch) -> None:
        for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
            monkeypatch.setenv(name, "http://127.0.0.1:9")  # nothing listens there
        monkeypatch.setenv("NO_PROXY", "")
        ref = ref_for(store, 1, PNG, vendor="s3", object_url=f"{store.url}/obj/{doc_id(1)}")
        assert not isinstance(await go(make_task(tmp_path, store, [ref])), C.Refused)

    async def test_redirect_not_followed(self, tmp_path, store) -> None:
        from aiohttp import web
        app = web.Application()
        hits = []

        async def redirect(request) -> None:
            hits.append("r")
            raise web.HTTPFound("/elsewhere")

        async def elsewhere(request) -> object:
            hits.append("e")
            return web.Response(body=PNG)

        app.router.add_get("/obj/{id}", redirect)
        app.router.add_get("/elsewhere", elsewhere)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            ref = ref_for(store, 1, PNG, vendor="s3", object_url=f"http://127.0.0.1:{port}/obj/{doc_id(1)}")
            store.signed_override[doc_id(1)] = ref["object_url"]
            r = await go(make_task(tmp_path, store, [ref]))
        finally:
            await runner.cleanup()
        assert isinstance(r, C.Refused) and hits == ["r"]

    async def test_a_signed_answer_over_8_kib_is_refused(self, tmp_path, store) -> None:
        ref = ref_for(store, 1, PNG, vendor="s3", object_url=f"{store.url}/obj/{doc_id(1)}")
        store.signed_pad = 9000  # a valid signed URL in an answer larger than 8 KiB
        r = await go(make_task(tmp_path, store, [ref]))
        assert isinstance(r, C.Refused) and store.count("signed") == 0


class TestResolverSeam:
    def test_non_loopback_override_raises(self) -> None:
        with pytest.raises(ValueError):
            M.Materialiser(resolver_override={"a.example.com": "10.0.0.1"})

    def test_none_resolves_normally(self) -> None:
        M.Materialiser(resolver_override=None)

    def test_production_runtime_passes_none(self) -> None:
        src = Path(M.__file__).with_name("api.py").read_text()
        calls = [n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "Materialiser"]
        assert calls, "api.py must construct the Materialiser"
        forbidden = {"resolver_override", "ca_file", "allow_loopback", "before_task"}
        assert all(not forbidden & {k.arg for k in c.keywords} for c in calls)

    def test_no_environment_variable_enables_the_seam(self) -> None:
        for path in (Path(M.__file__), CHILD_SRC):
            text = path.read_text()
            for name in ("resolver_override", "ca_file", "allow_loopback"):
                assert f'environ.get("{name.upper()}' not in text and "os.getenv" not in text

    async def test_override_can_name_a_port_and_a_suffix(self) -> None:
        """A virtual-hosted bucket host (``<bucket>.s3.<region>.amazonaws.com``) reaches a local server on its own port."""
        resolver = C.PinnedResolver(admit_non_public=True, override={"*.s3.us-east-1.amazonaws.com": "127.0.0.1:19143"})
        [hit] = await resolver.resolve("bucket-1.s3.us-east-1.amazonaws.com", 443)
        assert (hit["host"], hit["port"], hit["hostname"]) == ("127.0.0.1", 19143, "bucket-1.s3.us-east-1.amazonaws.com")
        exact = C.PinnedResolver(admit_non_public=True, override={"a.example.com": "127.0.0.1"})
        assert (await exact.resolve("a.example.com", 8443))[0]["port"] == 8443  # no port in the value: the URL's port stays
        assert C.PinnedResolver(admit_non_public=True, override={"*.example.com": "127.0.0.1:1"})._override
        with pytest.raises(ValueError):
            M.Materialiser(resolver_override={"*.example.com": "10.0.0.1:19143"})
        M.Materialiser(resolver_override={"*.example.com": "127.0.0.1:19143"})

    async def test_override_pins_the_address_keeps_host_for_tls_and_host_header(self, tmp_path, store) -> None:
        from aiohttp import web
        seen = []
        app = web.Application()

        async def obj(request) -> object:
            seen.append(request.headers["Host"])
            return web.Response(body=PNG, headers={"ETag": f'"{etag_of(PNG)}"'})

        app.router.add_get("/org/key.png", obj)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            url = f"http://bucket.example.com:{port}/org/key.png"
            ref = ref_for(store, 1, PNG, vendor="s3", object_url=url)
            store.signed_override[doc_id(1)] = url
            r = await go(make_task(tmp_path, store, [ref], resolver_override={"bucket.example.com": "127.0.0.1"}))
        finally:
            await runner.cleanup()
        assert not isinstance(r, C.Refused) and seen == [f"bucket.example.com:{port}"]


class TestLimits:
    def test_child_imports_nothing_else(self) -> None:
        tree = ast.parse(CHILD_SRC.read_text())
        mods = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                mods |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom):
                assert n.level == 0
                mods.add((n.module or "").split(".")[0])
        stdlib = set(__import__("sys").stdlib_module_names)
        assert mods - stdlib <= {"aiohttp", "yarl"}, mods

    def test_magic_table_equals_the_parents(self) -> None:
        assert C.MAGIC == I._MAGIC

    @needs_proc
    async def test_limits_are_set_in_the_child(self, tmp_path) -> None:
        pool = M.Materialiser(pool_size=1)
        slot = await pool._acquire()
        try:
            await asyncio.to_thread(pool._start, slot)
            limits = Path(f"/proc/{slot.proc.pid}/limits").read_text()
            by_name = {line[:25].strip(): line[25:].split() for line in limits.splitlines()[1:]}
            assert by_name["Max file size"][0] == str(C.FSIZE_LIMIT) and by_name["Max core file size"][0] == "0"
            assert by_name["Max address space"][0] != "unlimited"
        finally:
            pool._release(slot)
            await pool.close()


class TestFsize:
    def test_sigxfsz_is_ignored_in_the_child(self) -> None:
        src = CHILD_SRC.read_text()
        assert "SIGXFSZ, signal.SIG_IGN" in src

    async def test_efbig_is_413_resolved_child_survives_no_strike_callback(self, tmp_path, store) -> None:
        events = []
        pool = M.Materialiser(pool_size=1, allow_loopback=True, on_task_end=lambda o, c: events.append(o))
        refs = [ref_for(store, 1, PNG + b"x" * (15 << 20)), ref_for(store, 2, PNG + b"x" * (15 << 20))]
        task = make_task(tmp_path, store, refs, [2, 2], cap=200 << 20)  # the cap would allow it; only RLIMIT_FSIZE stops it
        try:
            with pytest.raises(PushRefusal) as e:
                await pool.run(task)
            assert (e.value.status, e.value.extra["limit"]) == (413, "resolved")
            ok = make_task(tmp_path, store, [ref_for(store, 3, PNG)])
            assert (await pool.run(ok)).size > 0 and pool.spawned == 1  # the same child served both
        finally:
            await pool.close()
        assert events == ["refused", "ok"]


class _SlowToReap:
    """A killed child that takes a while to be reaped (uninterruptible I/O); joining it blocks the calling thread."""

    exitcode = -9

    def __init__(self, reap_s: float) -> None:
        self.reap_s, self.killed, self.joined_on = reap_s, False, []

    def is_alive(self) -> bool:
        return not self.killed

    def kill(self) -> None:
        self.killed = True

    def join(self, timeout: float | None = None) -> None:
        self.joined_on.append(threading.get_ident())
        time.sleep(self.reap_s)


class _Conn:
    def __init__(self, *, polls: bool = False) -> None:
        self.polls = polls

    def send(self, _task) -> None:
        pass

    def poll(self, _timeout=None) -> bool:
        return self.polls

    def recv(self) -> NoReturn:
        raise EOFError

    def close(self) -> None:
        pass


class TestKillDoesNotBlockTheLoop:
    @staticmethod
    def _reaped_off_the_loop(proc: _SlowToReap) -> bool:
        """The child was joined, and never on the event loop's thread (where a slow reap would stall every request).
        Where the join ran is checked, not how long the loop stalled: a stall bound also measures the host's load."""
        return bool(proc.joined_on) and threading.get_ident() not in proc.joined_on

    async def test_an_overrun_reaps_the_child_off_the_event_loop(self, tmp_path, store) -> None:
        pool = M.Materialiser(pool_size=1, wall_budget_s=0.01, allow_loopback=True)
        proc = _SlowToReap(0)
        pool._slots[0].proc, pool._slots[0].conn = proc, _Conn(polls=False)

        async def run() -> None:
            with pytest.raises(PushRefusal):
                await pool.run(make_task(tmp_path, store, []))

        await run()
        assert proc.killed and self._reaped_off_the_loop(proc)

    async def test_a_dead_pipe_reaps_the_child_off_the_event_loop(self, tmp_path, store) -> None:
        pool = M.Materialiser(pool_size=1, allow_loopback=True)
        proc = _SlowToReap(0)
        pool._slots[0].proc, pool._slots[0].conn = proc, _Conn(polls=True)

        async def run() -> None:
            with pytest.raises(PushRefusal):
                await pool.run(make_task(tmp_path, store, []))

        await run()
        assert self._reaped_off_the_loop(proc)

    async def test_a_cancelled_push_kills_at_once_and_still_reaps_to_the_end(self, tmp_path, store) -> None:
        pool = M.Materialiser(pool_size=1, wall_budget_s=30, allow_loopback=True)
        proc = _SlowToReap(0.3)
        conn = _Conn(polls=False)
        conn.poll = lambda _t=None: time.sleep(0.2) or False  # type: ignore[method-assign]
        pool._slots[0].proc, pool._slots[0].conn = proc, conn
        task = asyncio.create_task(pool.run(make_task(tmp_path, store, [])))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert proc.killed and pool._slots[0].proc is None and not pool._slots[0].busy


class TestPool:
    async def test_sigkill_mid_task_is_503_nothing_stored_dir_gone_respawn(self, tmp_path, store) -> None:
        pool = M.Materialiser(pool_size=1, allow_loopback=True)
        ref = ref_for(store, 1, PNG)
        task = make_task(tmp_path, store, [ref])

        async def kill(_task) -> None:
            os.kill(pool._slots[0].proc.pid, 9)
            await asyncio.sleep(0.2)

        pool.before_task = kill
        try:
            with pytest.raises(PushRefusal) as e:
                await pool.run(task)
            assert e.value.status == 503
            pool.before_task = None
            pool.remove_dir(str(tmp_path / "work"))
            assert not (tmp_path / "work").exists()
            assert (await pool.run(make_task(tmp_path, store, [ref]))).size > 0 and pool.spawned == 2
        finally:
            await pool.close()

    async def test_sigkill_while_the_child_works_is_503_and_a_new_child_serves_the_next(self, tmp_path, store) -> None:
        from aiohttp import web
        app = web.Application()

        async def slow(request) -> web.Response:
            await asyncio.sleep(3)
            return web.Response()

        app.router.add_get("/api/v1/document/internal/{id}/download", slow)
        runner = web.AppRunner(app, shutdown_timeout=0.1)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        pool = M.Materialiser(pool_size=1, allow_loopback=True)
        try:
            task = make_task(tmp_path, store, [ref_for(store, 1, PNG)], storage_url=f"http://127.0.0.1:{port}")
            loop = asyncio.get_running_loop()
            loop.call_later(1.0, lambda: os.kill(pool._slots[0].proc.pid, 9))
            with pytest.raises(PushRefusal) as e:
                await pool.run(task)
            assert (e.value.status, e.value.reason) == (503, "PUSH_TIMEOUT")
            assert (await pool.run(make_task(tmp_path, store, [ref_for(store, 2, PNG)]))).size > 0 and pool.spawned == 2
        finally:
            await runner.cleanup()
            await pool.close()

    async def test_overrun_is_killed_and_replaced_and_reports_to_on_task_end(self, tmp_path, store) -> None:
        events = []
        pool = M.Materialiser(pool_size=1, wall_budget_s=1.5, allow_loopback=True, on_task_end=lambda o, c: events.append(o))
        from aiohttp import web
        app = web.Application()

        async def slow(request) -> None:
            await asyncio.sleep(4)

        app.router.add_get("/api/v1/document/internal/{id}/download", slow)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            task = make_task(tmp_path, store, [ref_for(store, 1, PNG)], storage_url=f"http://127.0.0.1:{port}", request_timeout_s=60)
            started = time.monotonic()
            with pytest.raises(PushRefusal) as e:
                await pool.run(task)
            assert (e.value.status, e.value.reason) == (503, "PUSH_TIMEOUT") and time.monotonic() - started < 10
            assert (await pool.run(make_task(tmp_path, store, [ref_for(store, 2, PNG)]))).size > 0
        finally:
            await runner.cleanup()
            await pool.close()
        assert events == ["overrun", "ok"]


# ---- accounting: strikes, suspect events, overruns --------------------------------------------------

class Heard:
    def __init__(self) -> None:
        self.names: list[str] = []

    def __call__(self, event: str, value: float = 0.0) -> None:
        self.names.append(event)


def kill_on_download(store: FakeStorage, pid: int, sig: int, only: str | None = None) -> None:
    """Kill ``pid`` with a real signal when its storage request arrives, so the child dies while it runs the task.

    A kill before the task is sent races the send: once the dead child's end of the pipe is closed the send fails,
    and that is a death before the task, not one caused by it.
    """
    async def kill(doc: str) -> None:
        if only is None or doc == only:
            store.on_download = None
            os.kill(pid, sig)
    store.on_download = kill


def crash_next(pool: M.Materialiser, store: FakeStorage, sig: int = 11) -> Callable[[dict], Awaitable[None]]:
    """A ``before_task`` that kills the child the task was just handed to, with a real signal, while it runs it."""
    async def hook(_task) -> None:
        kill_on_download(store, pool._slots[0].proc.pid, sig)
    return hook


class TestAccounting:
    async def test_a_real_crash_on_a_fresh_child_is_a_strike_and_a_replaced_child_serves_next(self, tmp_path, store) -> None:
        ends, heard = [], Heard()
        pool = M.Materialiser(pool_size=1, allow_loopback=True, on_task_end=lambda o, c: ends.append(o))
        pool.before_task = crash_next(pool, store)
        try:
            with pytest.raises(PushRefusal) as e:
                await pool.run(make_task(tmp_path, store, [ref_for(store, 1, PNG)]), account=heard)
            assert e.value.status == 503 and heard.names == ["strike"] and ends == ["crashed"]
            pool.before_task = None
            assert (await pool.run(make_task(tmp_path, store, [ref_for(store, 2, PNG)]))).size > 0
        finally:
            await pool.close()

    @pytest.mark.parametrize("sig", [6, 11, 7])
    async def test_every_crash_signal_counts(self, tmp_path, store, sig) -> None:
        heard = Heard()
        pool = M.Materialiser(pool_size=1, allow_loopback=True)
        pool.before_task = crash_next(pool, store, sig)
        try:
            with pytest.raises(PushRefusal):
                await pool.run(make_task(tmp_path, store, [ref_for(store, 1, PNG)]), account=heard)
            assert heard.names == ["strike"]
        finally:
            await pool.close()

    async def test_an_external_sigkill_is_nothing(self, tmp_path, store) -> None:
        heard = Heard()
        pool = M.Materialiser(pool_size=1, allow_loopback=True)
        pool.before_task = crash_next(pool, store, 9)
        try:
            with pytest.raises(PushRefusal):
                await pool.run(make_task(tmp_path, store, [ref_for(store, 1, PNG)]), account=heard)
            assert heard.names == []
        finally:
            await pool.close()

    async def test_a_reused_child_death_is_suspect_not_a_strike(self, tmp_path, store) -> None:
        heard = Heard()
        pool = M.Materialiser(pool_size=1, allow_loopback=True)
        try:
            await pool.run(make_task(tmp_path, store, [ref_for(store, 1, PNG)]))  # another caller's push: index 0
            pool.before_task = crash_next(pool, store)
            with pytest.raises(PushRefusal):
                await pool.run(make_task(tmp_path, store, [ref_for(store, 2, PNG)]), account=heard)
            assert heard.names == ["suspect"]
        finally:
            await pool.close()

    async def test_a_child_is_replaced_after_its_task_cap(self, tmp_path, store, monkeypatch) -> None:
        monkeypatch.setattr(M, "MAX_TASKS_PER_CHILD", 2)
        pool = M.Materialiser(pool_size=1, allow_loopback=True)
        try:
            for i in range(1, 4):
                await pool.run(make_task(tmp_path, store, [ref_for(store, i, PNG)]))
            assert pool.spawned == 2
        finally:
            await pool.close()

    async def test_the_attacker_who_warms_a_child_first_is_still_bounded_by_the_suspect_counter(self, tmp_path, store) -> None:
        """Benign push (index 0), then the crashing one (index 1) with a fresh key each time: never a strike, still refused."""
        from app.connectors.sources.localKB.push import ledger as L

        ledger, user = L.PushLedger(), ("org", "mallory")
        pool = M.Materialiser(pool_size=1, allow_loopback=True)
        try:
            for i in range(4):
                pool.before_task = None
                await pool.run(make_task(tmp_path, store, [ref_for(store, 1, PNG)]), account=ledger.accountant(user, f"r:warm{i}", L.IMAGE))
                pool.before_task = crash_next(pool, store)
                with pytest.raises(PushRefusal):
                    await pool.run(make_task(tmp_path, store, [ref_for(store, 2, PNG)]), account=ledger.accountant(user, f"r:crash{i}", L.IMAGE))
            with pytest.raises(PushRefusal) as e:
                ledger.check_images(user)
            assert e.value.reason == "PUSH_TIMEOUT"  # four suspect events; strikes alone (0) never suspended anyone
            assert not ledger._rows[user].strikes
        finally:
            await pool.close()

    async def test_an_honest_caller_whose_push_dies_on_a_reused_child_has_no_strike(self, tmp_path, store) -> None:
        from app.connectors.sources.localKB.push import ledger as L

        ledger, honest = L.PushLedger(), ("org", "honest")
        pool = M.Materialiser(pool_size=1, allow_loopback=True)
        try:
            await pool.run(make_task(tmp_path, store, [ref_for(store, 1, PNG)]))  # someone else warmed the child
            pool.before_task = crash_next(pool, store)
            with pytest.raises(PushRefusal):
                await pool.run(make_task(tmp_path, store, [ref_for(store, 2, PNG)]), account=ledger.accountant(honest, "r:mine", L.IMAGE))
            assert not ledger._rows[honest].strikes and len(ledger._rows[honest].suspects) == 1
        finally:
            await pool.close()

    async def _slow_server(self, seconds: float) -> tuple[object, int]:
        from aiohttp import web
        app = web.Application()

        async def slow(request) -> None:
            await asyncio.sleep(seconds)

        app.router.add_get("/api/v1/document/internal/{id}/download", slow)
        runner = web.AppRunner(app, shutdown_timeout=0.1)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        return runner, site._server.sockets[0].getsockname()[1]

    @needs_proc
    async def test_a_stalled_storage_overrun_is_not_a_strike_and_the_cpu_is_the_childs_own(self, tmp_path, store) -> None:
        ends, heard = [], Heard()
        runner, port = await self._slow_server(5)
        pool = M.Materialiser(pool_size=1, wall_budget_s=1.5, allow_loopback=True, on_task_end=lambda o, c: ends.append((o, c)))
        try:
            task = make_task(tmp_path, store, [ref_for(store, 1, PNG)], storage_url=f"http://127.0.0.1:{port}", request_timeout_s=60)
            with pytest.raises(PushRefusal):
                await pool.run(task, account=heard)
            assert heard.names == ["overrun"]
            outcome, cpu_s = ends[0]
            assert outcome == "overrun" and cpu_s is not None and cpu_s < 0.75, "a waiting child spends almost no CPU (and this is not the wall time)"
        finally:
            await runner.cleanup()
            await pool.close()

    async def test_a_cpu_bound_overrun_is_a_strike(self, tmp_path, store, monkeypatch) -> None:
        heard = Heard()
        runner, port = await self._slow_server(5)
        readings = iter([(0, 5), (900, 5)])  # 9 s of CPU in a 1.5 s window is more than half: busy
        monkeypatch.setattr(M.cpu, "_TICKS", 100)
        monkeypatch.setattr(M.cpu, "read", lambda pid, proc_root="/proc": next(readings, (900, 5)))
        pool = M.Materialiser(pool_size=1, wall_budget_s=1.5, allow_loopback=True)
        try:
            task = make_task(tmp_path, store, [ref_for(store, 1, PNG)], storage_url=f"http://127.0.0.1:{port}", request_timeout_s=60)
            with pytest.raises(PushRefusal):
                await pool.run(task, account=heard)
            assert heard.names == ["strike"]
        finally:
            await runner.cleanup()
            await pool.close()

    async def test_a_recycled_pid_gives_no_measurement_and_no_strike(self, tmp_path, store, monkeypatch) -> None:
        heard = Heard()
        runner, port = await self._slow_server(5)
        readings = iter([(0, 5), (900, 6)])  # another start time: not the same process
        monkeypatch.setattr(M.cpu, "_TICKS", 100)
        monkeypatch.setattr(M.cpu, "read", lambda pid, proc_root="/proc": next(readings, (900, 6)))
        pool = M.Materialiser(pool_size=1, wall_budget_s=1.5, allow_loopback=True)
        try:
            task = make_task(tmp_path, store, [ref_for(store, 1, PNG)], storage_url=f"http://127.0.0.1:{port}", request_timeout_s=60)
            with pytest.raises(PushRefusal):
                await pool.run(task, account=heard)
            assert heard.names == ["overrun"]
        finally:
            await runner.cleanup()
            await pool.close()

    async def test_efbig_and_a_full_spool_are_not_strikes(self, tmp_path, store) -> None:
        heard = Heard()
        pool = M.Materialiser(pool_size=1, allow_loopback=True)
        refs = [ref_for(store, 1, PNG + b"x" * (15 << 20)), ref_for(store, 2, PNG + b"x" * (15 << 20))]
        try:
            with pytest.raises(PushRefusal) as e:
                await pool.run(make_task(tmp_path, store, refs, [2, 2], cap=200 << 20), account=heard)
            assert e.value.extra["limit"] == "resolved" and heard.names == []
            if os.path.exists("/dev/full"):
                with pytest.raises(PushRefusal) as e:
                    await pool.run(make_task(tmp_path, store, [ref_for(store, 3, PNG)], out_path="/dev/full"), account=heard)
                assert (e.value.status, e.value.reason) == (503, "SPOOL_FULL") and heard.names == []
        finally:
            await pool.close()

    async def test_a_cancelled_task_is_neither_a_strike_nor_an_overrun(self, tmp_path, store) -> None:
        heard = Heard()
        runner, port = await self._slow_server(5)
        pool = M.Materialiser(pool_size=1, wall_budget_s=30, allow_loopback=True)
        try:
            task = make_task(tmp_path, store, [ref_for(store, 1, PNG)], storage_url=f"http://127.0.0.1:{port}", request_timeout_s=60)
            t = asyncio.create_task(pool.run(task, account=heard))
            await asyncio.sleep(1.0)
            t.cancel()
            with pytest.raises(asyncio.CancelledError):
                await t
            assert heard.names == []
        finally:
            await runner.cleanup()
            await pool.close()

    async def test_concurrent_verdicts_do_not_cross(self, tmp_path, store) -> None:
        crashed, fine = Heard(), Heard()
        pool = M.Materialiser(pool_size=2, allow_loopback=True)
        for name in ("crash", "fine"):
            (tmp_path / name).mkdir()

        async def hook(task) -> None:
            if task["work_dir"].endswith("crash/work"):
                kill_on_download(store, pool._slots[0].proc.pid, 11, only=doc_id(1))  # the crashing task took the first slot
                await asyncio.sleep(0.3)

        pool.before_task = hook
        try:
            bad = asyncio.create_task(pool.run(make_task(tmp_path / "crash", store, [ref_for(store, 1, PNG)]), account=crashed))
            await asyncio.sleep(0)
            good = asyncio.create_task(pool.run(make_task(tmp_path / "fine", store, [ref_for(store, 2, PNG)]), account=fine))
            results = await asyncio.gather(bad, good, return_exceptions=True)
            assert isinstance(results[0], PushRefusal) and results[1].size > 0
            assert crashed.names == ["strike"] and fine.names == []
        finally:
            await pool.close()


class TestChildDiskFull:
    @pytest.mark.skipif(not os.path.exists("/dev/full"), reason="not applicable on this platform: no /dev/full")
    async def test_a_full_disk_at_the_download_write_is_spool_full_not_a_fetch_failure(self, tmp_path, store) -> None:
        task = make_task(tmp_path, store, [ref_for(store, 1, PNG)])
        os.symlink("/dev/full", os.path.join(task["work_dir"], doc_id(1) + ".img"))
        r = await go(task)
        assert isinstance(r, C.Refused) and (r.status, r.reason) == (503, "SPOOL_FULL")

    @pytest.mark.skipif(not os.path.exists("/dev/full"), reason="not applicable on this platform: no /dev/full")
    async def test_a_full_disk_at_the_splice_write_is_spool_full(self, tmp_path, store) -> None:
        r = await go(make_task(tmp_path, store, [ref_for(store, 1, PNG)], out_path="/dev/full"))
        assert isinstance(r, C.Refused) and (r.status, r.reason) == (503, "SPOOL_FULL")

    async def test_make_dir_on_a_full_disk_is_spool_full(self, tmp_path, monkeypatch) -> None:
        import errno

        def failing(path, *a, **kw) -> NoReturn:
            raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr(os, "makedirs", failing)
        with pytest.raises(PushRefusal) as e:
            M.Materialiser(allow_loopback=True).make_dir(str(tmp_path))
        assert e.value.reason == "SPOOL_FULL"
