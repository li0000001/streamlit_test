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

# Streamlit Cloud sleeping page button texts (by priority)
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


def wait_for_wake_button(page, timeout=30000):
    """Wait for the wake button to appear in the DOM, then click it."""
    # Method 1: Wait for any button containing known wake text
    for text in WAKE_BUTTON_TEXTS:
        try:
            print(f'Waiting for button: "{text}" ...')
            btn = page.locator(f'button:has-text("{text}")').first
            btn.wait_for(state="visible", timeout=timeout // len(WAKE_BUTTON_TEXTS))
            print(f'Found button: "{text}", clicking...')
            btn.click()
            return True
        except Exception:
            continue

    # Method 2: Wait for any clickable element with wake-related keywords
    try:
        print("Searching all elements for wake keywords...")
        for selector in ['button', 'a', '[role="button"]', 'input[type="submit"]']:
            page.locator(selector).first.wait_for(state="attached", timeout=5000)
            elements = page.locator(selector).all()
            for el in elements:
                el_text = (el.text_content() or "").strip().lower()
                if any(kw in el_text for kw in ["wake", "get me out", "get this app", "back up", "confirm", "yes"]):
                    print(f'Found wake element: "{el.text_content().strip()}" ({selector}), clicking...')
                    el.click()
                    return True
    except Exception:
        pass

    # Method 3: Click any non-trivial button on the page
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

    # Method 4: Try JavaScript evaluation to find and click the button
    try:
        print("Trying JavaScript click...")
        clicked = page.evaluate("""() => {
            const buttons = document.querySelectorAll('button');
            for (const btn of buttons) {
                const text = btn.textContent.toLowerCase();
                if (text.includes('yes') || text.includes('wake') || text.includes('get me') || text.includes('back up')) {
                    btn.click();
                    return btn.textContent.trim();
                }
            }
            return null;
        }""")
        if clicked:
            print(f'JavaScript clicked button: "{clicked}"')
            return True
    except Exception:
        pass

    print("No wake button found on the page.")
    return False


def wait_for_app_loaded(page, timeout=60000):
    """Wait for Streamlit app to fully load after wake."""
    try:
        page.wait_for_selector('[data-testid="stApp"]', timeout=timeout)
        print("Streamlit app loaded!")
        return True
    except Exception:
        pass

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

            # Wait for JavaScript rendering to complete
            print("Waiting for page to fully render...")
            try:
                page.wait_for_load_state("networkidle", timeout=30000)
            except Exception:
                pass
            # Extra wait for JS rendering
            time.sleep(8)

            # Check if sleeping
            content = page.content()
            is_sleeping = any(kw in content.lower() for kw in [
                "sleep", "zzz", "wake", "get me out", "hibernate",
                "this app is asleep", "app has gone to sleep",
                "get this app back up", "inactivity"
            ])

            if is_sleeping:
                print("App is SLEEPING - attempting to wake it up...")

                # Wait for and click the wake button
                clicked = wait_for_wake_button(page, timeout=30000)
                if clicked:
                    print("Wake button clicked! Waiting for app to start...")
                    time.sleep(10)

                    # Wait for app to load
                    loaded = wait_for_app_loaded(page, timeout=60000)
                    if loaded:
                        print(f"Waiting {WAIT_AFTER_LOAD}s for initialization...")
                        time.sleep(WAIT_AFTER_LOAD)
                        print(f"Wake-up successful! (attempt {attempt})")
                        browser.close()
                        browser = None
                        sys.exit(0)
                    else:
                        print("App did not fully load after clicking wake button.")
                else:
                    # Debug: print page content snippet
                    snippet = content[:500].replace('\n', ' ')
                    print(f"Could not find wake button. Page snippet: {snippet}")
            else:
                # App is not sleeping
                loaded = wait_for_app_loaded(page, timeout=30000)
                if loaded or (response and response.status == 200):
                    print(f"Waiting {WAIT_AFTER_LOAD}s...")
                    time.sleep(WAIT_AFTER_LOAD)
                    print(f"App is already awake! (attempt {attempt})")
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

    if attempt < MAX_RETRIES:
        wait = attempt * 15
        print(f"Waiting {wait}s before retry...")
        time.sleep(wait)

print(f"All {MAX_RETRIES} attempts failed.")
sys.exit(1)
