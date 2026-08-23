from __future__ import annotations

import os
from pathlib import Path

import pytest
from patchright.sync_api import expect, sync_playwright

from boss_agent_cli.application import (
	Application,
	InboundApplicant,
	JobSearchBatch,
	JobSourceDetail,
	JobSummary,
	PlatformSessionState,
	RecruitingApplicantBatch,
	RecruitingOpening,
	RecruitingProspectContext,
	WorkspaceKind,
)
from boss_agent_cli.application.testing import FakeBossAdapter, InMemoryCredentialStore
from boss_agent_cli.web.auth import StartupAuthenticator
from boss_agent_cli.web.server import LocalWebServer
from boss_agent_cli.web.workspace import WorkspaceRegistry


pytestmark = pytest.mark.skipif(
	os.environ.get("BOSS_RUN_BROWSER_TESTS") != "1",
	reason="set BOSS_RUN_BROWSER_TESTS=1 after installing Patchright Chromium",
)


def test_production_browser_completes_both_core_journeys_without_persistent_browser_state(tmp_path: Path) -> None:
	job = JobSummary("job-browser", "Python 后端工程师", "示例科技", "上海", "20-40K", "3-5年", "本科")
	opening = RecruitingOpening("opening-browser", "后端工程师", "招聘中")
	applicant = InboundApplicant("prospect-browser", "招聘对象甲", "5 年 Python 经验")
	boss = FakeBossAdapter(
		search_batches=(JobSearchBatch((job,), 100),),
		details={"job-browser": JobSourceDetail(job, "负责 Python 服务", recruiter="招聘者甲")},
		openings=(opening,),
		applicant_batches=(RecruitingApplicantBatch((applicant,), 100),),
		prospect_contexts={
			"prospect-browser": RecruitingProspectContext(
				applicant,
				resume_text="仅在内存显示的简历",
				chat_messages=("候选人：您好",),
				contact_details=("联系方式已保护",),
			),
		},
	)
	run_ids = iter(("job-browser-run", "recruiting-browser-run"))
	intent_ids = iter(("job-browser-intent", "recruiting-browser-intent"))
	authenticator = StartupAuthenticator(
		bootstrap_token="browser-bootstrap",
		local_session_id="browser-session",
		session_token_factory=lambda: "browser-session-token",
	)
	application = Application(
		workspace_store=WorkspaceRegistry(tmp_path / "data", local_session_id="browser-session"),
		credential_store=InMemoryCredentialStore(
			{
				WorkspaceKind.JOB_SEEKING: PlatformSessionState.CONNECTED,
				WorkspaceKind.RECRUITING: PlatformSessionState.CONNECTED,
			}
		),
		boss=boss,
		run_id_factory=lambda: next(run_ids),
		intent_id_factory=lambda: next(intent_ids),
	)
	server = LocalWebServer(authenticator=authenticator, application=application)
	server.start()
	console_errors: list[str] = []
	try:
		with sync_playwright() as playwright:
			browser = playwright.chromium.launch(headless=True)
			page = browser.new_page(viewport={"width": 720, "height": 900})
			page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
			page.goto(server.launch_url)

			expect(page.get_by_role("heading", name="BOSS 本地工作台")).to_be_visible()
			assert page.url == f"{server.origin}/"
			page.get_by_label("目标", exact=True).fill("寻找后端岗位")
			page.get_by_label("关键词", exact=True).fill("Python")
			page.get_by_role("button", name="保存目标").press("Enter")
			start_search = page.get_by_role("button", name="开始只读搜索")
			expect(start_search).to_be_enabled()
			start_search.click()
			expect(page.get_by_role("heading", name="1 个职位")).to_be_visible()
			page.get_by_role("button", name="查看来源事实").click()
			expect(page.get_by_text("平台来源原文")).to_be_visible()
			page.get_by_role("button", name="加入收藏").click()
			expect(page.get_by_role("heading", name="1 个已收藏职位")).to_be_visible()
			page.get_by_label("招呼内容").fill("您好，希望进一步沟通。")
			page.get_by_role("button", name="准备发送招呼").click()
			expect(page.get_by_role("heading", name="发送前确认")).to_be_visible()
			assert boss.greeting_calls == []
			page.get_by_role("button", name="确认并发送一次").click()
			expect(page.get_by_text("发送成功", exact=True)).to_be_visible()
			assert boss.greeting_calls == [("job-browser", "您好，希望进一步沟通。")]

			page.get_by_role("button", name="切换到招聘工作区").click()
			expect(page.get_by_role("button", name="招聘工作区（当前）")).to_be_visible()
			page.get_by_role("button", name="读取招聘职位").click()
			expect(page.get_by_role("heading", name="后端工程师", exact=True)).to_be_visible()
			page.get_by_role("button", name="选择职位").click()
			page.get_by_role("button", name="读取新招呼与投递").click()
			expect(page.get_by_role("heading", name="招聘对象甲")).to_be_visible()
			page.get_by_role("button", name="明确查看简历与沟通").click()
			expect(page.get_by_text("仅在内存显示的简历")).to_be_visible()
			page.get_by_label("回复内容").fill("您好，感谢您的投递。")
			page.get_by_role("button", name="准备确认回复").click()
			expect(page.get_by_role("heading", name="发送前确认")).to_be_visible()
			assert boss.recruiting_reply_calls == []
			page.get_by_role("button", name="确认并发送一次").click()
			expect(page.get_by_text("发送成功", exact=True)).to_be_visible()
			assert boss.recruiting_reply_calls == [("prospect-browser", "您好，感谢您的投递。")]

			browser_state = page.evaluate(
				"""async () => ({
					localStorage: localStorage.length,
					sessionStorage: sessionStorage.length,
					indexedDB: (await indexedDB.databases()).length,
					overflow: document.documentElement.scrollWidth > window.innerWidth,
				})"""
			)
			assert browser_state == {
				"localStorage": 0,
				"sessionStorage": 0,
				"indexedDB": 0,
				"overflow": False,
			}
			browser.close()
	finally:
		server.close()

	assert console_errors == []
