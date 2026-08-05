import subprocess, json, logging, os, signal

logger = logging.getLogger(__name__)


def _kill_process_group(pid):
    """পুরো প্রসেস গ্রুপকে SIGKILL পাঠায় (zombie/child chrome এড়াতে)।"""
    if not pid or pid <= 0:
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except ProcessLookupError:
        pass
    except Exception as e:
        logger.debug(f"প্রসেস গ্রুপ কিল করতে সমস্যা: {e}")


def _terminate_and_drain(proc, label, url):
    """
    টাইমআউট হওয়া প্রসেসকে কিল করে এবং pipe বাফার খালি (drain) করে।

    ⚠️ গুরুত্বপূর্ণ: kill()-এর পর proc.wait() না করে proc.communicate()
    করতে হবে। কারণ child process kill হওয়ার আগেই stdout/stderr pipe-এ
    অনেক ডেটা (OS pipe buffer, সাধারণত ~64KB) লিখে ফেলতে পারে। সেটা কেউ
    read না করলে wait() অনির্দিষ্টকালের জন্য ব্লক (deadlock) হয়ে যায় —
    এটাই আসল কারণ ছিল আগের hang-এর পেছনে।
    """
    logger.warning(f"{label} টাইমআউট: {url}")
    _kill_process_group(proc.pid)
    try:
        proc.kill()  # গ্রুপ কিল fail করলে fallback হিসেবে সরাসরি kill
    except Exception:
        pass
    try:
        # wait() নয়, communicate() — যাতে pipe drain হয় এবং deadlock না হয়
        proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        logger.error(f"{label}: kill করার পরও প্রসেস ৫ সেকেন্ডে সাড়া দেয়নি: {url}")
    except Exception as e:
        logger.debug(f"{label} drain করতে সমস্যা: {e}")


def run_wappalyzer(url: str) -> dict:
    proc = None
    try:
        proc = subprocess.Popen(
            ["wappalyzer", url, "-o", "json"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,  # নতুন প্রসেস গ্রুপ, killpg-এর জন্য দরকার
        )
        try:
            stdout, stderr = proc.communicate(timeout=30)
            if proc.returncode == 0:
                return json.loads(stdout)
            logger.warning(f"Wappalyzer non-zero exit {proc.returncode}: {stderr[:200]}")
            return {}
        except subprocess.TimeoutExpired:
            _terminate_and_drain(proc, "Wappalyzer", url)
            return {}
        except json.JSONDecodeError as e:
            logger.warning(f"Wappalyzer output parse করতে সমস্যা: {e}")
            return {}
    except Exception as e:
        logger.error(f"Wappalyzer error: {e}")
        return {}


def run_lighthouse(url: str) -> dict:
    proc = None
    try:
        proc = subprocess.Popen(
            ["lighthouse", url, "--output=json", "--chrome-flags=--headless --no-sandbox", "--quiet"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=60)
            if proc.returncode == 0:
                report = json.loads(stdout)
                categories = report.get("categories", {})
                perf = categories.get("performance", {}).get("score", None)
                seo = categories.get("seo", {}).get("score", None)
                return {"performance_score": perf, "seo_score": seo}
            logger.warning(f"Lighthouse exit {proc.returncode}: {stderr[:200]}")
            return {"performance_score": None, "seo_score": None}
        except subprocess.TimeoutExpired:
            _terminate_and_drain(proc, "Lighthouse", url)
            return {"performance_score": None, "seo_score": None}
        except json.JSONDecodeError as e:
            logger.warning(f"Lighthouse output parse করতে সমস্যা: {e}")
            return {"performance_score": None, "seo_score": None}
    except Exception as e:
        logger.error(f"Lighthouse error: {e}")
        return {"performance_score": None, "seo_score": None}
