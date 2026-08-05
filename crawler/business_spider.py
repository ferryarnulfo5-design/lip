import requests
from bs4 import BeautifulSoup

def crawl_website(base_url: str) -> list:
    pages = []
    for path in ["", "about", "contact", "team"]:
        try:
            resp = requests.get(f"{base_url}/{path}", timeout=10)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                pages.append({"url": resp.url, "html": soup.get_text()})
        except:
            pass
    return pages
