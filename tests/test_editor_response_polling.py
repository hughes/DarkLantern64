"""Deterministic file-sharing recovery without launching an editor."""
import errno
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools import verify_project_editor as editor

ROOT = Path(__file__).resolve().parents[1]


class Clock:
    def __init__(self):
        self.now = 0.0
        self.delays = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.delays.append(seconds)
        self.now += seconds


def windows_error(code):
    error = PermissionError(errno.EACCES, "fixture sharing error")
    error.winerror = code
    return error


class ResponsePollingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="editor-response-", dir=ROOT / "build")
        self.addCleanup(temporary.cleanup)
        self.queue = Path(temporary.name)
        self.process = Mock(pid=42)
        self.process.poll.return_value = None
        self.client = editor.EditorClient(self.queue, self.process)
        self.clock = Clock()
        self.response = self.queue / "response.json"
        for name, replacement in (("monotonic", self.clock.monotonic), ("sleep", self.clock.sleep)):
            mocked = patch.object(editor.time, name, replacement)
            mocked.start(); self.addCleanup(mocked.stop)

    def test_sharing_retries_submit_exactly_one_command(self):
        calls = []
        def read(path):
            calls.append(path)
            if len(calls) <= 3:
                raise windows_error((5, 32, 33)[len(calls)-1])
            return json.dumps(dict(id=path.stem, ok=True, result={"complete": True})).encode()
        with patch.object(Path, "exists", return_value=True), patch.object(Path, "read_bytes", autospec=True, side_effect=read):
            result = self.client.request("save")
        self.assertEqual(result, {"complete": True})
        requests = list((self.queue / "requests").glob("*.json"))
        self.assertEqual(len(requests), 1)
        self.assertEqual(json.loads(requests[0].read_bytes())["id"], calls[0].stem)
        self.assertEqual(len(set(calls)), 1)
        self.assertEqual(self.process.poll.call_count, 3)
        self.assertAlmostEqual(self.clock.now, .3)

    def test_persistent_denial_keeps_original_error_and_two_second_budget(self):
        error = windows_error(32)
        with patch.object(Path, "read_bytes", side_effect=error):
            with self.assertRaises(PermissionError) as raised:
                editor._read_response_bytes(self.response, deadline=90, alive=self.client.alive)
        self.assertIs(raised.exception, error)
        self.assertAlmostEqual(self.clock.now, 2)
        self.assertTrue(all(0 < delay <= .1 for delay in self.clock.delays))

    def test_request_deadline_shortens_sharing_budget(self):
        error = windows_error(33)
        with patch.object(Path, "read_bytes", side_effect=error):
            with self.assertRaises(PermissionError) as raised:
                editor._read_response_bytes(self.response, deadline=.25, alive=self.client.alive)
        self.assertIs(raised.exception, error)
        self.assertAlmostEqual(self.clock.now, .25)

    def test_owned_editor_exit_interrupts_retry(self):
        self.process.poll.return_value = 7
        with patch.object(Path, "read_bytes", side_effect=windows_error(5)):
            with self.assertRaisesRegex(AssertionError, "Owned editor exited early with code 7"):
                editor._read_response_bytes(self.response, deadline=90, alive=self.client.alive)
        self.assertEqual(self.clock.delays, [])

    def test_other_io_errors_propagate_immediately(self):
        for error in (windows_error(53), FileNotFoundError(errno.ENOENT, "missing"), OSError(errno.EIO, "broken disk")):
            with self.subTest(error=error), patch.object(Path, "read_bytes", side_effect=error):
                with self.assertRaises(OSError) as raised:
                    editor._read_response_bytes(self.response, deadline=90, alive=self.client.alive)
                self.assertIs(raised.exception, error)
        self.assertEqual(self.clock.delays, [])

    def test_crt_errno_requires_fresh_native_classification(self):
        error = PermissionError(errno.EACCES, "Windows CRT omitted winerror")
        with patch.object(editor.os, "name", "nt"):
            for code in (5, 32, 33, 0, 53):
                with self.subTest(code=code), patch.object(editor, "_native_response_read_error", return_value=code) as probe:
                    self.assertEqual(editor._response_read_retryable(error, self.response), code in (0, 5, 32, 33))
                    probe.assert_called_once_with(self.response)
        with patch.object(editor.os, "name", "posix"), patch.object(editor, "_native_response_read_error") as probe:
            self.assertFalse(editor._response_read_retryable(error, self.response))
            probe.assert_not_called()

    def test_bad_json_and_wrong_response_id_are_not_retried(self):
        for body, exception in ((b"{", json.JSONDecodeError), (b'{"id":"wrong","ok":true,"result":{}}', AssertionError)):
            with self.subTest(body=body), patch.object(Path, "exists", return_value=True), patch.object(Path, "read_bytes", return_value=body) as read:
                with self.assertRaises(exception):
                    self.client.request("inspect")
                read.assert_called_once()
        self.assertEqual(self.clock.delays, [])

    def test_completed_quit_response_remains_readable_after_owned_exit(self):
        self.process.poll.return_value = 0
        def read(path):
            return json.dumps(dict(id=path.stem, ok=True, result={"quitting": True})).encode()
        with patch.object(Path, "exists", return_value=True), patch.object(Path, "read_bytes", autospec=True, side_effect=read):
            self.assertEqual(self.client.request("quit"), {"quitting": True})
        self.process.poll.assert_not_called()
        self.assertEqual(self.clock.delays, [])

    def test_invalid_session_data_is_not_hidden_until_timeout(self):
        with patch.object(Path, "exists", return_value=True), patch.object(Path, "read_bytes", return_value=b"{") as read:
            with self.assertRaises(json.JSONDecodeError):
                self.client.wait_session()
            read.assert_called_once()
        self.assertEqual(self.clock.delays, [])

    def test_session_read_retries_and_retains_owned_pid_check(self):
        body = json.dumps(dict(pid=42, project_editor=True)).encode()
        with patch.object(Path, "exists", return_value=True), patch.object(Path, "read_bytes", side_effect=[windows_error(32), body]):
            self.assertEqual(self.client.wait_session()["pid"], 42)
        self.assertEqual(self.process.poll.call_count, 2)
        self.assertAlmostEqual(self.clock.now, .1)


if __name__ == "__main__":
    unittest.main()
