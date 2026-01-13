# ежедневная выжимка (плейсхолдер)

def build_summary(llm, dialog_text: str) -> str:
    if not dialog_text.strip():
        return "За сегодня диалогов не было."

    prompt = (
        "Сделай короткую выжимку/заключение по переписке за день (3-6 пунктов):\n\n"
        f"{dialog_text}"
    )
    return llm.generate(prompt)