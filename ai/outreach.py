import requests, logging

logger = logging.getLogger(__name__)

def generate_outreach(website, people, lead_score, ollama_host):
    prompt = f"Write a short, friendly cold email pitch for {website} targeting {people}. Their lead score: {lead_score}. Keep it under 150 words."
    try:
        resp = requests.post(f"{ollama_host}/api/generate", json={
            "model": "tinyllama",
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": 200}
        }, timeout=60)
        data = resp.json()
        return {
            "subject": f"Quick idea for {website}",
            "body": data.get("response", "")
        }
    except Exception as e:
        logger.error(f"Outreach error: {e}")
        return {"subject": "", "body": ""}
