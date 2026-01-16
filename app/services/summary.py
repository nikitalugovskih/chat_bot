# ежедневная выжимка (плейсхолдер)

def build_summary(llm, dialog_text: str) -> str:
    if not dialog_text.strip():
        return "За сегодня диалогов не было."
    prompt = f"Переписка за день:\n\n{dialog_text}"
    return llm.generate(prompt, mode="summary")