import os
import io
import re
import time
import base64
import tempfile
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import httpx
from bs4 import BeautifulSoup
from html2image import Html2Image
from PIL import Image, ImageChops

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в переменных окружения / .env файле!")

app = FastAPI(title="KaTeX Premium Render Bot API")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Глобальные переменные для локального кэша KaTeX (чтобы рендерить без сети)
KATEX_CSS = ""
KATEX_JS = ""
KATEX_AUTO_RENDER_JS = ""

@app.on_event("startup")
async def load_katex_assets():
    """Скачивает ресурсы KaTeX один раз при старте, чтобы рендерить инлайново без CDN"""
    global KATEX_CSS, KATEX_JS, KATEX_AUTO_RENDER_JS
    try:
        async with httpx.AsyncClient() as client:
            css_res = await client.get("https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css")
            js_res = await client.get("https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js")
            auto_res = await client.get("https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js")
            
            if all(r.status_code == 200 for r in [css_res, js_res, auto_res]):
                KATEX_CSS = css_res.text
                KATEX_JS = js_res.text
                KATEX_AUTO_RENDER_JS = auto_res.text
                print("УСПЕХ: Ресурсы KaTeX успешно кэшированы локально.")
                return
    except Exception as e:
        print(f"ВНИМАНИЕ: Не удалось кэшировать KaTeX локально ({e}). Будет использован стандартный CDN.")

def get_chromium_path():
    if os.getenv("CHROMIUM_PATH"):
        return os.getenv("CHROMIUM_PATH")
    paths = [
        "/usr/bin/chromium",            # Linux (Railway Docker)
        "/usr/bin/chromium-browser",    
        "/usr/bin/google-chrome",       
        "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
        "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe"
    ]
    for path in paths:
        if os.path.exists(path):
            return path
    return None

browser_path = get_chromium_path()
hti_args = {
    'custom_flags': [
        '--headless',              # Стандартный headless режим
        '--no-sandbox',
        '--disable-gpu',
        '--hide-scrollbars',
        '--disable-dev-shm-usage', # Спасает от нехватки памяти RAM в Docker
        '--disable-dbus',          # Игнорирует отсутствие системной шины d-bus
        '--no-zygote',
        '--single-process',
        '--window-size=1280,1024', # Явно задаем размер окна под наш Xvfb
        '--default-background-color=eef2f3'
    ]
}

if browser_path:
    hti_args['browser_executable'] = browser_path

hti = Html2Image(**hti_args)

class MathMessage(BaseModel):
    chat_id: int | str
    caption: str = ""
    latex: str

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
            print(f"Ошибка скачивания встроенного изображения: {e}")
            
    return raw_text, img_base64

def extract_and_format_badge(text: str) -> tuple[str, str]:
    match = re.search(r'^(?:<strong>)?([А-Яа-яA-Za-z][-–]?\d+)\.?(?:</strong>)?\s*', text)
    if match:
        badge_text = match.group(1)
        clean_text = text[match.end():].strip()
        badge_html = f'<div class="task-badge">{badge_text}</div>'
        return clean_text, badge_html
    return text, ""

async def convert_to_katex_html(raw_text: str) -> tuple[str, bool]:
    raw_text, embedded_img = await process_embedded_images(raw_text)
    has_image = embedded_img is not None

    if "<img" in raw_text:
        soup = BeautifulSoup(raw_text, "html.parser")
        for img in soup.find_all("img"):
            alt = img.get("alt", "")
            if alt:
                img.replace_with(f"${alt}$")
        text_content = soup.decode_contents()
    else:
        text_content = raw_text

    text_content = re.sub(r'</?(p|span|div)[^>]*>', ' ', text_content)
    text_content = re.sub(r'\s+', ' ', text_content).strip()

    text_content, badge_html = extract_and_format_badge(text_content)
    img_html = f'<img src="{embedded_img}" class="task-image">' if embedded_img else ''

    # Если ресурсы загружены в память — вшиваем их напрямую (inline), иначе берем CDN
    css_include = f"<style>{KATEX_CSS}</style>" if KATEX_CSS else '<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css">'
    js_include = f"<script>{KATEX_JS}</script>" if KATEX_JS else '<script src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js"></script>'
    auto_render_include = f"<script>{KATEX_AUTO_RENDER_JS}</script>" if KATEX_AUTO_RENDER_JS else '<script src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js"></script>'

    html_template = f"""
    <!DOCTYPE html>
    <html style="background-color: #eef2f3; margin: 0; padding: 0;">
    <head>
        <meta charset="utf-8">
        {css_include}
        {js_include}
        {auto_render_include}
        <style>
            body {{
                margin: 0;
                padding: 40px;
                display: inline-block;
                background-color: #eef2f3;
            }}
            .card {{
                font-family: 'Inter', 'Segoe UI', system-ui, -apple-system, sans-serif;
                background-color: #ffffff;
                padding: 32px;
                border-radius: 16px;
                box-shadow: 0 10px 25px rgba(0, 0, 0, 0.08), 0 4px 10px rgba(0, 0, 0, 0.04);
                width: 620px;
                display: flex;
                flex-direction: column;
                gap: 20px;
                border: 1px solid rgba(0, 0, 0, 0.03);
            }}
            .task-badge {{
                align-self: flex-start;
                background: linear-gradient(135deg, #2563eb, #1d4ed8);
                color: #ffffff;
                font-weight: 700;
                font-size: 16px;
                padding: 6px 14px;
                border-radius: 8px;
                letter-spacing: 0.5px;
                text-transform: uppercase;
                box-shadow: 0 4px 12px rgba(37, 99, 235, 0.2);
            }}
            .text-container {{
                font-size: 22px;
                line-height: 1.65;
                color: #1e293b;
                font-weight: 400;
            }}
            .task-image {{
                display: block;
                max-width: 100%;
                max-height: 450px;
                width: auto;
                height: auto;
                border-radius: 10px;
                margin: 10px auto 0 auto;
                object-fit: contain;
                background-color: #fafafa;
                border: 1px dashed #e2e8f0;
                padding: 8px;
            }}
            .katex {{
                font-size: 1.05em;
                color: #0f172a;
            }}
        </style>
    </head>
    <body>
        <div class="card">
            {badge_html}
            <div class="text-container" id="math-content">{text_content}</div>
            {img_html}
        </div>
        <script>
            // Моментальный синхронный рендеринг математики
            renderMathInElement(document.getElementById("math-content"), {{
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
    
    if bbox:
        padding = 20
        left = max(0, bbox[0] - padding)
        top = max(0, bbox[1] - padding)
        right = min(img.size[0], bbox[2] + padding)
        bottom = min(img.size[1], bbox[3] + padding)
        img_cropped = img.crop((left, top, right, bottom))
    else:
        img_cropped = img

    output = io.BytesIO()
    img_cropped.save(output, format="PNG")
    return output.getvalue()

@app.post("/send_math")
async def send_math(msg: MathMessage):
    # Работаем строго во временной директории /tmp/
    with tempfile.NamedTemporaryFile(suffix=".html", mode="w", encoding="utf-8", delete=False) as tf_html, \
         tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf_img:
        
        html_file = tf_html.name
        img_file = tf_img.name

    try:
        html_code, has_image = await convert_to_katex_html(msg.latex)
        
        with open(html_file, "w", encoding="utf-8") as f:
            f.write(html_code)
        
        canvas_width = 820
        canvas_height = 2200 if has_image else 1000
        
        # Рендерим через явное указание локального пути для стабильности в Linux
        try:
            hti.screenshot(html_file=html_file, save_as=img_file, size=(canvas_width, canvas_height))
        except Exception as e:
            if not os.path.exists(img_file):
                print(f"DEBUG: Провал вызова screenshot: {e}")
                raise

        # Короткое ожидание финализации записи на диск Linux-контейнера
        time.sleep(0.3)
        
        # Если файл по какой-то причине отсутствует или пуст — даем ему вторую попытку с задержкой
        if not os.path.exists(img_file) or os.path.getsize(img_file) == 0:
            time.sleep(0.7)
            if not os.path.exists(img_file) or os.path.getsize(img_file) == 0:
                raise FileNotFoundError("Браузер не смог сгенерировать непустой скриншот (0 байт).")
            
        # Обрезка полей изображения
        img_bytes = autocrop_image(img_file)
        
        # Отправка в Telegram API
        async with httpx.AsyncClient() as client:
            files = {"photo": ("task.png", img_bytes, "image/png")}
            data = {"chat_id": msg.chat_id, "caption": msg.caption}
            resp = await client.post(f"{TELEGRAM_API}/sendPhoto", data=data, files=files, timeout=30.0)
            
        res_data = resp.json()
        if resp.status_code != 200:
            print(f"DEBUG: Ошибка Telegram API: {res_data}")
            raise HTTPException(status_code=resp.status_code, detail=f"Telegram Error: {res_data}")
            
        return {"status": "success", "telegram_response": res_data}

    except Exception as e:
        print(f"CRITICAL Блок Исключения: {repr(e)}")
        raise HTTPException(status_code=500, detail=f"Ошибка адаптивного рендеринга: {str(e)}")
        
    finally:
        # Гарантированное удаление временных файлов с диска
        if os.path.exists(html_file): os.remove(html_file)
        if os.path.exists(img_file): os.remove(img_file)