"""
存储阵列管理页面自动登录模块（基于 Playwright）

登录流程：
1. 打开管理页面 URL
2. 等待用户名输入框出现
3. 填写用户名和密码
4. 点击登录按钮
5. 等待登录成功标志，或检测是否出现首次登录修改密码页面
6. 如果需要修改密码，填写旧密码、新密码、确认密码，点击确认
7. 登录成功，保持浏览器窗口打开供用户操作

选择器由用户在主机配置中自行填写。
"""

import asyncio
from typing import Optional


# 保存已启动的浏览器实例，避免重复启动
_browsers: dict[str, dict] = {}


async def auto_login_storage(host_config: dict) -> dict:
    """
    自动登录存储阵列管理页面

    Args:
        host_config: 主机配置字典，需包含：
            - host: 主机 IP
            - mgmt_port: 管理页面端口
            - mgmt_username: 管理用户名
            - mgmt_password: 管理密码
            - pw_username_selector: 用户名输入框选择器
            - pw_password_selector: 密码输入框选择器
            - pw_login_btn_selector: 登录按钮选择器
            - pw_old_password_selector: 旧密码输入框选择器（可选）
            - pw_new_password_selector: 新密码输入框选择器（可选）
            - pw_confirm_password_selector: 确认密码输入框选择器（可选）
            - pw_confirm_btn_selector: 确认按钮选择器（可选）
            - pw_success_selector: 登录成功标志选择器（可选）
            - pw_headless: 是否无头模式（默认 False）

    Returns:
        dict: 登录结果，包含 status、message、url 等
    """
    host_id = host_config.get("id", "unknown")
    host = host_config["host"]
    port = host_config.get("mgmt_port", 8088)
    username = host_config.get("mgmt_username", "")
    password = host_config.get("mgmt_password", "")
    headless = host_config.get("pw_headless", False)

    # Playwright 延迟导入：打包的 EXE 为减小体积已排除该依赖，
    # 未安装时返回友好提示，不阻塞其余功能。
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {
            "status": "error",
            "message": "当前运行环境未安装 Playwright（打包版为减小体积已排除）。"
                       "请使用源码方式运行（python main.py），或 pip install playwright 后重试。",
        }

    url = f"https://{host}:{port}"

    # 选择器配置
    sel = {
        "username": host_config.get("pw_username_selector", ""),
        "password": host_config.get("pw_password_selector", ""),
        "login_btn": host_config.get("pw_login_btn_selector", ""),
        "old_password": host_config.get("pw_old_password_selector", ""),
        "new_password": host_config.get("pw_new_password_selector", ""),
        "confirm_password": host_config.get("pw_confirm_password_selector", ""),
        "confirm_btn": host_config.get("pw_confirm_btn_selector", ""),
        "success": host_config.get("pw_success_selector", ""),
    }

    # 检查必填选择器
    missing = [k for k, v in sel.items() if k in ("username", "password", "login_btn") and not v]
    if missing:
        return {
            "status": "error",
            "message": f"缺少必填选择器配置: {', '.join(missing)}。请在编辑主机时填写 Playwright 选择器。",
        }

    # 如果已有浏览器实例，先关闭
    if host_id in _browsers:
        try:
            await _browsers[host_id]["browser"].close()
        except Exception:
            pass
        del _browsers[host_id]

    try:
        playwright = await async_playwright().start()

        # 启动浏览器（有头模式，用户可以看到）
        browser = await playwright.chromium.launch(
            headless=headless,
            args=[
                "--ignore-certificate-errors",
                "--disable-blink-features=AutomationControlled",
                "--start-maximized",
            ],
        )

        context = await browser.new_context(
            ignore_https_errors=True,
            viewport=None,  # 使用最大化窗口
        )

        page = await context.new_page()

        print(f"[Playwright] 打开管理页面: {url}")
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)

        # 等待页面加载完成
        await page.wait_for_load_state("networkidle", timeout=15000)

        # 步骤1：等待用户名输入框出现
        print(f"[Playwright] 等待用户名输入框: {sel['username']}")
        await page.wait_for_selector(sel["username"], timeout=20000)

        # 步骤2：填写用户名
        await page.fill(sel["username"], username)
        print(f"[Playwright] 已填写用户名: {username}")

        # 步骤3：填写密码
        await page.fill(sel["password"], password)
        print("[Playwright] 已填写密码")

        # 步骤4：点击登录按钮
        await page.click(sel["login_btn"])
        print("[Playwright] 已点击登录按钮")

        # 步骤5：等待登录结果
        # 优先等待成功标志，如果配置了的话
        login_success = False
        need_change_password = False

        if sel["success"]:
            try:
                await page.wait_for_selector(sel["success"], timeout=15000)
                login_success = True
                print("[Playwright] 检测到登录成功标志")
            except Exception:
                print("[Playwright] 未检测到登录成功标志，检查是否需要修改密码")

        # 如果没有成功标志，或者等待超时，检查是否需要修改密码
        if not login_success and sel["old_password"] and sel["new_password"]:
            try:
                await page.wait_for_selector(sel["old_password"], timeout=8000)
                need_change_password = True
                print("[Playwright] 检测到首次登录修改密码页面")
            except Exception:
                # 既没有成功标志，也没有修改密码页面，可能登录已经成功
                login_success = True
                print("[Playwright] 未检测到修改密码页面，假设登录成功")

        # 步骤6：如果需要修改密码
        if need_change_password:
            print("[Playwright] 开始修改密码流程")
            # 填写旧密码
            await page.fill(sel["old_password"], password)
            # 填写新密码（使用管理密码作为新密码，用户可自行修改）
            await page.fill(sel["new_password"], password)
            # 填写确认密码
            if sel["confirm_password"]:
                await page.fill(sel["confirm_password"], password)
            # 点击确认按钮
            if sel["confirm_btn"]:
                await page.click(sel["confirm_btn"])
            print("[Playwright] 已提交修改密码")

            # 等待修改密码成功
            if sel["success"]:
                try:
                    await page.wait_for_selector(sel["success"], timeout=15000)
                    login_success = True
                    print("[Playwright] 修改密码后登录成功")
                except Exception:
                    login_success = True
                    print("[Playwright] 等待成功标志超时，假设成功")
            else:
                await asyncio.sleep(3)
                login_success = True

        # 保存浏览器实例
        _browsers[host_id] = {
            "playwright": playwright,
            "browser": browser,
            "context": context,
            "page": page,
            "url": url,
        }

        if login_success:
            return {
                "status": "success",
                "message": "登录成功，浏览器窗口已打开，你可以直接在浏览器中操作",
                "url": url,
            }
        else:
            return {
                "status": "warning",
                "message": "登录流程已执行，但未检测到明确的成功标志，请在浏览器窗口中确认登录状态",
                "url": url,
            }

    except Exception as e:
        # 出错时清理
        if host_id in _browsers:
            try:
                await _browsers[host_id]["browser"].close()
            except Exception:
                pass
            del _browsers[host_id]
        return {
            "status": "error",
            "message": f"自动登录失败: {str(e)}",
        }


async def close_browser(host_id: str) -> dict:
    """关闭指定主机的浏览器实例"""
    if host_id in _browsers:
        try:
            await _browsers[host_id]["browser"].close()
            await _browsers[host_id]["playwright"].stop()
        except Exception:
            pass
        del _browsers[host_id]
        return {"status": "success", "message": "浏览器已关闭"}
    return {"status": "warning", "message": "没有找到运行中的浏览器实例"}


def list_active_browsers() -> list[dict]:
    """列出所有运行中的浏览器实例"""
    return [
        {"host_id": hid, "url": info["url"]}
        for hid, info in _browsers.items()
    ]
