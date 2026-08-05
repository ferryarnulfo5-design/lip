"""
website_intel/tech_detector.py

run_wappalyzer() এর replacement — কোনো external CLI (`wappalyzer`), কোনো
API key, কোনো monthly limit লাগে না। enthec/webappanalyzer প্রজেক্টের
(GPL-3.0, community-maintained, ২০২৩ সালের আগে Wappalyzer নিজেই যা
open-source রেখেছিল তার continuation) fingerprint JSON ফাইলগুলো ডাউনলোড
করে লোকালি ক্যাশ করে, তারপর raw HTML / HTTP headers / meta tags /
script src এর উপর সরাসরি regex ম্যাচিং চালায়।

সোর্স: https://github.com/enthec/webappanalyzer  (src/technologies/*.json)
"""

import json
import logging
import re
import threading
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ফিঙ্গারপ্রিন্ট ডাটাবেস — ডাউনলোড + ক্যাশিং
# ---------------------------------------------------------------------------

_RAW_BASE = "https://raw.githubusercontent.com/enthec/webappanalyzer/main/src/technologies/"
_LETTERS = list("abcdefghijklmnopqrstuvwxyz") + ["_"]  # ফাইলগুলো a.json, b.json ... _.json
_CACHE_MAX_AGE_SEC = 7 * 24 * 3600  # ৭ দিন — এর বেশি পুরনো হলে re-download চেষ্টা

_FINGERPRINTS = None
_FP_LOCK = threading.Lock()


def _cache_path() -> Path:
    return Path(__file__).resolve().parent / ".fingerprint_cache.json"


def _download_fingerprints() -> dict:
    """সব a-z + _ ফাইল ডাউনলোড করে একটা dict এ merge করে। কোনো একটা ফাইল
    ব্যর্থ হলেও বাকিগুলো নিয়ে এগিয়ে যায় — আংশিক ডাটাবেস, কিছু না থাকার
    চেয়ে ভালো।"""
    merged = {}
    session = requests.Session()
    for letter in _LETTERS:
        url = f"{_RAW_BASE}{letter}.json"
        try:
            resp = session.get(url, timeout=10)
            if resp.status_code == 200:
                merged.update(resp.json())
            else:
                logger.warning(f"fingerprint file ফেচ ব্যর্থ {letter}.json: HTTP {resp.status_code}")
        except Exception as e:
            logger.warning(f"fingerprint file ডাউনলোড এরর {letter}.json: {e}")
    return merged


def _load_fingerprints() -> dict:
    cache_file = _cache_path()

    if cache_file.exists():
        age = time.time() - cache_file.stat().st_mtime
        if age < _CACHE_MAX_AGE_SEC:
            try:
                return json.loads(cache_file.read_text(encoding="utf-8"))
            except Exception:
                pass  # কারাপ্ট ক্যাশ — re-download এ যাচ্ছি

    logger.info("Wappalyzer fingerprint database ডাউনলোড হচ্ছে (enthec/webappanalyzer)...")
    data = _download_fingerprints()

    if data:
        try:
            cache_file.write_text(json.dumps(data), encoding="utf-8")
        except Exception as e:
            logger.debug(f"fingerprint cache লিখতে সমস্যা: {e}")
        return data

    # ডাউনলোড পুরোপুরি ব্যর্থ (যেমন: network নেই) — পুরনো ক্যাশ থাকলে সেটাই ব্যবহার করি
    if cache_file.exists():
        logger.warning("fingerprint ডাউনলোড ব্যর্থ — পুরনো (stale) ক্যাশ ব্যবহার করছি")
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    logger.error("fingerprint database লোড করা যায়নি — tech detection off থাকবে")
    return {}


def _get_fingerprints() -> dict:
    """থ্রেড-সেফ lazy singleton — প্রথম কলে ডাউনলোড/লোড হয়, পরের কলগুলো
    মেমরি থেকে সার্ভ হয় (ThreadPoolExecutor দিয়ে সমান্তরাল সাইট প্রসেসিং
    করলেও বারবার ডাউনলোড হবে না)।"""
    global _FINGERPRINTS
    if _FINGERPRINTS is None:
        with _FP_LOCK:
            if _FINGERPRINTS is None:
                _FINGERPRINTS = _load_fingerprints()
    return _FINGERPRINTS


# ---------------------------------------------------------------------------
# প্যাটার্ন ম্যাচিং হেল্পার
# ---------------------------------------------------------------------------

# Wappalyzer প্যাটার্নে version/confidence তথ্য এভাবে যুক্ত থাকে:
# "wp-content/themes/([\\w-]+)\\;version:\\1\\;confidence:50" — ম্যাচ করার
# আগে এই সাফিক্স ফেলে দিতে হবে, নাহলে regex compile ভুল হবে বা ভুল ম্যাচ করবে।
_VERSION_SUFFIX = re.compile(r"\\;(?:version|confidence):[^\\]*", re.IGNORECASE)
_compiled_cache: dict = {}


def _compiled(pattern: str):
    if pattern not in _compiled_cache:
        cleaned = _VERSION_SUFFIX.split(pattern)[0]
        try:
            _compiled_cache[pattern] = re.compile(cleaned, re.IGNORECASE)
        except re.error:
            _compiled_cache[pattern] = None  # ভাঙা/অসমর্থিত regex — স্কিপ
    return _compiled_cache[pattern]


def _as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _match_any(patterns, text: str) -> bool:
    if not text:
        return False
    for p in _as_list(patterns):
        regex = _compiled(p)
        if regex and regex.search(text):
            return True
    return False


# ---------------------------------------------------------------------------
# মূল ফাংশন — run_wappalyzer() এর জায়গায় বসবে
# ---------------------------------------------------------------------------

def detect_technologies(url: str, timeout: int = 15) -> dict:
    """
    run_wappalyzer(url) এর drop-in replacement।

    রিটার্ন: {"technologies": [{"name": str, "categories": [int, ...]}], "url": str}
    ব্যর্থ হলে {"technologies": []} — run_wappalyzer() এর মতোই কখনো raise করে না,
    process_site() এর error-handling ভাঙবে না।
    """
    fingerprints = _get_fingerprints()
    if not fingerprints:
        return {"technologies": []}

    try:
        resp = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 (compatible; LeadIntelBot/1.0)"},
        )
    except Exception as e:
        logger.warning(f"tech_detector fetch ব্যর্থ {url}: {e}")
        return {"technologies": []}

    html = resp.text or ""
    headers = {k.lower(): v for k, v in resp.headers.items()}

    meta_tags, script_srcs = {}, []
    try:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.find_all("meta"):
            name = (tag.get("name") or tag.get("property") or "").lower()
            if name:
                meta_tags[name] = tag.get("content") or ""
        for tag in soup.find_all("script", src=True):
            script_srcs.append(tag["src"])
    except Exception as e:
        logger.debug(f"HTML পার্স করতে সমস্যা {url}: {e}")

    detected = []
    for name, fp in fingerprints.items():
        if not isinstance(fp, dict):
            continue

        matched = _match_any(fp.get("html"), html)

        if not matched and fp.get("headers"):
            for header_name, pattern in fp["headers"].items():
                if _match_any(pattern, headers.get(header_name.lower(), "")):
                    matched = True
                    break

        if not matched and fp.get("meta"):
            for meta_name, pattern in fp["meta"].items():
                if _match_any(pattern, meta_tags.get(meta_name.lower(), "")):
                    matched = True
                    break

        if not matched and fp.get("scriptSrc"):
            for src in script_srcs:
                if _match_any(fp["scriptSrc"], src):
                    matched = True
                    break

        if matched:
            detected.append({"name": name, "categories": fp.get("cats", [])})

    return {"technologies": detected, "url": resp.url}
