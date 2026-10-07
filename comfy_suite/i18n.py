"""Interface translations. Strings are written in English; ``tr`` looks them up for the
current language (Ukrainian ships built in)."""

from __future__ import annotations

import locale
import os

UK: dict[str, str] = {
    "AI for PhotoSuite": "ШІ для PhotoSuite",
    "Generate": "Генерувати",
    "Refine": "Доопрацювати",
    "Fill": "Заповнити",
    "Upscale": "Збільшити",
    "Live": "Наживо",
    "Custom Graph": "Власний граф",
    "Settings": "Налаштування",
    "Connect": "Підключитися",
    "Connecting…": "Підключення…",
    "Connected": "Підключено",
    "Disconnected": "Не підключено",
    "Style": "Стиль",
    "Edit style": "Редагувати стиль",
    "Prompt": "Запит",
    "Describe the image or the content to fill…": "Опишіть зображення або вміст для заповнення…",
    "Negative prompt": "Негативний запит",
    "What to avoid…": "Чого уникати…",
    "Strength": "Сила",
    "Batch": "Кількість",
    "Seed": "Зерно (seed)",
    "Fixed seed": "Фіксоване зерно",
    "Random": "Випадкове",
    "Use selection": "Використовувати виділення",
    "Control layers": "Керувальні шари",
    "Add control layer": "Додати керувальний шар",
    "Canvas": "Полотно",
    "Auto": "Авто",
    "Preprocess": "Попередня обробка",
    "Remove": "Видалити",
    "Cancel": "Скасувати",
    "Cancel all": "Скасувати все",
    "History": "Історія",
    "Apply": "Застосувати",
    "Apply all": "Застосувати все",
    "Discard": "Відкинути",
    "Copy prompt": "Копіювати запит",
    "Reuse seed": "Використати це зерно",
    "Clear history": "Очистити історію",
    "Queued": "У черзі",
    "Running": "Виконується",
    "Done": "Готово",
    "Failed": "Помилка",
    "Cancelled": "Скасовано",
    "Factor": "Множник",
    "Upscale model": "Модель збільшення",
    "None (Lanczos)": "Немає (Lanczos)",
    "Refine after upscaling": "Доопрацювати після збільшення",
    "Target size": "Цільовий розмір",
    "Start": "Старт",
    "Stop": "Стоп",
    "Apply to document": "Застосувати до документа",
    "Live painting regenerates the canvas while you paint.": "Живе малювання перегенеровує полотно, поки ви малюєте.",
    "Interval": "Інтервал",
    "Load workflow…": "Завантажити workflow…",
    "No workflow loaded": "Workflow не завантажено",
    "Run": "Запустити",
    "ComfyUI server": "Сервер ComfyUI",
    "PhotoSuite control port": "Порт керування PhotoSuite",
    "Token file": "Файл токена",
    "Token": "Токен",
    "Exchange folder": "Папка обміну",
    "PhotoSuite executable": "Виконуваний файл PhotoSuite",
    "Selection grow (px)": "Розширення виділення (px)",
    "Selection feather (px)": "Розмиття краю виділення (px)",
    "Context around selection": "Контекст навколо виділення",
    "History size": "Розмір історії",
    "Keep window on top": "Вікно поверх інших",
    "Language": "Мова",
    "Test connection": "Перевірити з'єднання",
    "Save": "Зберегти",
    "Save as new": "Зберегти як новий",
    "Close": "Закрити",
    "Name": "Назва",
    "Architecture": "Архітектура",
    "Checkpoint": "Чекпойнт",
    "VAE": "VAE",
    "From checkpoint": "З чекпойнта",
    "Style prompt": "Запит стилю",
    "Sampler": "Семплер",
    "Scheduler": "Планувальник",
    "Steps": "Кроки",
    "Guidance (Flux)": "Guidance (Flux)",
    "CLIP skip": "CLIP skip",
    "Native resolution": "Рідна роздільність",
    "Live sampler": "Семплер (наживо)",
    "Live steps": "Кроки (наживо)",
    "Live CFG": "CFG (наживо)",
    "Add LoRA": "Додати LoRA",
    "Strength (LoRA)": "Сила (LoRA)",
    "Model": "Модель",
    "Mode": "Режим",
    "Layer": "Шар",
    "Range": "Діапазон",
    "No document open in PhotoSuite": "У PhotoSuite немає відкритого документа",
    "Not connected": "Немає з'єднання",
    "Restart required to change the language.": "Щоб змінити мову, перезапустіть програму.",
    "reading the document": "читання документа",
    "done": "готово",
    "started": "розпочато",
}

_lang = ""


def set_language(lang: str) -> None:
    global _lang
    _lang = lang


def language() -> str:
    if _lang:
        return _lang
    env = os.environ.get("LANG", "") or (locale.getlocale()[0] or "")
    return "uk" if env.lower().startswith(("uk", "ukrainian")) else "en"


def tr(text: str) -> str:
    if language() == "uk":
        return UK.get(text, text)
    return text
