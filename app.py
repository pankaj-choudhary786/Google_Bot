import os
import time
import uuid
import threading
import requests
import google.generativeai as genai
import yt_dlp
from flask import Flask, request, jsonify

app = Flask(__name__)

# --- CONFIGURATION ---
API_KEY = os.environ.get("GOOGLE_API_KEY")

# GLOBAL STORAGE (In-Memory Database)
# Warning: If server restarts, these jobs disappear.
JOBS = {} 

# --- HELPER: AUTO-DETECT MODEL ---
def get_best_model():
    if not API_KEY: return None
    genai.configure(api_key=API_KEY)
    try:
        # Prioritize Flash -> Pro -> Any
        available = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        for m in available:
            if "flash" in m.lower() and "legacy" not in m.lower(): return m
        for m in available:
            if "pro" in m.lower() and "vision" not in m.lower(): return m
        return available[0] if available else "models/gemini-1.5-flash"
    except:
        return "models/gemini-1.5-flash"

# --- HELPER: DOWNLOADERS ---
def download_youtube_video(url, output_path):
    print(f"-> Extracting YouTube: {url}")
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': output_path,
        'quiet': True,
        'no_warnings': True,
        'merge_output_format': 'mp4'
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

def download_cloud_file(url, output_path):
    print(f"-> Downloading Cloud File: {url}")
    with requests.get(url, stream=True) as r:
        r.raise_for_status()
        with open(output_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)

# --- BACKGROUND WORKER ---
def background_worker(job_id, video_url):
    local_path = f"temp_{job_id}.mp4"
    JOBS[job_id]["status"] = "processing"
    
    try:
        if not API_KEY: raise ValueError("API Key missing")

        # 1. Download
        if "youtube.com" in video_url or "youtu.be" in video_url:
            download_youtube_video(video_url, local_path)
        else:
            download_cloud_file(video_url, local_path)
            
        # 2. Upload
        genai.configure(api_key=API_KEY)
        print(f"[{job_id}] Uploading to Gemini...")
        video_file = genai.upload_file(path=local_path)
        
        # 3. Wait for Processing
        while video_file.state.name == "PROCESSING":
            time.sleep(2)
            video_file = genai.get_file(video_file.name)
            
        if video_file.state.name == "FAILED":
            raise ValueError(f"Google processing failed: {video_file.state.name}")

        # 4. Generate
        print(f"[{job_id}] Generating transcript...")
        model_name = get_best_model()
        model = genai.GenerativeModel(model_name=model_name)
        
        prompt = (
            "generate a frame by frame and per second transcript of this video . "
            "Show all expressions and every frame in the transcript with timestampts "
            "of the per second transcript. if any dialouge is there in the video "
            "speak by characters in the video then also add that in the transcript."
        )
        
        response = model.generate_content([video_file, prompt])
        
        # 5. Success
        JOBS[job_id]["status"] = "completed"
        JOBS[job_id]["transcript"] = response.text
        JOBS[job_id]["model_used"] = model_name
        print(f"[{job_id}] DONE!")

    except Exception as e:
        print(f"[{job_id}] ERROR: {e}")
        JOBS[job_id]["status"] = "failed"
        JOBS[job_id]["error"] = str(e)
        
    finally:
        # Cleanup
        if os.path.exists(local_path):
            try: os.remove(local_path)
            except: pass

# --- ENDPOINTS ---
@app.route('/process', methods=['POST'])
def start_job():
    data = request.json
    if not data or 'url' not in data:
        return jsonify({"error": "No url provided"}), 400
    
    # Create Job ID
    job_id = str(uuid.uuid4())
    JOBS[job_id] = {"status": "queued", "submitted_at": time.time()}
    
    # Start Background Thread
    thread = threading.Thread(target=background_worker, args=(job_id, data['url']))
    thread.daemon = True # Ensures thread doesn't block shutdown
    thread.start()
    
    # Return INSTANTLY
    return jsonify({
        "status": "started",
        "job_id": job_id,
        "message": "Use GET /result/<job_id> to check status."
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
