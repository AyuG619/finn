# End-to-end UI flows against the mock server (python -m tests.mock_server), using Playwright.
# Run: python backend/tests/ui_flows.py
import asyncio, json
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8765"
results = []
def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        page = await ctx.new_page()
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        # Brief loads with slips
        await page.goto(BASE + "/#/brief"); await page.wait_for_selector(".slip", timeout=15000)
        n0 = await page.locator(".slips.deal > .slip").count()
        check("brief has needs-you slips", n0 >= 2, n0)
        # results figures render after enrichment
        await page.wait_for_selector("table.figs", timeout=15000)
        check("results figures table", await page.locator("table.figs tr").count() >= 5)
        # mark first slip seen
        await page.locator(".slips.deal > .slip").first.get_by_role("button", name="Seen").click()
        await page.wait_for_timeout(300)
        n1 = await page.locator(".slips.deal > .slip").count()
        check("seen removes a slip from needs-you", n1 == n0 - 1, (n0, n1))
        check("seen section appears", await page.get_by_role("heading", name="Seen").count() == 1)
        # window switch
        await page.get_by_role("button", name="3 days").click()
        await page.wait_for_timeout(1500)
        check("window chip pressed", await page.get_by_role("button", name="3 days").get_attribute("aria-pressed") == "true")
        # expand a register row
        rows = page.locator(".register .reg")
        if await rows.count():
            await rows.first.click(); await page.wait_for_timeout(800)
            check("register row expands", await page.locator(".register .reg[aria-expanded='true'], .register .reg .reg-open").count() >= 1)
        # folded chips open
        chip = page.locator(".fold .chip").first
        if await chip.count():
            await chip.click(); await page.wait_for_timeout(200)
            check("folded list opens", await page.locator(".fold-list li").count() >= 1)

        # Ask bar: threshold command
        await page.fill(".askbar input", "alert me on 3% moves"); await page.press(".askbar input", "Enter")
        await page.wait_for_timeout(800)
        check("ask sets price threshold and routes to watch", "#/watch" in page.url and "3%" in (await page.locator(".stepper output").first.inner_text()), page.url)
    
        # stepper +
        await page.locator(".stepper").first.get_by_role("button", name="Raise price move threshold").click()
        check("stepper raises", "3.5%" in (await page.locator(".stepper output").first.inner_text()))
        # Ask bar: research command
        await page.fill(".askbar input", "research trent"); await page.press(".askbar input", "Enter")
        await page.wait_for_timeout(1500)
        check("ask routes to research", "#/research/TRENT" in page.url, page.url)
        # dossier completes (mock pipeline ~5s)
        await page.wait_for_selector(".ledger li", timeout=20000)
        check("promise ledger renders stamps", await page.locator(".stamp").count() >= 3)
        check("charts render", await page.locator(".chart svg").count() >= 3)
        # table toggle
        await page.get_by_role("button", name="View as table").first.click()
        check("chart table view", await page.locator("table.tbl").count() >= 2)
        # ask a question in research context via ask bar
        await page.fill(".askbar input", "what did they guide on margins?"); await page.press(".askbar input", "Enter")
        await page.wait_for_selector(".sheet .answer", timeout=15000)
        check("research question answered in sheet with citations", await page.locator(".sheet .cite").count() >= 1)
        await page.get_by_role("button", name="Close").click()
        # Book: open company sheet
        await page.goto(BASE + "/#/book"); await page.wait_for_selector(".row", timeout=15000)
        await page.locator(".row").first.click()
        await page.wait_for_selector(".sheet .chart svg", timeout=15000)
        check("company sheet with price chart", True)
        await page.keyboard.press("Escape")
        check("sheet closes on escape", await page.locator(".sheet").count() == 0)
        # Research home search
        await page.goto(BASE + "/#/research"); await page.wait_for_selector(".pick")
        await page.fill(".search input", "tata"); await page.wait_for_timeout(700)
        check("search suggestions", await page.locator(".suggest button").count() >= 1)
        # Watch: categories toggle persists
        await page.goto(BASE + "/#/watch"); await page.wait_for_selector(".cats .chip")
        cap = page.get_by_role("button", name="Capex")
        before = await cap.get_attribute("aria-pressed"); await cap.click()
        after = await cap.get_attribute("aria-pressed")
        check("category toggle", before != after, (before, after))
        await page.reload(); await page.wait_for_selector(".cats .chip")
        check("prefs persist across reload", await page.get_by_role("button", name="Capex").get_attribute("aria-pressed") == after)

        check("no console errors", not errors, errors[:5])
        await b.close()

try:
    asyncio.run(main())
except Exception as e:
    print("EXCEPTION:", str(e).splitlines()[0])
for name, ok, detail in results:
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {detail}"))
