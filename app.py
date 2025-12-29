import os
import time
import uuid
import threading
import requests
import google.generativeai as genai
from flask import Flask, request, jsonify

app = Flask(__name__)

# --- CONFIGURATION ---
API_KEY = os.environ.get("GOOGLE_API_KEY")

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

# --- HELPER: CLOUD DOWNLOADER ---
def download_cloud_file(url, output_path):
    print(f"-> Downloading from Cloud: {url}")
    # User-Agent mimics a browser to avoid rejection from some cloud servers
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    with requests.get(url, stream=True, headers=headers) as r:
        r.raise_for_status()
        with open(output_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)

# --- BACKGROUND WORKER ---
def background_worker(job_id, video_url):
    local_path = f"temp_{job_id}.mp4"
    JOBS[job_id]["status"] = "working"
    
    try:
        if not API_KEY: raise ValueError("Server configuration error")

        # 1. Download from Cloud Link
        download_cloud_file(video_url, local_path)
            
        # 2. Upload to Gemini
        genai.configure(api_key=API_KEY)
        video_file = genai.upload_file(path=local_path)
        
        # 3. Wait for Processing
        while video_file.state.name == "PROCESSING":
            time.sleep(2)
            video_file = genai.get_file(video_file.name)
            
        if video_file.state.name == "FAILED":
            raise ValueError("Processing failed")

        # 4. Generate Transcript
        model = genai.GenerativeModel(model_name=get_model())
        
        prompt = (
            "generate a frame by frame and per second detailed transcript of this video . "
            "Show all expressions and every frame in the transcript with timestampts "
            "of the per second transcript. if any dialouge is there in the video "
            "speak by characters in the video then also add that in the transcript."
        )
        
        response = model.generate_content([video_file, prompt])
        
        # 5. Success
        JOBS[job_id]["status"] = "completed"
        JOBS[job_id]["transcript"] = response.text

    except Exception as e:
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

