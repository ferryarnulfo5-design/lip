import argparse, csv, json, logging, os, re, subprocess, sys, time
from pathlib import Path
import requests
from bs4 import BeautifulSoup
import spacy

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")

# -------- Module imports (all local) ---------
from crawler.business_spider import crawl_website
from email_verifier.verifier import verify_email_batch
from decision_maker.extractor import extract_decision_makers
from website_intel import run_wappalyzer, run_lighthouse
from ai.qualify import qualify_lead
from ai.outreach import generate_outreach

def run_pipeline(input_csv):
    Path("output").mkdir(exist_ok=True)
    enriched = []

    with open(input_csv, "r") as f:
        reader = csv.DictReader(f)
        for idx, row in enumerate(reader, 1):
            website = row["website"].strip()
            if not website.startswith("http"):
                website = f"https://{website}"
            name = row.get("name", website)
            logger.info(f"Processing {idx}: {name} ({website})")

            try:
                pages = crawl_website(website)
                emails = extract_emails_from_pages(pages)
                verified = verify_email_batch(emails)
                people = extract_decision_makers(pages)
                tech = run_wappalyzer(website)
                lh_report = run_lighthouse(website)
                lead_score = qualify_lead(website, people, tech, lh_report, OLLAMA_HOST)
                outreach = generate_outreach(website, people, lead_score, OLLAMA_HOST)

                record = {
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
                enriched.append(record)
                logger.info(f"✅ Done: {len(verified)} emails, {len(people)} people")

            except Exception as e:
                logger.error(f"❌ Failed {name}: {str(e)}")
                enriched.append({"business_name": name, "website": website, "error": str(e)})

            time.sleep(2)

    if enriched:
        output_csv = "output/enriched_leads.csv"
        with open(output_csv, "w", newline="") as out:
            writer = csv.DictWriter(out, fieldnames=enriched[0].keys())
            writer.writeheader()
            writer.writerows(enriched)
        logger.info(f"🎉 Pipeline complete. Output: {output_csv}")

def extract_emails_from_pages(pages):
    pattern = r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"
    emails = set()
    for page in pages:
        if page.get("html"):
            emails.update(re.findall(pattern, page["html"]))
    return list(emails)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    args = parser.parse_args()
    run_pipeline(args.input)
