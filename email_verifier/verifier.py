def verify_email_batch(emails: list) -> list:
    # সরল সিনট্যাক্স চেক – বাস্তবে এখানে SMTP যাচাই করা যাবে
    valid = []
    for e in emails:
        parts = e.split('@')
        if len(parts) == 2 and '.' in parts[1]:
            valid.append(e)
    return valid
