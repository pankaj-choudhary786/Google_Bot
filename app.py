import os
import time
import requests
import google.generativeai as genai
import yt_dlp
from flask import Flask, request, jsonify

app = Flask(__name__)

# --- CONFIGURATION ---
API_KEY = os.environ.get("GOOGLE_API_KEY")

if API_KEY:
    genai.configure(api_key=API_KEY)

# --- HELPER FUNCTIONS ---
def download_youtube_video(url, output_path):
    print(f"-> Detected YouTube URL. Extracting...")
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
    print(f"-> Detected Cloud URL. Downloading...")
    with requests.get(url, stream=True) as r:
        r.raise_for_status()
        with open(output_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)

# --- MAIN ROUTE ---
@app.route('/process', methods=['POST'])
def process_video():
    data = request.json
    if not data or 'url' not in data:
        return jsonify({"error": "No url provided"}), 400
    
    video_url = data['url']
    local_filename = f"temp_{int(time.time())}.mp4"
    local_path = os.path.join(os.getcwd(), local_filename)
    
    try:
        if not API_KEY:
            return jsonify({"error": "Server API Key is missing."}), 500

        # 1. DOWNLOAD
        if "youtube.com" in video_url or "youtu.be" in video_url:
            download_youtube_video(video_url, local_path)
        else:
            download_cloud_file(video_url, local_path)
        print("-> Download complete.")

        # 2. UPLOAD
        print("-> Uploading to Gemini...")
        video_file = genai.upload_file(path=local_path)
        
        print("-> Waiting for processing...")
        while video_file.state.name == "PROCESSING":
            time.sleep(2)
            video_file = genai.get_file(video_file.name)
            
        if video_file.state.name == "FAILED":
            raise ValueError(f"Video processing failed: {video_file.state.name}")

        # 3. GENERATE
        print("-> Generating transcript...")
        
        model = genai.GenerativeModel(model_name="gemini-1.5-flash")
        
        # --- YOUR CUSTOM PROMPT ---
        prompt = (
            "generate a frame by frame and per second transcript of this video . "
            "Show all expressions and every frame in the transcript with timestampts "
            "of the per second transcript. if any dialouge is there in the video "
            "speak by characters in the video then also add that in the transcript."
        )
        
        response = model.generate_content([video_file, prompt])
        
        return jsonify({
            "status": "success",
            "transcript": response.text
        })

    except Exception as e:
        print(f"Error: {e}")
        # Debugging: Print available models if generate fails
        try:
            print("--- LIST OF AVAILABLE MODELS ---")
            for m in genai.list_models():
                if 'generateContent' in m.supported_generation_methods:
                    print(m.name)
            print("--------------------------------")
        except:
            pass
            
        return jsonify({"error": str(e)}), 500
        
    finally:
        if os.path.exists(local_path):
            try: os.remove(local_path)
            except: pass

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
