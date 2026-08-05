import subprocess, json, logging, os, signal

logger = logging.getLogger(__name__)

def _kill_process_tree(pid, timeout=5):
    """পুরো প্রসেস ট্রি কিল করে (zombie এড়াতে)"""
    if pid <= 0:
        return
    try:
        # পুরো গ্রুপ কিল
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except ProcessLookupError:
        pass
    except Exception as e:
        logger.debug(f"কিল করতে সমস্যা: {e}")

def run_wappalyzer(url: str) -> dict:
    try:
        # ৩০ সেকেন্ডের টাইমআউট, প্রসেস গ্রুপ তৈরি
        proc = subprocess.Popen(
            ["wappalyzer", url, "-o", "json"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True  # নতুন প্রসেস গ্রুপ
        )
        try:
            stdout, stderr = proc.communicate(timeout=30)
            if proc.returncode == 0:
                return json.loads(stdout)
            else:
                logger.warning(f"Wappalyzer non-zero exit {proc.returncode}: {stderr[:200]}")
                return {}
        except subprocess.TimeoutExpired:
            logger.warning(f"Wappalyzer টাইমআউট: {url}")
            _kill_process_tree(proc.pid)
            proc.kill()
            proc.wait(timeout=5)
            return {}
    except Exception as e:
        logger.error(f"Wappalyzer error: {e}")
        return {}

def run_lighthouse(url: str) -> dict:
    try:
        proc = subprocess.Popen(
            ["lighthouse", url, "--output=json", "--chrome-flags=--headless --no-sandbox", "--quiet"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True
        )
        try:
            stdout, stderr = proc.communicate(timeout=60)  # ১ মিনিট
            if proc.returncode == 0:
                report = json.loads(stdout)
                categories = report.get("categories", {})
                perf = categories.get("performance", {}).get("score", None)
                seo = categories.get("seo", {}).get("score", None)
                return {"performance_score": perf, "seo_score": seo}
            else:
                logger.warning(f"Lighthouse exit {proc.returncode}: {stderr[:200]}")
                return {"performance_score": None, "seo_score": None}
        except subprocess.TimeoutExpired:
            logger.warning(f"Lighthouse টাইমআউট (60s): {url}")
            _kill_process_tree(proc.pid)
            proc.kill()
            proc.wait(timeout=5)
            return {"performance_score": None, "seo_score": None}
    except Exception as e:
        logger.error(f"Lighthouse error: {e}")
        return {"performance_score": None, "seo_score": None}
