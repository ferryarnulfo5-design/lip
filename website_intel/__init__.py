import subprocess, json, logging, re
import requests

logger = logging.getLogger(__name__)

TECH_SIGNATURES = {
    "WordPress": [r"wp-content", r"wp-includes", r"/wp-json/"],
    "Shopify": [r"cdn\.shopify\.com", r"Shopify\.theme"],
    "Wix": [r"static\.wixstatic\.com", r"wix\.com"],
    "Squarespace": [r"squarespace\.com", r"static1\.squarespace\.com"],
    "Webflow": [r"webflow\.com", r"webflow\.js"],
    "GoDaddy Website Builder": [r"godaddy\.com/websitebuilder", r"gdbuilder"],
    "jQuery": [r"jquery(\.min)?\.js"],
    "React": [r"react(-dom)?(\.min)?\.js", r"__REACT_DEVTOOLS"],
    "Google Analytics": [r"google-analytics\.com", r"gtag\("],
    "Google Tag Manager": [r"googletagmanager\.com"],
    "Facebook Pixel": [r"connect\.facebook\.net.*fbevents"],
    "Cloudflare": [r"cloudflare"],
    "HubSpot": [r"hs-scripts\.com", r"hubspot"],
}

def run_wappalyzer(url: str) -> dict:
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        html = resp.text
        headers_text = json.dumps(dict(resp.headers)).lower()
        combined = (html + headers_text).lower()

        found = []
        for tech, patterns in TECH_SIGNATURES.items():
            if any(re.search(p, combined, re.IGNORECASE) for p in patterns):
                found.append(tech)

        return {"technologies": found, "status_code": resp.status_code}

    except Exception as e:
        logger.error(f"Tech detection error: {e}")
        return {"technologies": [], "status_code": None}


def run_lighthouse(url: str) -> dict:
    try:
        result = subprocess.run(
            ["lighthouse", url, "--output=json", "--chrome-flags=--headless", "--quiet"],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            report = json.loads(result.stdout)
            categories = report.get("categories", {})
            perf = categories.get("performance", {}).get("score", None)
            seo = categories.get("seo", {}).get("score", None)
            return {"performance_score": perf, "seo_score": seo}
    except Exception as e:
        logger.error(f"Lighthouse error: {e}")
    return {"performance_score": None, "seo_score": None}
