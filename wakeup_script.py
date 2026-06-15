# wakeup_script.py
import sys
import time
from playwright.sync_api import sync_playwright

if len(sys.argv) < 2:
    print("Error: Please provide a URL as a command-line argument.")
    sys.exit(1)

url = sys.argv[1]
MAX_RETRIES = 3
PAGE_TIMEOUT = 90000
WAIT_AFTER_LOAD = 15

# Streamlit Cloud 休眠页面上可能出现的按钮文字（按匹配优先级排序）
WAKE_BUTTON_TEXTS = [
    "Yes, get this app back up!",
    "Yes, get me out of here",
    "Wake up",
    "Wake this app",
    "Get me out",
    "Confirm",
    "Yes",
]

print(f"Target URL: {url}")
print(f"Max retries: {MAX_RETRIES}")


def try_click_wake_button(page):
    """尝试在休眠页面上找到并点击唤醒按钮"""
    # 方法 1：通过按钮文字匹配
    for text in WAKE_BUTTON_TEXTS:
        try:
            btn = page.locator(f'button:has-text("{text}")').first
            if btn.is_visible(timeout=2000):
                print(f'Found wake button: "{text}", clicking...')
                btn.click()
                return True
        except Exception:
            continue

    # 方法 2：通过链接/按钮中包含 wake 关键词
    try:
        for selector in ['button', 'a', 'input[type="submit"]', '[role="button"]']:
            elements = page.locator(selector).all()
            for el in elements:
                el_text = (el.text_content() or "").strip().lower()
                if any(kw in el_text for kw in ["wake", "get me out", "confirm", "yes"]):
                    print(f'Found wake element: "{el_text}" ({selector}), clicking...')
                    el.click()
                    return True
    except Exception:
        pass

    # 方法 3：点击页面中所有看起来像主操作的按钮（排除 Fork/Menu）
    try:
        buttons = page.locator('button').all()
        for btn in buttons:
            btn_text = (btn.text_content() or "").strip()
            if btn_text and btn_text not in ["Fork", "Main menu", ""] and len(btn_text) > 2:
                print(f'Clicking primary button: "{btn_text}"')
                btn.click()
                return True
    except Exception:
        pass

    print("No wake button found on the page.")
    return False


def wait_for_app_loaded(page, timeout=60000):
    """等待 Streamlit 应用真正加载完成"""
    try:
        page.wait_for_selector('[data-testid="stApp"]', timeout=timeout)
        print("Streamlit app loaded!")
        return True
    except Exception:
        pass

    # 备选：等待 iframe 加载（Streamlit Cloud 用 iframe 嵌套应用）
    try:
        page.wait_for_selector('iframe', timeout=timeout)
        print("Streamlit iframe detected.")
        return True
    except Exception:
        pass

    return False


for attempt in range(1, MAX_RETRIES + 1):
    print(f"\n--- Attempt {attempt}/{MAX_RETRIES} ---")
    browser = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            print(f"Navigating to {url} ...")
            response = page.goto(url, timeout=PAGE_TIMEOUT, wait_until="domcontentloaded")

            status = response.status if response else "N/A"
            title = page.title()
            print(f"HTTP status: {status}")
            print(f"Page title: '{title}'")

            # 等待页面渲染
            time.sleep(5)

            # 检查是否休眠
            content = page.content()
            is_sleeping = any(kw in content.lower() for kw in [
                "sleep", "zzz", "wake", "get me out", "hibernate",
                "this app is asleep", "app has gone to sleep"
            ])

            if is_sleeping:
                print("App is SLEEPING - attempting to wake it up...")

                # 点击唤醒按钮
                clicked = try_click_wake_button(page)
                if clicked:
                    print("Wake button clicked! Waiting for app to start...")
                    time.sleep(10)

                    # 等待应用加载
                    loaded = wait_for_app_loaded(page, timeout=60000)
                    if loaded:
                        print(f"Waiting {WAIT_AFTER_LOAD}s for initialization...")
                        time.sleep(WAIT_AFTER_LOAD)
                        print(f"\nWake-up successful! (attempt {attempt})")
                        browser.close()
                        browser = None
                        sys.exit(0)
                    else:
                        print("App did not fully load after clicking wake button.")
                else:
                    print("Could not find or click a wake button.")
            else:
                # 应用没有休眠，可能已经醒了
                loaded = wait_for_app_loaded(page, timeout=30000)
                if loaded or (response and response.status == 200):
                    print(f"Waiting {WAIT_AFTER_LOAD}s...")
                    time.sleep(WAIT_AFTER_LOAD)
                    print(f"\nApp is already awake! (attempt {attempt})")
                    browser.close()
                    browser = None
                    sys.exit(0)
                else:
                    print(f"Unexpected status {status}, will retry...")

    except Exception as e:
        print(f"Attempt {attempt} failed: {e}")
    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass

    # 重试前等待
    if attempt < MAX_RETRIES:
        wait = attempt * 15
        print(f"Waiting {wait}s before retry...")
        time.sleep(wait)

print(f"\nAll {MAX_RETRIES} attempts failed.")
sys.exit(1)
