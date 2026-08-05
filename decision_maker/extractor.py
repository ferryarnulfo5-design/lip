import spacy

nlp = spacy.load("en_core_web_sm")

def extract_decision_makers(pages: list) -> list:
    people = []
    titles = ["CEO", "Founder", "Owner", "Managing Director", "Marketing Manager", "President"]
    for page in pages:
        text = page.get("html", "")
        if not text:
            continue
        doc = nlp(text)
        # নাম ধরার চেষ্টা
        for ent in doc.ents:
            if ent.label_ == "PERSON":
                name = ent.text.strip()
                # টাইটেল আশেপাশে আছে কিনা দেখুন
                title = "unknown"
                for t in titles:
                    if t.lower() in text.lower():
                        title = t
                        break
                people.append({"name": name, "title": title, "source_url": page.get("url")})
    # ডুপ্লিকেট সরান
    unique = {p["name"]: p for p in people}.values()
    return list(unique)[:5]  # সর্বোচ্চ ৫ জন
