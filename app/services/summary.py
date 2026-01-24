# ежедневная выжимка (плейсхолдер)

def build_summary(llm, dialog_text: str) -> str:
    if not dialog_text.strip():
        return "За сегодня диалогов не было."
    prompt = f"Переписка за день:\n\n{dialog_text}"
    return llm.generate(prompt, mode="summary")

def build_memory(llm, dialog_text: str, existing_memory: str | None = None) -> str:
    if not dialog_text.strip():
        return (existing_memory or "").strip()
    mem = (existing_memory or "").strip()
    prompt = (
        "EXISTING_MEMORY:\n"
        + (mem if mem else "-")
        + "\n\n"
        "DIALOG:\n"
        + dialog_text
    )
    updated = llm.generate(prompt, mode="memory") or ""
    updated = updated.strip()
    if not updated:
        return mem
    return updated[:800]
