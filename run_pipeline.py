import argparse, csv, json, logging, os, re, sys, threading, time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests
from bs4 import BeautifulSoup
import spacy

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")

from crawler.business_spider import crawl_website
from email_verifier.verifier import verify_email_batch
from decision_maker.extractor import extract_decision_makers
from website_intel import detect_technologies, run_lighthouse
from ai.qualify import qualify_lead
from ai.outreach import generate_outreach

WEBSITE_NAME_CANDIDATES = ["website", "url", "site", "domain", "web", "homepage", "web site", "website url"]
NAME_CANDIDATES = ["name", "business", "business_name", "company", "company name"]

URL_PATTERN = re.compile(r"(https?://)?(www\.)?[a-zA-Z0-9-]+\.[a-zA-Z]{2,}")

MAX_WORKERS = 5          # একসাথে কয়টা সাইট প্রসেস হবে (default; --workers দিয়ে বদলানো যায়)
SITE_TIMEOUT_SEC = 180   # প্রতিটা সাইটের জন্য hard cap — এর বেশি লাগলে বাদ দিয়ে পরের সাইটে

_write_lock = threading.Lock()


def find_column(df, candidates):
    """নাম মিলিয়ে কলাম খোঁজা — স্পেস/আন্ডারস্কোর/কেস ইগনোর করে।"""
    normalized = {re.sub(r"[\s_]+", "", c.strip().lower()): c for c in df.columns}
    for cand in candidates:
        key = re.sub(r"[\s_]+", "", cand)
        if key in normalized:
            return normalized[key]
    return None


def find_website_column_by_content(df):
    """নাম দিয়ে না পেলে, ডেটার ভেতরে URL-প্যাটার্ন খুঁজে কলাম বের করা।"""
    best_col, best_score = None, 0
    for col in df.columns:
        sample = df[col].astype(str).head(20)
        matches = sample.apply(lambda x: bool(URL_PATTERN.search(x))).sum()
        if matches > best_score:
            best_col, best_score = col, matches
    return best_col if best_score >= 3 else None


def load_input(input_path):
    path = Path(input_path)
    if not path.exists():
        logger.error(f"ফাইল পাওয়া যায়নি: {input_path}")
        sys.exit(1)

    ext = path.suffix.lower()
    try:
        if ext in (".xlsx", ".xls"):
            df = pd.read_excel(path, dtype=str)
        else:
            df = pd.read_csv(path, dtype=str, encoding="utf-8-sig", sep=None, engine="python")
    except Exception as e:
        logger.error(f"ফাইল পড়তে সমস্যা হয়েছে: {e}")
        sys.exit(1)

    df.columns = [str(c).strip() for c in df.columns]
    return df


def extract_emails_from_pages(pages):
    pattern = r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"
    emails = set()
    for page in pages:
        if page.get("html"):
            emails.update(re.findall(pattern, page["html"]))
    return list(emails)


class SiteProcessingError(Exception):
    """একটা সাইট প্রসেস করতে গিয়ে যেকোনো ধরনের ব্যর্থতার জন্য।"""
    pass


def process_site(name, website):
    """
    একটা সাইটের জন্য পুরো enrichment pipeline চালায়। থ্রেড-সেফ — কোনো shared
    state লেখে না, শুধু dict রিটার্ন করে। একাধিক থ্রেড থেকে সমান্তরালে কল
    করা নিরাপদ।
    """
    try:
        pages = crawl_website(website)
        emails = extract_emails_from_pages(pages)
        verified = verify_email_batch(emails)
        people = extract_decision_makers(pages)
        tech = detect_technologies(website)
        lh_report = run_lighthouse(website)
        lead_score = qualify_lead(website, people, tech, lh_report, OLLAMA_HOST)
        outreach = generate_outreach(website, people, lead_score, OLLAMA_HOST)

        return {
            "business_name": name,
            "website": website,
            "emails": ";".join(verified),
            "decision_makers": json.dumps(people),
            "tech_stack": json.dumps(tech),
            "lighthouse_score": lh_report.get("performance_score", ""),
            "lead_score": lead_score.get("score", ""),
            "ai_summary": lead_score.get("summary", ""),
            "outreach_subject": outreach.get("subject", ""),
            "outreach_body": outreach.get("body", ""),
        }
    except Exception as e:
        raise SiteProcessingError(str(e)) from e


def run_with_timeout(fn, args=(), timeout=SITE_TIMEOUT_SEC):
    """
    fn কে একটা আলাদা (ডেমন) থ্রেডে চালায়। timeout সেকেন্ডের মধ্যে শেষ না
    হলে TimeoutError তুলে মূল থ্রেড এগিয়ে যায়, যাতে একটা সাইট পুরো
    pipeline কে আটকে না রাখে।

    সতর্কতা: Python এ থ্রেড জোর করে kill করা যায় না, তাই timeout হলেও এই
    থ্রেডের ভেতরের কাজ (এবং তার থেকে spawn হওয়া subprocess, যদি নিজে থেকে
    টাইমআউট/kill না হয়) ব্যাকগ্রাউন্ডে চলতে থাকতে পারে। এজন্যই
    website_intel/__init__.py এর subprocess-level কিল+drain fix টা জরুরি —
    এটা সেই safety net এর উপরের স্তর, প্রতিস্থাপন না।
    """
    result = {}
    error = {}

    def target():
        try:
            result["value"] = fn(*args)
        except Exception as e:
            error["value"] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout)

    if t.is_alive():
        raise TimeoutError(f"{timeout}s এর মধ্যে শেষ হয়নি")
    if "value" in error:
        raise error["value"]
    return result.get("value")


def run_pipeline(input_csv, max_workers=MAX_WORKERS, site_timeout=SITE_TIMEOUT_SEC):
    Path("output").mkdir(exist_ok=True)
    df = load_input(input_csv)

    website_col = find_column(df, WEBSITE_NAME_CANDIDATES) or find_website_column_by_content(df)
    if not website_col:
        logger.error(f"ওয়েবসাইট কলাম খুঁজে পাওয়া যায়নি। কলামগুলো: {list(df.columns)}")
        sys.exit(1)
    logger.info(f"ওয়েবসাইট কলাম হিসেবে '{website_col}' ব্যবহার করছি।")

    name_col = find_column(df, NAME_CANDIDATES)

    tasks = []
    for idx, row in df.iterrows():
        website = str(row[website_col]).strip()
        if not website or website.lower() == "nan":
            logger.warning(f"সারি {idx+1}: ওয়েবসাইট খালি, বাদ দিচ্ছি।")
            continue
        if not website.startswith("http"):
            website = f"https://{website}"
        name = str(row[name_col]).strip() if name_col else website
        tasks.append((idx, name, website))

    total = len(tasks)
    if total == 0:
        logger.warning("প্রসেস করার মতো কোনো সাইট পাওয়া যায়নি।")
        return

    logger.info(f"{total}টা সাইট, {max_workers}টা worker দিয়ে শুরু হচ্ছে (per-site timeout {site_timeout}s)")

    fieldnames = [
        "business_name", "website", "emails", "decision_makers", "tech_stack",
        "lighthouse_score", "lead_score", "ai_summary", "outreach_subject",
        "outreach_body", "error",
    ]

    output_csv = "output/enriched_leads.csv"
    # হেডার আগেই লিখে, প্রতিটা রেজাল্ট আসার সাথে সাথে append করা হচ্ছে —
    # কোনো worker/runner মাঝপথে ক্র্যাশ করলেও এতক্ষণের কাজ হারাবে না।
    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=fieldnames).writeheader()

    def worker(task):
        idx, name, website = task
        logger.info(f"শুরু {idx + 1}/{total}: {name} ({website})")
        try:
            row = run_with_timeout(process_site, args=(name, website), timeout=site_timeout)
            logger.info(f"✅ শেষ: {name}")
            return row
        except TimeoutError:
            logger.error(f"⏱️ টাইমআউট ({site_timeout}s) — বাদ দিয়ে এগোচ্ছি: {name}")
            return {"business_name": name, "website": website, "error": f"timeout after {site_timeout}s"}
        except SiteProcessingError as e:
            logger.error(f"❌ ব্যর্থ {name}: {e}")
            return {"business_name": name, "website": website, "error": str(e)}
        except Exception as e:
            logger.error(f"❌ অপ্রত্যাশিত এরর {name}: {e}")
            return {"business_name": name, "website": website, "error": str(e)}

    done_count = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(worker, task) for task in tasks]
        for future in as_completed(futures):
            row = future.result()
            with _write_lock:
                with open(output_csv, "a", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writerow({k: row.get(k, "") for k in fieldnames})
            done_count += 1
            logger.info(f"অগ্রগতি: {done_count}/{total}")

    logger.info(f"🎉 Pipeline complete. Output: {output_csv}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Input CSV or XLSX file path")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS, help="সমান্তরাল worker সংখ্যা (default: 5)")
    parser.add_argument("--site-timeout", type=int, default=SITE_TIMEOUT_SEC, help="প্রতি সাইটের hard timeout, সেকেন্ডে (default: 180)")
    args = parser.parse_args()
    run_pipeline(args.input, max_workers=args.workers, site_timeout=args.site_timeout)
