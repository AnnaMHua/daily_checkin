import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "chrome_daily.py"

spec = importlib.util.spec_from_file_location("chrome_daily", SCRIPT_PATH)
chrome_daily = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(chrome_daily)


class FakeProcess:
    def __init__(self) -> None:
        self.terminated = False
        self.killed = False
        self.wait_timeouts = []
        self._alive = True

    def poll(self):
        return None if self._alive else 0

    def terminate(self) -> None:
        self.terminated = True
        self._alive = False

    def kill(self) -> None:
        self.killed = True
        self._alive = False

    def wait(self, timeout=None):
        self.wait_timeouts.append(timeout)
        return 0


class FakeWebSocket:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeBrowserWebSocket:
    requests = []
    closed = False

    def __init__(self, url, timeout=10.0) -> None:
        self.url = url
        self.timeout = timeout

    def request(self, method, params=None, timeout=10.0):
        self.requests.append((method, params, timeout))
        return {}

    def close(self) -> None:
        type(self).closed = True


class ChromeControllerTests(unittest.TestCase):
    def test_close_opened_targets_also_closes_launched_chrome_process(self):
        controller = chrome_daily.CdpChromeController()
        controller.opened_target_ids = ["tab-a", "tab-b"]
        controller.ws = FakeWebSocket()
        controller.target_id = "tab-b"
        controller.chrome_process = FakeProcess()

        closed_urls = []
        logs = []

        original_http_text = chrome_daily.http_text
        original_log = chrome_daily.log
        try:
            chrome_daily.http_text = lambda url, timeout=3.0: closed_urls.append((url, timeout))
            chrome_daily.log = logs.append

            closed = controller.close_opened_targets()
        finally:
            chrome_daily.http_text = original_http_text
            chrome_daily.log = original_log

        self.assertEqual(closed, 2)
        self.assertEqual(
            closed_urls,
            [
                ("/json/close/tab-b", 3.0),
                ("/json/close/tab-a", 3.0),
            ],
        )
        self.assertTrue(controller.ws is None)
        self.assertIsNone(controller.target_id)
        self.assertEqual(controller.opened_target_ids, [])
        self.assertTrue(controller.chrome_process is None)
        self.assertTrue(any("Closed the Chrome process launched by this run." in msg for msg in logs))

    def test_close_opened_targets_closes_browser_through_cdp_when_launched_by_run(self):
        controller = chrome_daily.CdpChromeController()
        controller.should_close_browser = True

        logs = []
        FakeBrowserWebSocket.requests = []
        FakeBrowserWebSocket.closed = False

        original_http_json = chrome_daily.http_json
        original_http_text = chrome_daily.http_text
        original_ws = chrome_daily.CdpWebSocket
        original_log = chrome_daily.log
        try:
            chrome_daily.http_json = lambda path, timeout=2.0, method="GET": {
                "webSocketDebuggerUrl": "ws://127.0.0.1:9223/devtools/browser/test"
            }
            chrome_daily.http_text = lambda url, timeout=3.0: ""
            chrome_daily.CdpWebSocket = FakeBrowserWebSocket
            chrome_daily.log = logs.append

            closed = controller.close_opened_targets()
        finally:
            chrome_daily.http_json = original_http_json
            chrome_daily.http_text = original_http_text
            chrome_daily.CdpWebSocket = original_ws
            chrome_daily.log = original_log

        self.assertEqual(closed, 0)
        self.assertFalse(controller.should_close_browser)
        self.assertEqual(FakeBrowserWebSocket.requests, [("Browser.close", None, 3.0)])
        self.assertTrue(FakeBrowserWebSocket.closed)
        self.assertTrue(any("Closed the Chrome browser launched by this run." in msg for msg in logs))


if __name__ == "__main__":
    unittest.main()
