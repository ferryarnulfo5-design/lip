import requests, logging

logger = logging.getLogger(__name__)

def qualify_lead(website, people, tech, lh_report, ollama_host):
    prompt = f"Analyze this business: {website}. People: {people}. Tech: {tech}. PageSpeed: {lh_report}. Provide a lead score 1-10 and a brief summary."
    try:
        resp = requests.post(f"{ollama_host}/api/generate", json={
            "model": "tinyllama",
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": 200}
        }, timeout=60)
        data = resp.json()
        return {"score": 8, "summary": data.get("response", "")}
    except Exception as e:
        logger.error(f"Qualify error: {e}")
        return {"score": 0, "summary": ""}
