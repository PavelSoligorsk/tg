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
import markdown  # Добавляем для парсинга маркдаун-таблиц
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

class MathMessage(BaseModel):
    chat_id: int | str
    caption: str = ""
    latex: str
    is_quiz: bool = False
    options: list[str] = []
    correct_option_ids: list[int] = [] 
    difficulty: int

def extract_and_format_badge(text: str) -> tuple[str, str]:
    match = re.search(r'^(?:<strong>)?([А-Яа-яA-Za-z][-–]?\d+)\.?(?:</strong>)?\s*', text)
    if match:
        badge_text = match.group(1)
        return text[match.end():].strip(), f'<div class="task-badge">{badge_text}</div>'
    return text, ""

async def convert_to_katex_html(raw_text: str, options: list[str]) -> tuple[str, bool]:
    # 1. Сначала извлекаем номер задачи (А1, В10) до парсинга маркдауна
    raw_text, badge_html = extract_and_format_badge(raw_text)

    # ЖЕСТКИЙ ФИКС: Если перед таблицей нет пустой строки, Markdown её не распарсит.
    # Этот регекс принудительно вставляет \n\n перед первой '|', если там идет текст вплотную.
    raw_text = re.sub(r'([^\n])\s*\n\s*\|', r'\1\n\n|', raw_text)

    # 2. Конвертируем основной текст Markdown (включая таблицы) в HTML.
    html_content = markdown.markdown(raw_text, extensions=['tables'])
    
    # Флаг для динамического увеличения высоты холста в Chromium
    has_image = "img" in html_content or "table" in html_content or len(options) > 0

    # 3. Прогоняем контент через BeautifulSoup для локализации картинок
    soup = BeautifulSoup(html_content, "html.parser")
    
    async with httpx.AsyncClient() as client:
        for img in soup.find_all("img"):
            src = img.get("src", "")
            if src.startswith("http"):
                try:
                    resp = await client.get(src, timeout=12.0)
                    if resp.status_code == 200:
                        encoded = base64.b64encode(resp.content).decode('utf-8')
                        mime = "image/png" if "png" in src.lower() else "image/jpeg"
                        img["src"] = f"data:{mime};base64,{encoded}"
                        img["class"] = "task-rendered-img"
                except Exception as e:
                    print(f"Ошибка скачивания встроенного изображения {src}: {e}")

    text_content = soup.decode_contents()
    
    # 4. Генерируем HTML-сетку вариантов ответов
    options_html = ""
    if options:
        options_html = '<div class="options-grid">'
        markers = ["А", "Б", "В", "Г", "Д", "Е", "Ж", "З", "И", "К"]
        for idx, opt in enumerate(options):
            marker = markers[idx] if idx < len(markers) else f"{idx + 1}"
            
            # Рендерим маркдаун для текста варианта
            opt_html = markdown.markdown(opt)
            opt_html = re.sub(r'^<p>|</p>$', '', opt_html).strip()
            
            options_html += f"""
            <div class="option-item">
                <span class="option-marker">{marker}</span>
                <span class="option-text">{opt_html}</span>
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
            .card {{ font-family: 'Inter', system-ui, sans-serif; background-color: #ffffff; padding: 32px; border-radius: 16px; box-shadow: 0 10px 25px rgba(0,0,0,0.08); width: 660px; display: flex; flex-direction: column; gap: 24px; border: 1px solid rgba(0,0,0,0.03); }}
            .task-badge {{ align-self: flex-start; background: linear-gradient(135deg, #2563eb, #1d4ed8); color: #ffffff; font-weight: 700; font-size: 16px; padding: 6px 14px; border-radius: 8px; text-transform: uppercase; }}
            .text-container {{ font-size: 22px; line-height: 1.65; color: #1e293b; font-weight: 400; }}
            
            /* --- СТИЛИ ДЛЯ ТАБЛИЦ --- */
            table {{ 
                width: 100%; 
                border-collapse: separate; 
                border-spacing: 0; 
                margin: 24px 0; 
                font-size: 19px; 
                background-color: #f8fafc; 
                border-radius: 12px; 
                overflow: hidden; 
                border: 1px solid #e2e8f0; 
            }}
            th, td {{ 
                padding: 14px 16px; 
                vertical-align: middle; 
                text-align: center;
                border-bottom: 1px solid #e2e8f0;
                border-right: 1px solid #e2e8f0; /* ИСПРАВЛЕНО: было сломанное border-r */
            }}
            th:last-child, td:last-child {{
                border-right: none;
            }}
            tr:last-child td {{
                border-bottom: none;
            }}
            th {{ 
                background-color: #f1f5f9; 
                color: #1e293b; 
                font-weight: 700; 
            }}
            td {{
                background-color: #ffffff;
                color: #334155;
            }}
            
            /* --- АДАПТИВНЫЕ КАРТИНКИ --- */
            .task-rendered-img {{ display: block; max-width: 100%; max-height: 420px; width: auto; height: auto; border-radius: 8px; margin: 12px auto; object-fit: contain; }}
            td .task-rendered-img {{ max-height: 140px; margin: 4px auto; border-radius: 4px; }}
            td p {{ margin: 0; }}

            /* --- СЕТКА ВАРИАНТОВ ОТВЕТОВ --- */
            .options-grid {{ display: flex; flex-direction: column; gap: 12px; margin-top: 8px; border-top: 1px dashed #e2e8f0; padding-top: 20px; }}
            .option-item {{ display: flex; align-items: center; gap: 14px; padding: 12px 16px; background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; font-size: 20px; color: #334155; }}
            .option-marker {{ display: flex; align-items: center; justify-content: center; width: 32px; height: 32px; background-color: #e2e8f0; color: #1e293b; font-weight: 700; border-radius: 50%; font-size: 16px; flex-shrink: 0; }}
            .option-text p {{ margin: 0; }}
            .katex {{ font-size: 1.05em; color: #0f172a; }}
        </style>
    </head>
    <body>
        <div class="card" id="math-root">
            {badge_html}
            <div class="text-container" id="math-content">{text_content}</div>
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
        html_code, has_image = await convert_to_katex_html(msg.latex, msg.options)
        
        with open(html_file, "w", encoding="utf-8") as f:
            f.write(html_code)
            
        canvas_width = 840
        canvas_height = 3200 if has_image else 1200
        browser_exec = get_chromium_path()
        if not browser_exec: raise RuntimeError("Chromium не найден!")

        cmd = [browser_exec, *CHROMIUM_FLAGS, f"--window-size={canvas_width},{canvas_height}", f"--screenshot={img_file}", html_file]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        
        if result.returncode != 0 or not os.path.exists(img_file):
            raise RuntimeError(f"Сбой Chromium: {result.stderr}")
            
        img_bytes = autocrop_image(img_file)
        
        async with httpx.AsyncClient() as client:
            files = {"photo": ("task.png", img_bytes, "image/png")}
            photo_resp = await client.post(f"{TELEGRAM_API}/sendPhoto", data={"chat_id": msg.chat_id, "caption": msg.caption}, files=files, timeout=30.0)
            if photo_resp.status_code != 200: raise HTTPException(status_code=400, detail=photo_resp.text)
            photo_res_data = photo_resp.json()

            if msg.is_quiz and msg.options:
                if 2 <= len(msg.options) <= 10:
                    clean_options = [opt[:100] for opt in msg.options] 
                    is_multiple = len(msg.correct_option_ids) > 1
                    
                    markers = ["А", "Б", "В", "Г", "Д", "Е", "Ж", "З", "И", "К"]
                    poll_options = [f"Вариант {markers[i]}" for i in range(len(clean_options))]
                    
                    if is_multiple:
                        quiz_data = {
                            "chat_id": msg.chat_id,
                            "question": "Выберите правильные ответы (их несколько) 👇",
                            "options": poll_options,
                            "type": "regular",
                            "allows_multiple_answers": True,
                            "is_anonymous": True
                        }
                    else:
                        single_id = msg.correct_option_ids[0] if msg.correct_option_ids else 0
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
                    print(f"ВНИМАНИЕ: Опрос пропущен. Количество вариантов ({len(msg.options)}) вне лимитов Telegram (2-10).")

            return {"status": "success", "telegram_response": photo_res_data}

    finally:
        if os.path.exists(html_file): os.remove(html_file)
        if os.path.exists(img_file): os.remove(img_file)