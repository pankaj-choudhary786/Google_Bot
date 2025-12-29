import time
import os
import json
import uuid
import threading
import requests
from flask import Flask, request, jsonify
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

app = Flask(__name__)

# --- CONFIGURATION ---
COOKIES_FILE = "cookies.json"

# GLOBAL STORAGE (Simple In-Memory Database)
# In a real production app, use a database (Supabase/Firebase)
JOBS = {} 

def get_driver():
    options = webdriver.ChromeOptions()
    options.add_argument("--headless") 
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled") 
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    return driver

def load_cookies(driver):
    if not os.path.exists(COOKIES_FILE):
        return False
    try:
        with open(COOKIES_FILE, 'r') as f:
            cookies = json.load(f)
        driver.get("https://aistudio.google.com/robots.txt") 
        for cookie in cookies:
            if 'expirationDate' in cookie:
                cookie['expiry'] = int(cookie['expirationDate'])
                del cookie['expirationDate']
            if 'sameSite' in cookie and cookie['sameSite'] not in ["Strict", "Lax", "None"]:
                del cookie['sameSite']
            try:
                driver.add_cookie(cookie)
            except:
                pass
        driver.get("https://aistudio.google.com/")
        time.sleep(3)
        return True
    except:
        return False

def download_file(url):
    local_filename = f"temp_{int(time.time())}_{uuid.uuid4().hex[:4]}.mp4"
    file_path = os.path.join(os.getcwd(), local_filename)
    with requests.get(url, stream=True) as r:
        r.raise_for_status()
        with open(file_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
    return file_path

# --- THE BACKGROUND WORKER ---
def process_video_task(job_id, video_url):
    JOBS[job_id]["status"] = "processing"
    driver = None
    final_video_path = None
    
    try:
        driver = get_driver()
        load_cookies(driver)
        
        # Verify Login
        if len(driver.find_elements(By.TAG_NAME, "textarea")) == 0:
             raise Exception("Login failed (Check cookies)")

        # Setup
        is_youtube = "youtube.com" in video_url or "youtu.be" in video_url
        if is_youtube:
            final_video_path = video_url
        else:
            final_video_path = download_file(video_url)

        # Upload
        if not is_youtube:
            add_btn = WebDriverWait(driver, 20).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(@aria-label, 'Insert') or contains(@aria-label, 'Add') or contains(text(), '+')]"))
            )
            add_btn.click()
            time.sleep(1)
            driver.find_element(By.CSS_SELECTOR, "input[type='file']").send_keys(os.path.abspath(final_video_path))
            time.sleep(20) # Wait for upload

        # Prompt
        base_prompt = "Generate a frame-by-frame transcript with timestamps."
        final_prompt = f"{video_url} {base_prompt}" if is_youtube else base_prompt
        
        box = driver.find_element(By.TAG_NAME, "textarea")
        box.click()
        box.send_keys(final_prompt)
        time.sleep(1)
        box.send_keys(Keys.CONTROL, Keys.RETURN)

        # Wait
        time.sleep(15)
        start_t = time.time()
        last_len = 0
        stable_count = 0
        final_text = ""
        
        while (time.time() - start_t) < 600:
            text = driver.find_element(By.TAG_NAME, "body").text
            if len(text) > last_len:
                last_len = len(text)
                stable_count = 0
            else:
                stable_count += 1
            if stable_count > 5 and len(text) > 500:
                final_text = text
                break
            time.sleep(2)

        # Cleanup Text
        garbage = ["Google AI models may make mistakes", "Run settings", "Response ready"]
        for junk in garbage:
            if junk in final_text:
                final_text = final_text.split(junk)[0]

        # SAVE RESULT
        JOBS[job_id]["status"] = "completed"
        JOBS[job_id]["transcript"] = final_text.strip()

    except Exception as e:
        JOBS[job_id]["status"] = "failed"
        JOBS[job_id]["error"] = str(e)
    
    finally:
        if driver: driver.quit()
        if final_video_path and not is_youtube and os.path.exists(final_video_path):
            try: os.remove(final_video_path)
            except: pass

# --- ENDPOINTS ---

@app.route('/process', methods=['POST'])
def start_process():
    data = request.json
    if not data or 'url' not in data:
        return jsonify({"error": "No url provided"}), 400
    
    # Create a generic Job ID
    job_id = str(uuid.uuid4())
    JOBS[job_id] = {"status": "pending", "submitted_at": time.time()}
    
    # Start thread
    thread = threading.Thread(target=process_video_task, args=(job_id, data['url']))
    thread.start()
    
    # Reply IMMEDIATELY
    return jsonify({
        "status": "started",
        "job_id": job_id,
        "message": "Use /result/<job_id> to check status."
    })

@app.route('/result/<job_id>', methods=['GET'])
def get_result(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    
    return jsonify(job)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
