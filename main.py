import os
import io
import re
import base64
import uuid
import subprocess
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import httpx
from bs4 import BeautifulSoup
from PIL import Image, ImageChops

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в переменных окружения / .env файле!")

app = FastAPI(title="KaTeX Premium Render Bot API")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

GLOBAL_ASSETS = {"css": "", "js": "", "auto_js": ""}

@app.on_event("startup")
async def load_katex_assets():
    try:
        async with httpx.AsyncClient() as client:
            res_css = await client.get("https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css")
            res_js = await client.get("https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js")
            res_auto = await client.get("https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js")
            
            if all(r.status_code == 200 for r in [res_css, res_js, res_auto]):
                GLOBAL_ASSETS["css"] = res_css.text
                GLOBAL_ASSETS["js"] = res_js.text
                GLOBAL_ASSETS["auto_js"] = res_auto.text
                print("УСПЕХ: Локальный кэш KaTeX успешно инициализирован.")
    except Exception as e:
        print(f"ВНИМАНИЕ: Ошибка кэширования ресурсов: {e}")

def get_chromium_path():
    if os.getenv("CHROMIUM_PATH"):
        return os.getenv("CHROMIUM_PATH")
    paths = [
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
    ]
    for path in paths:
        if os.path.exists(path):
            return path
    return None

CHROMIUM_FLAGS = [
    "--headless", "--no-sandbox", "--disable-setuid-sandbox",
    "--disable-gpu", "--disable-dev-shm-usage", "--disable-software-rasterizer",
    "--hide-scrollbars", "--disable-dbus", "--no-zygote", "--default-background-color=eef2f3"
]

# Обновленная Pydantic-модель под множественные ответы
class MathMessage(BaseModel):
    chat_id: int | str
    caption: str = ""
    latex: str
    is_quiz: bool = False
    options: list[str] = []
    # Теперь принимаем список ID правильных ответов (например, [0, 2])
    correct_option_ids: list[int] = [] 

async def process_embedded_images(raw_text: str) -> tuple[str, str | None]:
    match = re.search(r'!\[.*?\]\((https?://[^\s)]+)\)', raw_text)
    img_base64 = None
    if match:
        img_url = match.group(1)
        raw_text = re.sub(r'\[?!\[.*?\]\(https?://[^\s)]+\)\]?\(.*?\)', '', raw_text)
        raw_text = re.sub(r'!\[.*?\]\((https?://[^\s)]+)\)', '', raw_text)
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(img_url, timeout=10.0)
                if resp.status_code == 200:
                    encoded = base64.b64encode(resp.content).decode('utf-8')
                    mime = "image/png" if "png" in img_url.lower() else "image/jpeg"
                    img_base64 = f"data:{mime};base64,{encoded}"
        except Exception as e:
            print(f"Ошибка парсинга изображения: {e}")
    return raw_text, img_base64

def extract_and_format_badge(text: str) -> tuple[str, str]:
    match = re.search(r'^(?:<strong>)?([А-Яа-яA-Za-z][-–]?\d+)\.?(?:</strong>)?\s*', text)
    if match:
        badge_text = match.group(1)
        return text[match.end():].strip(), f'<div class="task-badge">{badge_text}</div>'
    return text, ""

async def convert_to_katex_html(raw_text: str, options: list[str]) -> tuple[str, bool]:
    raw_text, embedded_img = await process_embedded_images(raw_text)
    has_image = embedded_img is not None or len(options) > 0

    if "<img" in raw_text:
        soup = BeautifulSoup(raw_text, "html.parser")
        for img in soup.find_all("img"):
            alt = img.get("alt", "")
            if alt: img.replace_with(f"${alt}$")
        text_content = soup.decode_contents()
    else:
        text_content = raw_text

    text_content = re.sub(r'</?(p|span|div)[^>]*>', ' ', text_content)
    text_content = re.sub(r'\s+', ' ', text_content).strip()
    text_content, badge_html = extract_and_format_badge(text_content)
    
    img_html = f'<img src="{embedded_img}" class="task-image">' if embedded_img else ''

    # Генерируем красивую HTML-сетку для вариантов ответов прямо внутри карточки
    options_html = ""
    if options:
        options_html = '<div class="options-grid">'
        # Буквенные маркеры для СТ (А, Б, В, Г, Д...)
        markers = ["А", "Б", "В", "Г", "Д", "Е", "Ж", "З", "И", "К"]
        for idx, opt in enumerate(options):
            marker = markers[idx] if idx < len(markers) else f"{idx + 1}"
            options_html += f"""
            <div class="option-item">
                <span class="option-marker">{marker}</span>
                <span class="option-text">{opt}</span>
            </div>
            """
        options_html += '</div>'

    css_include = f"<style>{GLOBAL_ASSETS['css']}</style>" if GLOBAL_ASSETS['css'] else '<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css">'
    js_include = f"<script>{GLOBAL_ASSETS['js']}</script>" if GLOBAL_ASSETS['js'] else '<script src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js"></script>'
    auto_render_include = f"<script>{GLOBAL_ASSETS['auto_js']}</script>" if GLOBAL_ASSETS['auto_js'] else '<script src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js"></script>'

    html_template = f"""
    <!DOCTYPE html>
    <html style="background-color: #eef2f3; margin: 0; padding: 0;">
    <head>
        <meta charset="utf-8">
        {css_include} {js_include} {auto_render_include}
        <style>
            body {{ margin: 0; padding: 40px; display: inline-block; background-color: #eef2f3; }}
            .card {{ font-family: 'Inter', system-ui, sans-serif; background-color: #ffffff; padding: 32px; border-radius: 16px; box-shadow: 0 10px 25px rgba(0,0,0,0.08); width: 640px; display: flex; flex-direction: column; gap: 24px; border: 1px solid rgba(0,0,0,0.03); }}
            .task-badge {{ align-self: flex-start; background: linear-gradient(135deg, #2563eb, #1d4ed8); color: #ffffff; font-weight: 700; font-size: 16px; padding: 6px 14px; border-radius: 8px; text-transform: uppercase; }}
            .text-container {{ font-size: 22px; line-height: 1.65; color: #1e293b; font-weight: 400; }}
            .task-image {{ display: block; max-width: 100%; max-height: 400px; width: auto; height: auto; border-radius: 10px; margin: 10px auto 0 auto; object-fit: contain; }}
            .options-grid {{ display: flex; flex-direction: column; gap: 12px; margin-top: 8px; border-top: 1px dashed #e2e8f0; padding-top: 20px; }}
            .option-item {{ display: flex; align-items: center; gap: 14px; padding: 12px 16px; background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; font-size: 20px; color: #334155; }}
            .option-marker {{ display: flex; align-items: center; justify-content: center; width: 32px; height: 32px; background-color: #e2e8f0; color: #1e293b; font-weight: 700; border-radius: 50%; font-size: 16px; }}
            .katex {{ font-size: 1.05em; color: #0f172a; }}
        </style>
    </head>
    <body>
        <div class="card" id="math-root">
            {badge_html}
            <div class="text-container" id="math-content">{text_content}</div>
            {img_html}
            {options_html}
        </div>
        <script>
            renderMathInElement(document.getElementById("math-root"), {{
                delimiters: [
                    {{left: "$$", right: "$$", display: true}},
                    {{left: "$", right: "$", display: false}}
                ],
                throwOnError : false
            }});
        </script>
    </body>
    </html>
    """
    return html_template, has_image

def autocrop_image(img_path: str) -> bytes:
    img = Image.open(img_path).convert("RGB")
    bg = Image.new(img.mode, img.size, (238, 242, 243))
    diff = ImageChops.difference(img, bg)
    diff = ImageChops.add(diff, diff, 2.0, -100)
    bbox = diff.getbbox()
    img_cropped = img.crop((max(0, bbox[0]-20), max(0, bbox[1]-20), min(img.size[0], bbox[2]+20), min(img.size[1], bbox[3]+20))) if bbox else img
    output = io.BytesIO()
    img_cropped.save(output, format="PNG")
    return output.getvalue()

@app.post("/send_math")
async def send_math(msg: MathMessage):
    unique_id = uuid.uuid4().hex
    html_file = f"/tmp/task_{unique_id}.html"
    img_file = f"/tmp/task_{unique_id}.png"

    try:
        # Передаем варианты ответов в рендер HTML шаблона
        html_code, has_image = await convert_to_katex_html(msg.latex, msg.options)
        
        with open(html_file, "w", encoding="utf-8") as f:
            f.write(html_code)
            
        canvas_width = 820
        canvas_height = 2500 if has_image else 1200
        browser_exec = get_chromium_path()
        if not browser_exec: raise RuntimeError("Chromium не найден!")

        cmd = [browser_exec, *CHROMIUM_FLAGS, f"--window-size={canvas_width},{canvas_height}", f"--screenshot={img_file}", html_file]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        
        if result.returncode != 0 or not os.path.exists(img_file):
            raise RuntimeError(f"Сбой Chromium: {result.stderr}")
            
        img_bytes = autocrop_image(img_file)
        
        # --- ОТПРАВКА В TELEGRAM ---
        async with httpx.AsyncClient() as client:
            # Отправляем карточку
            files = {"photo": ("task.png", img_bytes, "image/png")}
            photo_resp = await client.post(f"{TELEGRAM_API}/sendPhoto", data={"chat_id": msg.chat_id, "caption": msg.caption}, files=files, timeout=30.0)
            if photo_resp.status_code != 200: raise HTTPException(status_code=400, detail=photo_resp.text)
            photo_res_data = photo_resp.json()

            # Обработка опроса/викторины с валидацией длины массива
            if msg.is_quiz and msg.options:
                # 1. Валидация количества вариантов: Telegram строго принимает от 2 до 10
                if 2 <= len(msg.options) <= 10:
                    # Обрезаем строки вариантов до 100 символов (лимит Telegram API на один option)
                    clean_options = [opt[:100] for opt in msg.options] 
                    
                    # ПРОВЕРКА: Сколько правильных ответов пришло?
                    is_multiple = len(msg.correct_option_ids) > 1
                    
                    # Генерируем маркеры для кнопок опроса (А, Б, В...) на основе реального количества вариантов
                    markers = ["А", "Б", "В", "Г", "Д", "Е", "Ж", "З", "И", "К"]
                    poll_options = [f"Вариант {markers[i]}" for i in range(len(clean_options))]
                    
                    if is_multiple:
                        # Если правильных ответов несколько — переключаемся на 'regular' с множественным выбором
                        quiz_data = {
                            "chat_id": msg.chat_id,
                            "question": "Выберите правильные ответы (их несколько) 👇",
                            "options": poll_options,
                            "type": "regular",
                            "allows_multiple_answers": True,
                            "is_anonymous": True
                        }
                    else:
                        # Если ответ один — оставляем классический интерактивный Quiz
                        single_id = msg.correct_option_ids[0] if msg.correct_option_ids else 0
                        
                        # Защита: индекс правильного ответа не должен выходить за границы массива вариантов
                        if single_id >= len(clean_options):
                            single_id = 0
                            
                        quiz_data = {
                            "chat_id": msg.chat_id,
                            "question": "Выберите правильный ответ 👇",
                            "options": poll_options,
                            "type": "quiz",
                            "correct_option_id": single_id,
                            "is_anonymous": True
                        }
                    
                    quiz_resp = await client.post(f"{TELEGRAM_API}/sendPoll", json=quiz_data, timeout=20.0)
                    if quiz_resp.status_code == 200:
                        photo_res_data["attached_quiz"] = quiz_resp.json()
                    else:
                        print(f"DEBUG Ошибка sendPoll API: {quiz_resp.text}")
                else:
                    # Логируем проблему в консоль рендер-бота, но не крашим бэкенд
                    print(f"ВНИМАНИЕ: Опрос пропущен. Количество вариантов ({len(msg.options)}) вне лимитов Telegram (2-10).")

            return {"status": "success", "telegram_response": photo_res_data}

    finally:
        if os.path.exists(html_file): os.remove(html_file)
        if os.path.exists(img_file): os.remove(img_file)