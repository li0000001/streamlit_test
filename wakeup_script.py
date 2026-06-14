# wakeup_script.py
import sys
import time
from playwright.sync_api import sync_playwright

if len(sys.argv) < 2:
    print("Error: Please provide a URL as a command-line argument.")
    sys.exit(1)

url = sys.argv[1]
MAX_RETRIES = 3
PAGE_TIMEOUT = 90000   # 90 秒（冷启动可能很慢）
WAIT_AFTER_LOAD = 15   # 加载后再等 15 秒确保 Streamlit 初始化完成

print(f"Target URL: {url}")
print(f"Max retries: {MAX_RETRIES}")

for attempt in range(1, MAX_RETRIES + 1):
    print(f"\n--- Attempt {attempt}/{MAX_RETRIES} ---")
    browser = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            # 访问页面
            print(f"Navigating to {url} ...")
            response = page.goto(url, timeout=PAGE_TIMEOUT, wait_until="domcontentloaded")

            status = response.status if response else "N/A"
            title = page.title()
            print(f"HTTP status: {status}")
            print(f"Page title: '{title}'")

            # 检查是否被 Streamlit Cloud 的 "app sleeping" 页面拦截
            content = page.content()
            if "sleeping" in content.lower() or "zzz" in content.lower():
                print("⚠️ App is sleeping, waiting for it to wake up...")
                # 等待 Streamlit 真正加载（最多再等 60 秒）
                try:
                    page.wait_for_selector('[data-testid="stApp"]', timeout=60000)
                    print("✅ Streamlit app loaded!")
                except Exception:
                    print("⏳ Streamlit app not fully loaded, but page responded.")

            # 等待确保所有后台脚本运行
            print(f"Waiting {WAIT_AFTER_LOAD}s to simulate user activity...")
            time.sleep(WAIT_AFTER_LOAD)

            browser.close()
            browser = None

            # 验证：HTTP 200 就认为唤醒成功
            if response and response.status == 200:
                print(f"\n✅ Wake-up successful! (attempt {attempt})")
                sys.exit(0)
            else:
                print(f"\n⚠️ Unexpected status {status}, will retry...")

    except Exception as e:
        print(f"❌ Attempt {attempt} failed: {e}")
    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass

    # 重试前等待
    if attempt < MAX_RETRIES:
        wait = attempt * 10  # 10s, 20s, 30s...
        print(f"Waiting {wait}s before retry...")
        time.sleep(wait)

print(f"\n❌ All {MAX_RETRIES} attempts failed.")
sys.exit(1)
