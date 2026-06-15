# wakeup_script.py
# Two modes:
# 1. App is running -> just visit and keep alive
# 2. App is sleeping -> wait for wake button, click it, wait for app to load
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

print(f"Target URL: {url}")
print(f"Max retries: {MAX_RETRIES}")


def click_wake_button(page):
    """Try to find and click the wake button using multiple methods."""
    # Method 1: Playwright locator with known button texts
    wake_texts = [
        "Yes, get this app back up!",
        "Yes, get me out of here",
        "Wake up",
        "Get me out",
    ]
    for text in wake_texts:
        try:
            btn = page.locator(f'button:has-text("{text}")')
            if btn.count() > 0 and btn.first.is_visible(timeout=3000):
                print(f'Found and clicking button: "{text}"')
                btn.first.click()
                return True
        except Exception:
            continue

    # Method 2: JavaScript DOM click (bypasses Playwright visibility checks)
    try:
        result = page.evaluate("""() => {
            const all = document.querySelectorAll('button, a, [role="button"]');
            for (const el of all) {
                const t = (el.textContent || '').toLowerCase();
                if (t.includes('yes') || t.includes('wake') ||
                    t.includes('get me out') || t.includes('back up') ||
                    t.includes('get this app')) {
                    el.click();
                    return el.textContent.trim();
                }
            }
            return null;
        }""")
        if result:
            print(f'JS clicked: "{result}"')
            return True
    except Exception:
        pass

    return False


def is_app_alive(page):
    """Check if Streamlit app is actually running (not sleeping)."""
    try:
        # Check for Streamlit app elements
        if page.locator('[data-testid="stApp"]').count() > 0:
            return True
        if page.locator('iframe[title*="streamlit"]').count() > 0:
            return True
        if page.locator('#root iframe').count() > 0:
            return True
    except Exception:
        pass
    return False


def wait_for_wake_button_and_click(page, max_wait=60):
    """Poll for the wake button to appear, then click it."""
    print(f"Polling for wake button (max {max_wait}s)...")
    start = time.time()
    while time.time() - start < max_wait:
        if click_wake_button(page):
            return True
        elapsed = int(time.time() - start)
        if elapsed % 10 == 0 and elapsed > 0:
            print(f"  Still waiting... ({elapsed}s)")
        time.sleep(2)
    print("Wake button never appeared.")
    return False


for attempt in range(1, MAX_RETRIES + 1):
    print(f"\n--- Attempt {attempt}/{MAX_RETRIES} ---")
    browser = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            # Use "load" instead of "domcontentloaded" to wait for all resources
            print(f"Navigating to {url} ...")
            response = page.goto(url, timeout=PAGE_TIMEOUT, wait_until="load")

            status = response.status if response else "N/A"
            title = page.title()
            print(f"HTTP status: {status}")
            print(f"Page title: '{title}'")

            # Wait for network to settle (React needs time to render)
            try:
                page.wait_for_load_state("networkidle", timeout=30000)
                print("Network idle reached.")
            except Exception:
                print("Network idle timeout, continuing...")

            # Extra time for React rendering
            time.sleep(10)

            # MODE 1: Check if app is already alive (not sleeping)
            if is_app_alive(page):
                print("MODE 1: App is ALIVE - keeping it warm.")
                time.sleep(WAIT_AFTER_LOAD)
                browser.close()
                browser = None
                print(f"Keep-alive successful! (attempt {attempt})")
                sys.exit(0)

            # MODE 2: App might be sleeping - wait for React to render the wake button
            print("MODE 2: App may be SLEEPING - waiting for wake button...")

            clicked = wait_for_wake_button_and_click(page, max_wait=60)

            if clicked:
                print("Wake button clicked! Waiting for app to start...")
                time.sleep(15)

                # Wait for the app to fully load
                try:
                    page.wait_for_selector('[data-testid="stApp"]', timeout=60000)
                    print("Streamlit app loaded!")
                    time.sleep(WAIT_AFTER_LOAD)
                    browser.close()
                    browser = None
                    print(f"Wake-up successful! (attempt {attempt})")
                    sys.exit(0)
                except Exception:
                    # Even if stApp not found, check iframe
                    try:
                        page.wait_for_selector('iframe', timeout=10000)
                        print("Streamlit iframe detected.")
                        time.sleep(WAIT_AFTER_LOAD)
                        browser.close()
                        browser = None
                        print(f"Wake-up successful! (attempt {attempt})")
                        sys.exit(0)
                    except Exception:
                        print("App did not fully load after wake click.")
            else:
                # Could not find wake button - dump debug info
                all_buttons = page.evaluate("""() => {
                    const btns = document.querySelectorAll('button');
                    return Array.from(btns).map(b => b.textContent.trim());
                }""")
                all_links = page.evaluate("""() => {
                    const links = document.querySelectorAll('a');
                    return Array.from(links).map(a => a.textContent.trim()).filter(t => t.length > 0);
                }""")
                print(f"Buttons found: {all_buttons}")
                print(f"Links found: {all_links}")
                print(f"URL after navigation: {page.url}")

    except Exception as e:
        print(f"Attempt {attempt} error: {e}")
    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass

    if attempt < MAX_RETRIES:
        wait = attempt * 15
        print(f"Waiting {wait}s before retry...")
        time.sleep(wait)

print(f"All {MAX_RETRIES} attempts failed.")
sys.exit(1)
