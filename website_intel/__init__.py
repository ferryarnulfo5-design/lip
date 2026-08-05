import subprocess, json, logging, os, signal
from .tech_detector import detect_technologies
logger = logging.getLogger(__name__)


def _terminate_and_drain(proc, drain_timeout=5):
    """
    প্রসেসকে (এবং তার পুরো process group কে) কিল করে, তারপর stdout/stderr
    pipe খালি (drain) করে দেয়।

    communicate(timeout=...) টাইমআউট হলে pipe এখনো খোলা ও unread থাকে। kill
    করার পর সরাসরি wait() কল করা বিপজ্জনক — child kill হওয়ার আগেই pipe
    buffer এ (OS বাফার সাধারণত ~64KB) অনেক ডেটা লিখে রাখলে এবং সেটা কেউ read
    না করলে, wait() অনির্দিষ্টকালের জন্য আটকে (deadlock) যেতে পারে। তাই kill
    এর পরও wait() না করে communicate() কল করা হচ্ছে, যাতে বাকি output read
    করে pipe খালি হয়ে যায় এবং zombie/hang এড়ানো যায়।
    """
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except ProcessLookupError:
        pass
    except Exception as e:
        logger.debug(f"কিল করতে সমস্যা: {e}")

    try:
        proc.communicate(timeout=drain_timeout)
    except subprocess.TimeoutExpired:
        logger.warning(f"drain timeout — pipe পুরোপুরি খালি করা যায়নি (pid={proc.pid})")
    except Exception as e:
        logger.debug(f"drain করতে সমস্যা: {e}")


def run_wappalyzer(url: str) -> dict:
    try:
        proc = subprocess.Popen(
            ["wappalyzer", url, "-o", "json"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,  # নতুন প্রসেস গ্রুপ
        )
        try:
            stdout, stderr = proc.communicate(timeout=30)
            if proc.returncode == 0:
                try:
                    return json.loads(stdout)
                except json.JSONDecodeError:
                    logger.warning(f"Wappalyzer invalid JSON output: {url}")
                    return {}
            else:
                logger.warning(f"Wappalyzer non-zero exit {proc.returncode}: {stderr[:200]}")
                return {}
        except subprocess.TimeoutExpired:
            logger.warning(f"Wappalyzer টাইমআউট: {url}")
            _terminate_and_drain(proc)
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
            start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=60)  # ১ মিনিট
            if proc.returncode == 0:
                try:
                    report = json.loads(stdout)
                except json.JSONDecodeError:
                    logger.warning(f"Lighthouse invalid JSON output: {url}")
                    return {"performance_score": None, "seo_score": None}
                categories = report.get("categories", {})
                perf = categories.get("performance", {}).get("score", None)
                seo = categories.get("seo", {}).get("score", None)
                return {"performance_score": perf, "seo_score": seo}
            else:
                logger.warning(f"Lighthouse exit {proc.returncode}: {stderr[:200]}")
                return {"performance_score": None, "seo_score": None}
        except subprocess.TimeoutExpired:
            logger.warning(f"Lighthouse টাইমআউট (60s): {url}")
            _terminate_and_drain(proc)
            return {"performance_score": None, "seo_score": None}
    except Exception as e:
        logger.error(f"Lighthouse error: {e}")
        return {"performance_score": None, "seo_score": None}
