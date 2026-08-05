import subprocess, json, logging

logger = logging.getLogger(__name__)

def run_wappalyzer(url: str) -> dict:
    try:
        result = subprocess.run(
            ["wappalyzer", url, "-o", "json"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
    except Exception as e:
        logger.error(f"Wappalyzer error: {e}")
    return {}

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
