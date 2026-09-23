"""探针2：无 Network.enable 时 page.on("response") 是否触发？
分两阶段：A) 纯 page.on，无任何 CDP；B) 建 CDP session + Network.enable 后再导航。"""
import asyncio
from patchright.async_api import async_playwright


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page()

        # ---- 阶段 A：无 CDP，纯 page.on ----
        hits_a = []
        reqs_a = []
        page.on("response", lambda r: hits_a.append(r.url))
        page.on("request", lambda r: reqs_a.append(r.url))
        try:
            await page.goto("https://example.com", wait_until="load", timeout=30000)
        except Exception as e:
            print("A goto err:", e)
        await asyncio.sleep(2)
        print("A(no CDP): requests =", len(reqs_a), "responses =", len(hits_a))

        # ---- 阶段 B：建 CDP session + Network.enable，再导航 ----
        cdp = await page.context.new_cdp_session(page)
        reqs_b = []
        page.on("request", lambda r: reqs_b.append(r.url))
        cdp_resp = []
        cdp.on("Network.responseReceived", lambda e: cdp_resp.append(e["response"]["url"]))
        await cdp.send("Network.enable")
        try:
            await page.goto("https://example.org", wait_until="load", timeout=30000)
        except Exception as e:
            print("B goto err:", e)
        await asyncio.sleep(2)
        print("B(Network.enable): page.on requests =", len(reqs_b),
              "cdp responseReceived =", len(cdp_resp))

        await browser.close()


asyncio.run(main())
