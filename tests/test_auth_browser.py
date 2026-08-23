from unittest.mock import MagicMock, patch

import pytest

from boss_agent_cli.auth.browser import (
	HOME_URL,
	LOGIN_PAGE_URL,
	_NAV_TIMEOUT_MS,
	_NETWORKIDLE_GRACE_MS,
	_find_zhilian_recruiter_page,
	_is_authenticated_zhipin_landing_url,
	_is_zhilian_url,
	_stoken_from_request_url,
	login_via_cdp,
	login_via_browser,
	refresh_stoken,
)


def _mock_playwright_context(mock_browser: MagicMock) -> MagicMock:
	mock_chromium = MagicMock()
	mock_chromium.launch.return_value = mock_browser
	mock_playwright = MagicMock()
	mock_playwright.chromium = mock_chromium
	mock_context_manager = MagicMock()
	mock_context_manager.__enter__ = MagicMock(return_value=mock_playwright)
	mock_context_manager.__exit__ = MagicMock(return_value=False)
	return mock_context_manager


def _mock_cdp_playwright(mock_context: MagicMock) -> tuple[MagicMock, MagicMock, MagicMock]:
	mock_page = MagicMock()
	mock_context.new_page.return_value = mock_page

	mock_browser = MagicMock()
	mock_browser.contexts = [mock_context]

	mock_playwright = MagicMock()
	mock_playwright.chromium.connect_over_cdp.return_value = mock_browser

	mock_launcher = MagicMock()
	mock_launcher.start.return_value = mock_playwright
	return mock_launcher, mock_playwright, mock_page


class _UrlPage:
	def __init__(self, url: str) -> None:
		self.url = url


def test_zhilian_url_host_validation_uses_exact_hostname() -> None:
	assert _is_zhilian_url("https://zhaopin.com/")
	assert _is_zhilian_url("https://RD6.ZHAOPIN.COM./app/im")
	assert not _is_zhilian_url("https://rd6.zhaopin.com.evil.example/app/im")
	assert not _is_zhilian_url("https://evil.example/app/im?next=https://rd6.zhaopin.com/app/im")
	assert not _is_zhilian_url("not-a-url-with-zhaopin.com")


def test_find_zhilian_recruiter_page_rejects_embedded_hostname() -> None:
	fake_chat = _UrlPage("https://rd6.zhaopin.com.evil.example/app/im")
	fake_recommend = _UrlPage("https://evil.example/app/recommend?next=https://rd6.zhaopin.com/app/im")
	valid_page = _UrlPage("https://rd6.zhaopin.com/profile")

	selected = _find_zhilian_recruiter_page([fake_chat, fake_recommend, valid_page])

	assert selected is valid_page


def test_stoken_capture_accepts_only_genuine_zhipin_requests() -> None:
	assert (
		_stoken_from_request_url(
			"https://www.zhipin.com/wapi/zpgeek/search/joblist.json?__zp_stoken__=request-token"
		)
		== "request-token"
	)
	assert _stoken_from_request_url("https://zhipin.com.evil.example/?__zp_stoken__=stolen") == ""
	assert _stoken_from_request_url("https://evil.example/?next=zhipin.com&__zp_stoken__=stolen") == ""


def test_authenticated_zhipin_landing_requires_same_site_non_login_path() -> None:
	assert _is_authenticated_zhipin_landing_url("https://www.zhipin.com/")
	assert _is_authenticated_zhipin_landing_url("https://www.zhipin.com/web/geek/job")
	assert not _is_authenticated_zhipin_landing_url("https://www.zhipin.com/web/user/?ka=header-login")
	assert not _is_authenticated_zhipin_landing_url("https://zhipin.com.evil.example/")


@patch("boss_agent_cli.auth.browser.probe_cdp", return_value="ws://localhost/devtools/browser")
@patch("boss_agent_cli.auth.browser.time.sleep", return_value=None)
def test_login_via_cdp_stops_playwright_on_timeout(mock_sleep, mock_probe_cdp):
	mock_context = MagicMock()
	mock_context.cookies.return_value = []
	mock_launcher, mock_playwright, mock_page = _mock_cdp_playwright(mock_context)

	with patch("boss_agent_cli.auth.browser.sync_playwright", return_value=mock_launcher):
		with pytest.raises(TimeoutError):
			login_via_cdp(timeout=1)

	mock_page.close.assert_called_once()
	mock_playwright.stop.assert_called_once()


@patch("boss_agent_cli.auth.browser.probe_cdp", return_value="ws://localhost/devtools/browser")
@patch("boss_agent_cli.auth.browser.time.sleep", return_value=None)
def test_login_via_cdp_stops_playwright_when_user_agent_extraction_fails(mock_sleep, mock_probe_cdp):
	mock_context = MagicMock()
	mock_context.cookies.side_effect = [
		[{"name": "wt2", "value": "token", "domain": ".zhipin.com"}],
		[{"name": "wt2", "value": "token", "domain": ".zhipin.com"}],
	]
	mock_launcher, mock_playwright, mock_page = _mock_cdp_playwright(mock_context)
	mock_page.evaluate.side_effect = RuntimeError("user agent unavailable")

	with patch("boss_agent_cli.auth.browser.sync_playwright", return_value=mock_launcher):
		with pytest.raises(RuntimeError, match="user agent unavailable"):
			login_via_cdp(timeout=1)

	mock_page.close.assert_called_once()
	mock_playwright.stop.assert_called_once()


@patch("boss_agent_cli.auth.browser.probe_cdp", return_value="ws://localhost/devtools/browser")
@patch("boss_agent_cli.auth.browser.time.sleep", return_value=None)
def test_login_via_cdp_captures_stoken_without_post_login_evaluation(mock_sleep, mock_probe_cdp):
	mock_context = MagicMock()
	mock_context.cookies.return_value = [
		{"name": "wt2", "value": "token", "domain": ".zhipin.com"},
	]
	mock_launcher, mock_playwright, mock_page = _mock_cdp_playwright(mock_context)
	navigated_home = False

	def goto(url, **kwargs):
		nonlocal navigated_home
		if url == HOME_URL:
			navigated_home = True

	def evaluate(script):
		if navigated_home:
			raise RuntimeError("post-login page evaluation stalled")
		return "UA"

	def on(event, callback):
		if event == "request":
			callback(MagicMock(url="https://www.zhipin.com/wapi/zpgeek/job?__zp_stoken__=cdp-token"))

	mock_page.goto.side_effect = goto
	mock_page.evaluate.side_effect = evaluate
	mock_page.on.side_effect = on

	with patch("boss_agent_cli.auth.browser.sync_playwright", return_value=mock_launcher):
		result = login_via_cdp(timeout=1)

	assert result["stoken"] == "cdp-token"
	mock_page.evaluate.assert_called_once_with("navigator.userAgent")
	mock_page.close.assert_called_once()
	mock_playwright.stop.assert_called_once()


@patch("boss_agent_cli.auth.browser.probe_cdp", return_value="ws://localhost/devtools/browser")
@patch("boss_agent_cli.auth.browser.time.sleep", return_value=None)
def test_zhilian_login_via_cdp_reuses_recruiter_page(mock_sleep, mock_probe_cdp):
	mock_page = MagicMock()
	mock_page.url = "https://rd6.zhaopin.com/app/im?sessionId=abc"
	mock_page.evaluate.return_value = "UA"
	mock_context = MagicMock()
	mock_context.pages = [mock_page]
	mock_context.cookies.return_value = [
		{"name": "at", "value": "access", "domain": ".zhaopin.com"},
		{"name": "rt", "value": "refresh", "domain": ".zhaopin.com"},
		{"name": "x-zp-client-id", "value": "cid", "domain": ".zhaopin.com"},
	]
	mock_launcher, mock_playwright, _mock_new_page = _mock_cdp_playwright(mock_context)

	with patch("boss_agent_cli.auth.browser.sync_playwright", return_value=mock_launcher):
		result = login_via_cdp(timeout=1, platform="zhilian")

	assert result["cookies"]["at"] == "access"
	assert result["x_zp_client_id"] == "cid"
	mock_context.new_page.assert_not_called()
	mock_page.goto.assert_not_called()
	mock_page.close.assert_not_called()
	mock_playwright.stop.assert_called_once()


@patch("boss_agent_cli.auth.browser.time.sleep", return_value=None)
def test_login_via_browser_tolerates_networkidle_timeout(mock_sleep):
	mock_page = MagicMock()
	mock_page.wait_for_load_state.side_effect = Exception("Timeout 30000ms exceeded")
	mock_page.evaluate.return_value = "UA"

	mock_context = MagicMock()
	mock_context.new_page.return_value = mock_page
	mock_context.cookies.side_effect = [
		[{"name": "wt2", "value": "token", "domain": ".zhipin.com"}],
		[
			{"name": "wt2", "value": "token", "domain": ".zhipin.com"},
			{"name": "__zp_stoken__", "value": "fresh-stoken", "domain": ".zhipin.com"},
		],
	]

	mock_browser = MagicMock()
	mock_browser.new_context.return_value = mock_context

	with patch("boss_agent_cli.auth.browser.sync_playwright", return_value=_mock_playwright_context(mock_browser)):
		result = login_via_browser(timeout=2, platform="zhipin")

	assert result["stoken"] == "fresh-stoken"
	assert result["user_agent"] == "UA"
	mock_browser.new_context.assert_called_once()
	mock_page.goto.assert_any_call(LOGIN_PAGE_URL, wait_until="domcontentloaded")
	mock_page.goto.assert_any_call(HOME_URL, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
	mock_page.wait_for_load_state.assert_called_once_with("networkidle", timeout=_NETWORKIDLE_GRACE_MS)
	mock_browser.close.assert_called_once()


@patch("boss_agent_cli.auth.browser.time.sleep", return_value=None)
def test_login_via_browser_does_not_evaluate_a_page_after_post_login_navigation(mock_sleep):
	mock_page = MagicMock()
	navigated_home = False

	def on(event, callback):
		if event == "request":
			callback(MagicMock(url="https://www.zhipin.com/wapi/zpgeek/search/joblist.json?__zp_stoken__=request-stoken"))

	def goto(url, **kwargs):
		nonlocal navigated_home
		if url == HOME_URL:
			navigated_home = True

	def evaluate(script):
		if navigated_home:
			raise RuntimeError("post-login page evaluation stalled")
		return "UA"

	mock_page.on.side_effect = on
	mock_page.goto.side_effect = goto
	mock_page.evaluate.side_effect = evaluate
	mock_context = MagicMock()
	mock_context.new_page.return_value = mock_page
	mock_context.cookies.side_effect = [
		[{"name": "wt2", "value": "token", "domain": ".zhipin.com"}],
		[{"name": "wt2", "value": "token", "domain": ".zhipin.com"}],
	]
	mock_browser = MagicMock()
	mock_browser.new_context.return_value = mock_context

	with patch("boss_agent_cli.auth.browser.sync_playwright", return_value=_mock_playwright_context(mock_browser)):
		result = login_via_browser(timeout=2, platform="zhipin")

	assert result["user_agent"] == "UA"
	assert result["stoken"] == "request-stoken"
	mock_page.evaluate.assert_called_once_with("navigator.userAgent")
	mock_browser.close.assert_called_once()


@patch("boss_agent_cli.auth.browser.time.sleep", return_value=None)
def test_login_via_browser_rejects_incomplete_zhipin_credentials(mock_sleep):
	mock_page = MagicMock()
	mock_page.evaluate.return_value = "UA"
	mock_context = MagicMock()
	mock_context.new_page.return_value = mock_page
	mock_context.cookies.return_value = [
		{"name": "wt2", "value": "token", "domain": ".zhipin.com"},
	]
	mock_browser = MagicMock()
	mock_browser.new_context.return_value = mock_context

	with patch("boss_agent_cli.auth.browser.sync_playwright", return_value=_mock_playwright_context(mock_browser)):
		with pytest.raises(RuntimeError, match="动态令牌"):
			login_via_browser(timeout=2, platform="zhipin")

	mock_page.evaluate.assert_called_once_with("navigator.userAgent")
	mock_browser.close.assert_called_once()


@patch("boss_agent_cli.auth.browser._extract_stoken", return_value="fresh-stoken")
def test_refresh_stoken_tolerates_networkidle_timeout(mock_extract_stoken):
	mock_page = MagicMock()
	mock_page.wait_for_load_state.side_effect = Exception("Timeout 30000ms exceeded")

	mock_context = MagicMock()
	mock_context.new_page.return_value = mock_page

	mock_browser = MagicMock()
	mock_browser.new_context.return_value = mock_context

	with patch("boss_agent_cli.auth.browser.sync_playwright", return_value=_mock_playwright_context(mock_browser)):
		result = refresh_stoken({"wt2": "cookie"}, "UA")

	assert result == "fresh-stoken"
	mock_browser.new_context.assert_called_once_with(user_agent="UA")
	mock_context.add_cookies.assert_called_once()
	mock_page.goto.assert_called_once_with(HOME_URL, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
	mock_page.wait_for_load_state.assert_called_once_with("networkidle", timeout=_NETWORKIDLE_GRACE_MS)
	mock_extract_stoken.assert_called_once_with(mock_page)
	mock_browser.close.assert_called_once()
