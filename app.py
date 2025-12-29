import os
import time
import uuid
import threading
import shutil
import requests
import google.generativeai as genai
import yt_dlp
from flask import Flask, request, jsonify

app = Flask(__name__)

# --- CONFIGURATION ---
API_KEY = os.environ.get("GOOGLE_API_KEY")
COOKIES_SOURCE = "/etc/secrets/youtube_cookies.txt" 

# GLOBAL STORAGE
JOBS = {} 

# --- HELPER: SILENT MODEL SELECTOR ---
def get_model():
    if not API_KEY: return "models/gemini-1.5-flash"
    genai.configure(api_key=API_KEY)
    try:
        available = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        for m in available:
            if "flash" in m.lower() and "legacy" not in m.lower(): return m
        return available[0]
    except:
        return "models/gemini-1.5-flash"

# --- HELPER: DOWNLOADERS ---
def download_youtube_video(url, output_path, cookie_path):
    # 'ios' client is often less strict about "Sign in" checks
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': output_path,
        'quiet': True,
        'no_warnings': True,
        'merge_output_format': 'mp4',
        'cookiefile': cookie_path,
        'nocheckcertificate': True,
        'source_address': '0.0.0.0', # Force IPv4
        'extractor_args': {
            'youtube': {
                'player_client': ['ios', 'web_creator'] # Mimic iPhone or Creator Studio
            }
        }
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

def download_cloud_file(url, output_path):
    with requests.get(url, stream=True) as r:
        r.raise_for_status()
        with open(output_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)

# --- BACKGROUND WORKER ---
def background_worker(job_id, video_url):
    local_video_path = f"temp_{job_id}.mp4"
    local_cookie_path = f"cookies_{job_id}.txt"
    
    JOBS[job_id]["status"] = "working"
    
    try:
        if not API_KEY: raise ValueError("Server configuration error")

        # 1. Setup Cookies (Copy to writable location)
        use_cookies = False
        if os.path.exists(COOKIES_SOURCE):
            try:
                shutil.copy(COOKIES_SOURCE, local_cookie_path)
                use_cookies = True
            except:
                pass

        # 2. Download
        if "youtube.com" in video_url or "youtu.be" in video_url:
            # We try to download. If cookies are missing, we try anyway (might work for some vids)
            cookie_arg = local_cookie_path if use_cookies else None
            if not cookie_arg:
                print("Note: No cookies found. Attempting cookie-less download.")
            
            download_youtube_video(video_url, local_video_path, cookie_arg)
        else:
            download_cloud_file(video_url, local_video_path)
            
        # 3. Upload
        genai.configure(api_key=API_KEY)
        video_file = genai.upload_file(path=local_video_path)
        
        # 4. Wait
        while video_file.state.name == "PROCESSING":
            time.sleep(2)
            video_file = genai.get_file(video_file.name)
            
        if video_file.state.name == "FAILED":
            raise ValueError("Processing failed")

        # 5. Generate
        model = genai.GenerativeModel(model_name=get_model())
        
        prompt = (
            "generate a frame by frame and per second transcript of this video . "
            "Show all expressions and every frame in the transcript with timestampts "
            "of the per second transcript. if any dialouge is there in the video "
            "speak by characters in the video then also add that in the transcript."
        )
        
        response = model.generate_content([video_file, prompt])
        
        # 6. Success
        JOBS[job_id]["status"] = "completed"
        JOBS[job_id]["transcript"] = response.text

    except Exception as e:
        JOBS[job_id]["status"] = "failed"
        JOBS[job_id]["error"] = str(e)
        
    finally:
        if os.path.exists(local_video_path):
            try: os.remove(local_video_path)
            except: pass
        if os.path.exists(local_cookie_path):
            try: os.remove(local_cookie_path)
            except: pass

# --- ENDPOINTS ---
@app.route('/process', methods=['POST'])
def start_job():
    data = request.json
    if not data or 'url' not in data:
        return jsonify({"error": "No url provided"}), 400
    
    job_id = str(uuid.uuid4())
    JOBS[job_id] = {"status": "queued"}
    
    thread = threading.Thread(target=background_worker, args=(job_id, data['url']))
    thread.daemon = True
    thread.start()
    
    return jsonify({
        "status": "started",
        "id": job_id
    })

@app.route('/result/<job_id>', methods=['GET'])
def get_result(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Not found"}), 404
    
    response = {"status": job["status"]}
    if "transcript" in job:
        response["transcript"] = job["transcript"]
    if "error" in job:
        response["error"] = job["error"]
        
    return jsonify(response)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
