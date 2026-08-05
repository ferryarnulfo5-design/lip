import argparse, csv, json, logging, os, re, sys, time
from pathlib import Path
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
from website_intel import run_wappalyzer, run_lighthouse
from ai.qualify import qualify_lead
from ai.outreach import generate_outreach

WEBSITE_NAME_CANDIDATES = ["website", "url", "site", "domain", "web", "homepage", "web site", "website url"]
NAME_CANDIDATES = ["name", "business", "business_name", "company", "company name"]

URL_PATTERN = re.compile(r"(https?://)?(www\.)?[a-zA-Z0-9-]+\.[a-zA-Z]{2,}")

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
    return best_col if best_score >= 3 else None  # অন্তত ৩টা সারিতে মিললে তবেই ভরসা করা

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
            df = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
    except Exception as e:
        logger.error(f"ফাইল পড়তে সমস্যা হয়েছে: {e}")
        sys.exit(1)

    df.columns = [str(c).strip() for c in df.columns]
    return df

def run_pipeline(input_csv):
    Path("output").mkdir(exist_ok=True)
    enriched = []

    df = load_input(input_csv)

    website_col = find_column(df, WEBSITE_NAME_CANDIDATES) or find_website_column_by_content(df)
    if not website_col:
        logger.error(f"ওয়েবসাইট কলাম খুঁজে পাওয়া যায়নি। কলামগুলো: {list(df.columns)}")
        sys.exit(1)
    logger.info(f"ওয়েবসাইট কলাম হিসেবে '{website_col}' ব্যবহার করছি।")

    name_col = find_column(df, NAME_CANDIDATES)

    for idx, row in df.iterrows():
        website = str(row[website_col]).strip()
        if not website or website.lower() == "nan":
            logger.warning(f"সারি {idx+1}: ওয়েবসাইট খালি, বাদ দিচ্ছি।")
            continue
        if not website.startswith("http"):
            website = f"https://{website}"

        name = str(row[name_col]).strip() if name_col else website
        logger.info(f"Processing {idx+1}: {name} ({website})")

        try:
            pages = crawl_website(website)
            emails = extract_emails_from_pages(pages)
            verified = verify_email_batch(emails)
            people = extract_decision_makers(pages)
            tech = run_wappalyzer(website)
            lh_report = run_lighthouse(website)
            lead_score = qualify_lead(website, people, tech, lh_report, OLLAMA_HOST)
            outreach = generate_outreach(website, people, lead_score, OLLAMA_HOST)

            enriched.append({
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
            })
            logger.info(f"✅ Done: {len(verified)} emails, {len(people)} people")

        except Exception as e:
            logger.error(f"❌ Failed {name}: {str(e)}")
            enriched.append({"business_name": name, "website": website, "error": str(e)})

        time.sleep(2)

    if enriched:
        output_csv = "output/enriched_leads.csv"
        with open(output_csv, "w", newline="", encoding="utf-8") as out:
            writer = csv.DictWriter(out, fieldnames=enriched[0].keys())
            writer.writeheader()
            writer.writerows(enriched)
        logger.info(f"🎉 Pipeline complete. Output: {output_csv}")
    else:
        logger.warning("কোনো ফলাফল তৈরি হয়নি।")

def extract_emails_from_pages(pages):
    pattern = r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"
    emails = set()
    for page in pages:
        if page.get("html"):
            emails.update(re.findall(pattern, page["html"]))
    return list(emails)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Input CSV or XLSX file path")
    args = parser.parse_args()
    run_pipeline(args.input)
