"""容器内实验：patchright 下 page.on("response") 事件是否触发？"""
import asyncio
from patchright.async_api import async_playwright


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page()

        hits = []
        page.on("response", lambda r: hits.append(r.url))

        cdp = await page.context.new_cdp_session(page)
        cdp_hits = []
        cdp.on("Network.responseReceived", lambda e: cdp_hits.append(e["response"]["url"]))
        await cdp.send("Network.enable")

        console_hits = []
        page.on("console", lambda m: console_hits.append(m.text))

        try:
            await page.goto("https://example.com", wait_until="load", timeout=30000)
        except Exception as e:
            print("goto err:", e)
        # console 输出测试
        try:
            await page.evaluate("() => { console.log('AUDIT_TEST_CONSOLE_OK') }")
        except Exception as e:
            print("eval err:", e)
        await asyncio.sleep(2)

        print("page.on('response') events:", len(hits))
        print("cdp Network.responseReceived events:", len(cdp_hits))
        print("page.on('console') events:", len(console_hits))
        if hits:
            print("sample response url:", hits[0][:80])
        if console_hits:
            print("console sample:", console_hits[:3])
        await browser.close()


asyncio.run(main())
