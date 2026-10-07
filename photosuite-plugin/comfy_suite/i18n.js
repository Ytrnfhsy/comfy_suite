/* Interface strings: written in English, translated to Ukrainian here. */
(function (CS) {
  "use strict";
  var UK = {
    "Generate": "Генерувати", "Refine": "Доопрацювати", "Fill": "Заповнити", "Upscale": "Збільшити",
    "Live": "Наживо", "Custom": "Власний граф", "Settings": "Налаштування", "Connect": "Підключитися",
    "Connecting…": "Підключення…", "Connected": "Підключено", "Not connected": "Немає з'єднання",
    "Style": "Стиль", "Edit style": "Редагувати стиль", "Prompt": "Запит",
    "Describe the image or the content to fill…": "Опишіть зображення або вміст для заповнення…",
    "Negative prompt": "Негативний запит", "What to avoid…": "Чого уникати…", "Strength": "Сила",
    "Batch": "Кількість", "Seed": "Зерно", "Fixed": "Фіксоване", "Use selection": "Використовувати виділення",
    "Control layers": "Керувальні шари", "Add control": "Додати керування", "Canvas": "Полотно", "Image file…": "Файл зображення…",
    "Auto": "Авто", "Preprocess": "Попередня обробка", "Remove": "Видалити", "Cancel": "Скасувати",
    "History": "Історія", "Apply": "Застосувати", "Apply all": "Застосувати все", "Discard": "Відкинути",
    "Reuse seed": "Повторити зерно", "Reuse prompt": "Повторити запит", "Clear": "Очистити",
    "Queued": "У черзі", "Running": "Виконується", "Factor": "Множник", "Upscale model": "Модель збільшення",
    "None (Lanczos)": "Немає (Lanczos)", "Refine after upscaling": "Доопрацювати після збільшення",
    "Target size": "Цільовий розмір", "Start": "Старт", "Stop": "Стоп",
    "Live painting regenerates the canvas while you paint.": "Живе малювання перегенеровує полотно, поки ви малюєте.",
    "Load workflow…": "Завантажити workflow…", "Or paste the API JSON here": "Або вставте сюди API JSON",
    "Run": "Запустити", "ComfyUI server": "Сервер ComfyUI", "Selection grow (px)": "Розширення виділення (px)",
    "Selection feather (px)": "Розмиття краю (px)", "Context around selection": "Контекст навколо виділення",
    "Live interval (ms)": "Інтервал наживо (мс)", "Language": "Мова", "Theme": "Тема", "Dark": "Темна", "Light": "Світла", "Save": "Зберегти", "Close": "Закрити",
    "Name": "Назва", "Architecture": "Архітектура", "Checkpoint": "Чекпойнт", "From checkpoint": "З чекпойнта",
    "Style prompt": "Запит стилю", "Sampler": "Семплер", "Scheduler": "Планувальник", "Steps": "Кроки",
    "Native resolution": "Рідна роздільність", "Live sampling": "Семплінг наживо", "Add LoRA": "Додати LoRA",
    "Save as new": "Зберегти як новий", "Delete": "Видалити", "Model": "Модель", "Mode": "Режим", "Source": "Джерело",
    "No document open in PhotoSuite": "У PhotoSuite немає відкритого документа",
    "reading the document": "читання документа", "done": "готово",
    "Result added as a layer. Press Enter to confirm its placement.": "Результат додано шаром. Натисніть Enter, щоб підтвердити розміщення.",
    "Upscaled image opened as a new document.": "Збільшене зображення відкрито новим документом.",
    "Start ComfyUI with --enable-cors-header so the panel can reach it.": "Запустіть ComfyUI з параметром --enable-cors-header, щоб панель могла до нього звертатися.",
    "Styles are kept for this session. To keep them, copy this into config.js (styles):": "Стилі зберігаються до кінця сесії. Щоб зберегти назавжди, скопіюйте це в config.js (styles):",
    "Settings are kept for this session; set defaults in config.js.": "Налаштування діють до кінця сесії; значення за замовчуванням — у config.js."
  };
  var lang = "";
  CS.setLanguage = function (l) { lang = l || ""; };
  CS.language = function () {
    if (lang) return lang;
    var langs = [];
    if (typeof navigator !== "undefined") langs = langs.concat(navigator.languages || [], navigator.language || []);
    try { langs.push(Intl.DateTimeFormat().resolvedOptions().locale); } catch (e) { /* no Intl */ }
    return langs.some(function (l) { return /^uk/i.test(l || ""); }) ? "uk" : "en";
  };
  CS.tr = function (s) { return CS.language() === "uk" && UK[s] ? UK[s] : s; };
  CS.UK = UK;
})(typeof window !== "undefined" ? (window.CS = window.CS || {}) : (globalThis.CS = globalThis.CS || {}));
