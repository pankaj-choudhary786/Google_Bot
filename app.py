import time
import os
import json
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

def get_driver():
    options = webdriver.ChromeOptions()
    # RENDER/LINUX SETTINGS (Crucial for Server)
    options.add_argument("--headless") 
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    
    # Anti-detection settings
    options.add_argument("--disable-blink-features=AutomationControlled") 
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    return driver

def load_cookies(driver):
    """Injects the cookies from your json file"""
    if not os.path.exists(COOKIES_FILE):
        print("CRITICAL: cookies.json not found!")
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
    except Exception as e:
        print(f"Cookie Error: {e}")
        return False

def download_file(url):
    print(f"-> Downloading video...")
    local_filename = f"temp_{int(time.time())}.mp4"
    file_path = os.path.join(os.getcwd(), local_filename)
    with requests.get(url, stream=True) as r:
        r.raise_for_status()
        with open(file_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
    return file_path

@app.route('/process', methods=['POST'])
def process_video():
    # 1. READ INPUT
    data = request.json
    if not data or 'url' not in data:
        return jsonify({"error": "Please provide 'url'"}), 400
    
    VIDEO_SOURCE = data['url']
    driver = None
    final_video_path = None
    needs_cleanup = False
    
    try:
        # 2. DRIVER & LOGIN
        driver = get_driver()
        load_cookies(driver)

        # Check if logged in
        if len(driver.find_elements(By.TAG_NAME, "textarea")) == 0:
             return jsonify({"error": "Login failed (Cookies might be expired)"}), 500

        # 3. VIDEO SETUP
        is_youtube = "youtube.com" in VIDEO_SOURCE or "youtu.be" in VIDEO_SOURCE
        
        if is_youtube:
            final_video_path = VIDEO_SOURCE
        else:
            final_video_path = download_file(VIDEO_SOURCE)
            needs_cleanup = True

        # 4. UPLOAD (Replacing PyAutoGUI with Direct Injection)
        if not is_youtube:
            print("--- UPLOADING ---")
            # Click the plus button to activate the file input
            add_btn = WebDriverWait(driver, 15).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(@aria-label, 'Insert') or contains(@aria-label, 'Add') or contains(text(), '+')]"))
            )
            add_btn.click()
            time.sleep(1)
            
            # THE MAGIC: Send file path directly to the hidden input
            file_input = driver.find_element(By.CSS_SELECTOR, "input[type='file']")
            file_input.send_keys(os.path.abspath(final_video_path))
            
            print("Waiting for processing (20s)...")
            time.sleep(20)

        # 5. PROMPTING
        print("--- PROMPTING ---")
        base_prompt = (
            "generate a frame by frame and per second transcript of this video . "
            "Show all expressions and every frame in the transcript with timestampts "
            "of the per second transcript. if any dialouge is there in the video "
            "speak by characters in the video then also add that in the transcript."
        )
        final_prompt = f"Here is the video link: {VIDEO_SOURCE}\n\n{base_prompt}" if is_youtube else base_prompt

        box = driver.find_element(By.TAG_NAME, "textarea")
        box.click()
        box.clear()
        box.send_keys(final_prompt)
        time.sleep(1)
        box.send_keys(Keys.CONTROL, Keys.RETURN)

        # 6. WAITING FOR AI
        print("--- WAITING ---")
        time.sleep(15)
        
        start_t = time.time()
        last_len = 0
        stable_count = 0
        final_text = ""
        
        while (time.time() - start_t) < 600:
            try:
                text = driver.find_element(By.TAG_NAME, "body").text
                if len(text) > last_len:
                    print(f"Generating... {len(text)} chars")
                    last_len = len(text)
                    stable_count = 0
                else:
                    stable_count += 1
                
                if stable_count > 5 and len(text) > 500:
                    final_text = text
                    break
                time.sleep(2)
            except:
                time.sleep(2)

        # 7. CLEANUP TEXT
        if "speak by characters in the video" in final_text:
            final_text = final_text.split("speak by characters in the video")[-1]
            
        garbage = ["Google AI models may make mistakes", "Run settings", "Response ready", "thumb_up"]
        for junk in garbage:
            if junk in final_text:
                final_text = final_text.split(junk)[0]

        return jsonify({"status": "success", "transcript": final_text.strip()})

    except Exception as e:
        print(f"Error: {e}")
        return jsonify({"error": str(e)}), 500
    
    finally:
        if driver: driver.quit()
        if needs_cleanup and final_video_path and os.path.exists(final_video_path):
            os.remove(final_video_path)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)