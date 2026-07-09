import time
import logging
import sys
import os
from seleniumbase import SB

logger = logging.getLogger("reddit_scraper")

def scrape_reddit_answers(ticker: str) -> dict:
    """
    Launches a SeleniumBase instance in UC (Undetected ChromeDriver) mode,
    navigates to Reddit Answers, submits the query via shadow DOM inputs, monitors redirection,
    and extracts the structured summary and citation URLs from the shadow DOM.
    Supports proxy settings and virtual display (Xvfb) for server environments.
    """
    ticker_upper = ticker.upper()
    search_url = f"https://www.reddit.com/answers/search/?q={ticker_upper}+stock"
    
    # Read environment configurations
    headless_env = os.getenv("REDDIT_HEADLESS", "True").lower() in ("true", "1", "yes")
    proxy_env = os.getenv("REDDIT_PROXY", "").strip() or None
    
    # Enable xvfb (virtual framebuffer) automatically on Linux if running in headless mode
    use_xvfb = headless_env and (sys.platform != "win32")
    
    logger.info(
        f"Starting Reddit scraper for ticker: {ticker_upper} "
        f"(headless={headless_env}, proxy={proxy_env is not None}, xvfb={use_xvfb})"
    )
    
    try:
        # Initialize SeleniumBase SB context manager with anti-bot options
        with SB(uc=True, headless=headless_env, headless2=headless_env, xvfb=use_xvfb, proxy=proxy_env) as sb:
            sb.set_window_size(1280, 1024)
            
            home_url = "https://www.reddit.com/answers/"
            logger.info(f"Navigating to base page: {home_url}")
            # Use uc_open_with_reconnect to bypass initial bot detection
            sb.uc_open_with_reconnect(home_url, reconnect_time=6)
            
            # Save page source and screenshot of the initial load for debugging
            try:
                sb.save_screenshot("j:\\Antigravity\\Richie Rich\\BarrenWuffet - Discord\\scripts\\reddit\\debug_home.png")
                with open("j:\\Antigravity\\Richie Rich\\BarrenWuffet - Discord\\scripts\\reddit\\debug_home.html", "w", encoding="utf-8") as f:
                    f.write(sb.get_page_source())
                logger.info("Saved debug screenshot and source of base page.")
            except Exception as e:
                logger.error(f"Failed to save debug assets: {e}")

            # Wait up to 10 seconds for guides-textarea-input to render inside shadow DOM
            textarea_found = False
            start_time = time.time()
            while time.time() - start_time < 10:
                try:
                    res = sb.execute_script("""
                        const host = document.querySelector('guides-textarea-input');
                        if (host && host.shadowRoot) {
                            const textarea = host.shadowRoot.querySelector('textarea#innerTextArea');
                            if (textarea) return true;
                        }
                        return false;
                    """)
                    if res:
                        textarea_found = True
                        break
                except Exception:
                    pass
                time.sleep(0.5)
                
            if not textarea_found:
                logger.error("Could not find input textarea shadow host. Page might have blocked the connection.")
                # Retrieve the title/body to log why we failed
                page_title = sb.get_title()
                return {
                    "ticker": ticker_upper,
                    "search_url": search_url,
                    "detailed_url": None,
                    "summary": None,
                    "error": f"Reddit Answers input box not found (Page Title: '{page_title}'). Check proxy or IP block status."
                }
                
            # Submit the query via JS
            query_str = f"{ticker_upper} stock"
            logger.info(f"Submitting query: '{query_str}'")
            submitted = sb.execute_script("""
                const host = document.querySelector('guides-textarea-input');
                const textarea = host.shadowRoot.querySelector('textarea#innerTextArea');
                textarea.value = arguments[0];
                textarea.dispatchEvent(new Event('input', { bubbles: true }));
                
                const button = host.shadowRoot.querySelector('button#submit-button') || 
                               host.shadowRoot.querySelector('button[aria-label="Submit Question"]');
                if (button) {
                    button.click();
                    return true;
                }
                return false;
            """, query_str)
            
            if not submitted:
                logger.error("Failed to click the submit button in shadow DOM.")
                return {
                    "ticker": ticker_upper,
                    "search_url": search_url,
                    "detailed_url": None,
                    "summary": None,
                    "error": "Failed to submit search query (button not found/clickable)"
                }
                
            # Wait up to 15 seconds for redirect to an answers URL
            detailed_url = None
            start_time = time.time()
            while time.time() - start_time < 15:
                current_url = sb.get_current_url()
                if "/answers/" in current_url:
                    parts = current_url.strip("/").split("/")
                    if len(parts) > 0 and parts[-1] != "answers":
                        detailed_url = current_url
                        break
                time.sleep(0.5)
                
            if not detailed_url:
                logger.error("Timeout waiting for search redirect.")
                return {
                    "ticker": ticker_upper,
                    "search_url": search_url,
                    "detailed_url": None,
                    "summary": None,
                    "error": "Timeout waiting for redirect to answers page"
                }
                
            logger.info(f"Redirected successfully. Detailed page URL: {detailed_url}")
            
            # Reopen the detailed answers page using reconnect to bypass detection on redirect
            sb.uc_open_with_reconnect(detailed_url, reconnect_time=4)
            
            # Wait up to 15 seconds for the shadow DOM and content container to render on the answers page
            structure = None
            start_time = time.time()
            while time.time() - start_time < 15:
                try:
                    # Find shadow host
                    hosts = sb.find_elements("guides-response-container-streaming")
                    if hosts:
                        # Run JS to traverse the shadow DOM and extract categories, bullet points & citations
                        res = sb.execute_script("""
                            const host = document.querySelector('guides-response-container-streaming');
                            if (!host || !host.shadowRoot) return null;
                            const el = host.shadowRoot.querySelector('.rendererd-rtjson');
                            if (!el) return null;
                            if (el.children.length === 0) return null;
                            
                            const childNodes = Array.from(el.childNodes);
                            const structure = [];
                            let currentHeader = 'General Overview';
                            
                            for (const node of childNodes) {
                                if (node.nodeType !== Node.ELEMENT_NODE) continue;
                                
                                const tagName = node.tagName.toLowerCase();
                                if (tagName === 'h3') {
                                    currentHeader = node.textContent.trim();
                                } else if (tagName === 'ul') {
                                    const items = Array.from(node.querySelectorAll('li')).map(li => {
                                        const p = li.querySelector('p') || li;
                                        const text = p.textContent.trim();
                                        const citations = Array.from(p.querySelectorAll('a')).map(a => ({
                                            text: a.textContent.trim(),
                                            href: a.href
                                        }));
                                        return { text, citations };
                                    });
                                    structure.push({
                                        category: currentHeader,
                                        items: items
                                    });
                                } else if (tagName === 'p') {
                                    const text = node.textContent.trim();
                                    const citations = Array.from(node.querySelectorAll('a')).map(a => ({
                                        text: a.textContent.trim(),
                                        href: a.href
                                    }));
                                    structure.push({
                                        category: currentHeader,
                                        items: [{ text, citations }]
                                    });
                                }
                            }
                            return structure;
                        """)
                        if res and len(res) > 0:
                            structure = res
                            break
                except Exception as e:
                    logger.debug(f"Error checking shadow DOM: {e}")
                time.sleep(0.5)
                
            if not structure:
                logger.info(f"Failed to extract structured summary from detailed page for {ticker_upper}.")
                
            logger.info("Scrape completed successfully.")
            return {
                "ticker": ticker_upper,
                "search_url": search_url,
                "detailed_url": detailed_url,
                "summary": structure
            }
            
    except Exception as e:
        logger.error(f"Scraper error for ticker {ticker_upper}: {e}", exc_info=True)
        return {
            "ticker": ticker_upper,
            "search_url": search_url,
            "detailed_url": None,
            "summary": None,
            "error": str(e)
        }

if __name__ == "__main__":
    # Test script execution
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    test_ticker = "AAPL"
    result = scrape_reddit_answers(test_ticker)
    print("Scraping Result:")
    import pprint
    pprint.pprint(result)
