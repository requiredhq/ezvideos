import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
import requests
from duckduckgo_search import DDGS
from moviepy.editor import *
from moviepy.video.fx.all import resize as mv_resize  # Rename to avoid conflict
from moviepy.video import fx as vfx
from PIL import Image, ImageTk
import os
import threading
import io
import queue
import time
import uuid  # For unique filenames

# --- Configuration ---
MAX_THUMBNAILS_DISPLAY = 30         # Max results to show per search
THUMBNAIL_SIZE = (100, 100)          # Display size for thumbnails
TARGET_DURATION_SEC = 60
OUTPUT_VIDEO_FILENAME = "tiktok_gui_video.mp4"
DOWNLOAD_FOLDER = "selected_images_gui"  # Store full selected images here
VIDEO_RESOLUTION = (1080, 1920)      # TikTok's vertical aspect ratio (9:16)
BING_API_KEY = ""                  # <<-- Enter your Bing API key here

# --- Global Variables ---
selected_files_info = {}  # Dictionary {widget_id: {'path': full_path, 'url': url, 'thumb_widget': widget}}
root = None               # Main window reference
status_queue = queue.Queue()  # Queue for status updates from threads
image_queue = queue.Queue()   # Queue for thumbnail data from threads
video_thread = None           # To check if video generation is running
search_engine_var = None      # Will hold the current search engine selection

# --- Additional Viral Options Widgets (to be created in setup_gui) ---
transition_style_var = None   # Dropdown: "Crossfade" or "None"
transition_duration_var = None  # Transition duration in seconds (float)
text_overlay_var = None       # Text overlay to be applied
filter_var = None             # Filter option: "None", "Vintage", "Bright"
bg_music_path_var = None      # Background music file path (string)

# --- Helper Functions (MoviePy part) ---
def create_tiktok_video_threaded(image_files_list, output_filename, target_duration, resolution,
                                 transition_style, transition_duration, overlay_text, filter_option, bg_music_path):
    """Runs the video creation in a separate thread with viral options."""
    global video_thread
    status_queue.put(f"Starting video creation with {len(image_files_list)} images...")
    try:
        valid_image_files = [f for f in image_files_list if os.path.exists(f)]
        if len(valid_image_files) != len(image_files_list):
            status_queue.put(f"Warning: {len(image_files_list) - len(valid_image_files)} selected files not found. Proceeding with {len(valid_image_files)}.")
        if not valid_image_files:
            status_queue.put("Error: No valid image files found to create video.")
            video_thread = None
            return

        clips = []
        duration_per_clip = max(1.0, target_duration / len(valid_image_files))
        status_queue.put(f"Aiming for ~{duration_per_clip:.2f} seconds per clip.")

        for i, img_path in enumerate(valid_image_files):
            try:
                status_queue.put(f"Processing clip {i+1}/{len(valid_image_files)}: {os.path.basename(img_path)}")
                if img_path.lower().endswith(".gif"):
                    clip = VideoFileClip(img_path, target_resolution=(None, resolution[1]), has_mask=True)
                    actual_clip_duration = min(duration_per_clip, clip.duration if clip.duration else duration_per_clip)
                    if actual_clip_duration < 0.1:
                        actual_clip_duration = 0.5
                    clip = clip.set_duration(actual_clip_duration)
                else:
                    clip = ImageClip(img_path, duration=duration_per_clip)
                
                # Resize & Crop to Fit TikTok Aspect Ratio
                clip_w, clip_h = clip.size
                target_w, target_h = resolution
                if clip_w == 0 or clip_h == 0:
                    status_queue.put(f"Warning: Invalid dimensions for {img_path}. Skipping.")
                    continue
                if clip_w / clip_h > target_w / target_h:
                    clip = clip.resize(height=target_h)
                    clip = clip.crop(x_center=clip.w/2, width=target_w)
                else:
                    clip = clip.resize(width=target_w)
                    clip = clip.crop(y_center=clip.h/2, height=target_h)
                clip = clip.resize(resolution)
                
                # Apply subtle zoom effect
                duration_val = clip.duration
                clip = clip.fx(mv_resize, lambda t: 1 + 0.05 * (t / duration_val if duration_val else 1))
                
                # Apply selected filter
                if filter_option == "Vintage":
                    clip = clip.fx(vfx.colorx, 0.8)
                elif filter_option == "Bright":
                    clip = clip.fx(vfx.colorx, 1.2)
                
                clips.append(clip)
            except Exception as e:
                status_queue.put(f"Error processing {os.path.basename(img_path)}: {e}. Skipping.")
                continue

        if not clips:
            status_queue.put("Error: No clips were successfully created.")
            video_thread = None
            return

        # Apply transitions based on selected style
        if transition_style == "Crossfade":
            final_clip = concatenate_videoclips(clips, method="compose", padding=-transition_duration)
        else:
            final_clip = concatenate_videoclips(clips, method="compose")

        final_clip = final_clip.fadein(0.5).fadeout(0.5)

        # Apply text overlay if provided
        if overlay_text.strip():
            txt_clip = TextClip(overlay_text, fontsize=70, color='white', font="Arial-Bold",
                                size=resolution, method='caption', align='South').set_duration(final_clip.duration)
            final_clip = CompositeVideoClip([final_clip, txt_clip])

        # Add background music if a valid file is provided
        if bg_music_path and os.path.exists(bg_music_path):
            audio_clip = AudioFileClip(bg_music_path).subclip(0, final_clip.duration)
            final_clip = final_clip.set_audio(audio_clip)

        status_queue.put(f"Writing video file: {output_filename}...")
        final_clip.write_videofile(output_filename,
                                   codec='libx264', audio_codec='aac',
                                   temp_audiofile='temp-audio.m4a', remove_temp=True,
                                   fps=24, preset='medium', threads=4,
                                   logger='bar')

        for clip in clips:
            clip.close()
        if final_clip:
            final_clip.close()

        status_queue.put(f"Success! Video saved as {output_filename}")
    except Exception as e:
        status_queue.put(f"FATAL VIDEO ERROR: {e}")
    finally:
        video_thread = None

# --- Multi-Engine Image Search Functions ---
def fetch_images_duckduckgo(keywords):
    status_queue.put(f"Searching DuckDuckGo for '{keywords}'...")
    try:
        results_count = 0
        with DDGS() as ddgs:
            ddgs_gen = ddgs.images(keywords, region="wt-wt", safesearch="moderate", max_results=MAX_THUMBNAILS_DISPLAY+10)
            if not ddgs_gen:
                status_queue.put("No results found or error fetching.")
                image_queue.put("SEARCH_COMPLETE")
                return
            for result in ddgs_gen:
                if results_count >= MAX_THUMBNAILS_DISPLAY:
                    break
                thumb_url = result.get('thumbnail')
                full_url = result.get('image')
                if not thumb_url or not full_url:
                    continue
                try:
                    response = requests.get(thumb_url, timeout=10)
                    response.raise_for_status()
                    content_type = response.headers.get('content-type')
                    if content_type and content_type.startswith('image'):
                        image_data = response.content
                        image_queue.put((image_data, full_url, thumb_url))
                        results_count += 1
                    else:
                        log_message(f"Skipping non-image thumbnail: {thumb_url}")
                except Exception as e:
                    log_message(f"Failed to download thumbnail {thumb_url}: {e}")
            status_queue.put(f"Found {results_count} potential thumbnails (DuckDuckGo).")
            image_queue.put("SEARCH_COMPLETE")
    except Exception as e:
        status_queue.put(f"Error during DuckDuckGo search: {e}")
        image_queue.put("SEARCH_COMPLETE")

def fetch_images_bing(keywords):
    status_queue.put(f"Searching Bing for '{keywords}'...")
    results_count = 0
    global BING_API_KEY
    if not BING_API_KEY:
        status_queue.put("Error: Bing API key not provided.")
        image_queue.put("SEARCH_COMPLETE")
        return
    try:
        headers = {"Ocp-Apim-Subscription-Key": BING_API_KEY}
        params = {"q": keywords, "count": MAX_THUMBNAILS_DISPLAY + 10}
        response = requests.get("https://api.bing.microsoft.com/v7.0/images/search", headers=headers, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        images = data.get("value", [])
        for item in images:
            if results_count >= MAX_THUMBNAILS_DISPLAY:
                break
            thumb_url = item.get("thumbnailUrl")
            full_url = item.get("contentUrl")
            if not thumb_url or not full_url:
                continue
            try:
                r_thumb = requests.get(thumb_url, timeout=10)
                r_thumb.raise_for_status()
                content_type = r_thumb.headers.get('content-type')
                if content_type and content_type.startswith('image'):
                    image_data = r_thumb.content
                    image_queue.put((image_data, full_url, thumb_url))
                    results_count += 1
                else:
                    log_message(f"Skipping non-image thumbnail: {thumb_url}")
            except Exception as e:
                log_message(f"Failed to download thumbnail {thumb_url}: {e}")
        status_queue.put(f"Found {results_count} potential thumbnails (Bing).")
    except Exception as e:
        status_queue.put(f"Error during Bing search: {e}")
    finally:
        image_queue.put("SEARCH_COMPLETE")

def fetch_images_brave(keywords):
    status_queue.put(f"Searching Brave for '{keywords}'...")
    results_count = 0
    try:
        params = {"q": keywords, "offset": 0, "count": MAX_THUMBNAILS_DISPLAY + 10}
        response = requests.get("https://api.search.brave.com/images", params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        images = data.get("images", [])
        for item in images:
            if results_count >= MAX_THUMBNAILS_DISPLAY:
                break
            thumb_url = item.get("thumbnail") or item.get("thumbnailUrl")
            full_url = item.get("url") or item.get("contentUrl")
            if not thumb_url or not full_url:
                continue
            try:
                r_thumb = requests.get(thumb_url, timeout=10)
                r_thumb.raise_for_status()
                content_type = r_thumb.headers.get('content-type')
                if content_type and content_type.startswith('image'):
                    image_data = r_thumb.content
                    image_queue.put((image_data, full_url, thumb_url))
                    results_count += 1
                else:
                    log_message(f"Skipping non-image thumbnail: {thumb_url}")
            except Exception as e:
                log_message(f"Failed to download thumbnail {thumb_url}: {e}")
        status_queue.put(f"Found {results_count} potential thumbnails (Brave).")
    except Exception as e:
        status_queue.put(f"Error during Brave search: {e}")
    finally:
        image_queue.put("SEARCH_COMPLETE")

def fetch_images_thread(keywords):
    """Dispatches image search based on selected engine."""
    engine = search_engine_var.get()
    if engine == "DuckDuckGo":
        fetch_images_duckduckgo(keywords)
    elif engine == "Bing":
        fetch_images_bing(keywords)
    elif engine == "Brave":
        fetch_images_brave(keywords)
    else:
        status_queue.put("Unknown search engine selected.")
        image_queue.put("SEARCH_COMPLETE")

# --- GUI Functions ---
def update_status_bar():
    try:
        while True:
            message = status_queue.get_nowait()
            status_var.set(message)
            log_message(message)
            root.update_idletasks()
    except queue.Empty:
        pass
    root.after(100, update_status_bar)

def update_image_display():
    try:
        while True:
            item = image_queue.get_nowait()
            if item == "SEARCH_COMPLETE":
                status_queue.put("Image search finished.")
                search_button.config(state=tk.NORMAL)
                break
            image_data, url, thumb_url = item
            try:
                img = Image.open(io.BytesIO(image_data))
                img.thumbnail(THUMBNAIL_SIZE)
                photo = ImageTk.PhotoImage(img)
                img_button = tk.Button(image_frame_inner, image=photo, relief=tk.RAISED, borderwidth=2)
                img_button.image = photo
                img_button.full_url = url
                is_selected = any(info['url'] == url for info in selected_files_info.values())
                num_cols = image_frame_inner.winfo_width() // (THUMBNAIL_SIZE[0] + 10)
                if num_cols < 1: num_cols = 4
                row = len(image_frame_inner.winfo_children()) // num_cols
                col = len(image_frame_inner.winfo_children()) % num_cols
                img_button.grid(row=row, column=col, padx=5, pady=5)
                widget_id = str(img_button)
                img_button.config(command=lambda w_id=widget_id, u=url, button=img_button: toggle_selection(w_id, u, button))
                if is_selected:
                    img_button.config(relief=tk.SUNKEN, bg='lightblue')
            except Exception as e:
                log_message(f"Error processing thumbnail for {thumb_url}: {e}")
    except queue.Empty:
        pass
    root.after(100, update_image_display)
    image_canvas.configure(scrollregion=image_canvas.bbox("all"))

def log_message(message):
    log_text.config(state=tk.NORMAL)
    log_text.insert(tk.END, f"{time.strftime('%H:%M:%S')} - {message}\n")
    log_text.see(tk.END)
    log_text.config(state=tk.DISABLED)

def download_and_save_full_image(url, widget_id):
    try:
        status_queue.put(f"Downloading full image: {url[:50]}...")
        filename_base = f"selected_{uuid.uuid4()}"
        response = requests.get(url, stream=True, timeout=20)
        response.raise_for_status()
        content_type = response.headers.get('content-type')
        if not content_type or not content_type.startswith('image'):
            raise ValueError(f"Non-image content type: {content_type}")
        extension = ".jpg"
        mime_type = content_type.split(';')[0].lower()
        if mime_type == 'image/jpeg':
            extension = ".jpg"
        elif mime_type == 'image/png':
            extension = ".png"
        elif mime_type == 'image/gif':
            extension = ".gif"
        filename = filename_base + extension
        filepath = os.path.join(DOWNLOAD_FOLDER, filename)
        with open(filepath, 'wb') as f:
            for chunk in response.iter_content(8192):
                f.write(chunk)
        img = Image.open(filepath)
        img.verify()
        img.close()
        status_queue.put(f"Saved: {filename}")
        selected_files_info[widget_id] = {'path': filepath, 'url': url, 'thumb_widget': None}
        update_selection_counter()
    except Exception as e:
        status_queue.put(f"Error downloading full {url}: {e}")
        if widget_id in selected_files_info:
            del selected_files_info[widget_id]
        update_selection_counter()

def toggle_selection(widget_id, url, button_widget):
    if widget_id not in selected_files_info:
        button_widget.config(relief=tk.SUNKEN, bg='lightblue')
        selected_files_info[widget_id] = {'path': None, 'url': url, 'thumb_widget': button_widget}
        threading.Thread(target=download_and_save_full_image, args=(url, widget_id), daemon=True).start()
    else:
        button_widget.config(relief=tk.RAISED, bg=root.cget('bg'))
        info = selected_files_info.pop(widget_id)
        if info.get('path') and os.path.exists(info['path']):
            try:
                os.remove(info['path'])
                status_queue.put(f"Removed deselected file: {os.path.basename(info['path'])}")
            except OSError as e:
                status_queue.put(f"Error removing file {info['path']}: {e}")
    update_selection_counter()

def update_selection_counter():
    count = len(selected_files_info)
    selection_counter_var.set(f"{count} images selected")

def start_search():
    keywords = search_entry.get()
    if not keywords:
        messagebox.showwarning("Input Needed", "Please enter search keywords.")
        return
    for widget in image_frame_inner.winfo_children():
        widget.destroy()
    image_canvas.yview_moveto(0)
    image_canvas.configure(scrollregion=image_canvas.bbox("all"))
    search_button.config(state=tk.DISABLED)
    status_queue.put("Starting search...")
    threading.Thread(target=fetch_images_thread, args=(keywords,), daemon=True).start()

def choose_bg_music():
    path = filedialog.askopenfilename(title="Select Background Music", filetypes=[("Audio Files", "*.mp3 *.wav *.m4a")])
    if path:
        bg_music_path_var.set(path)

def start_video_creation():
    global video_thread
    if video_thread and video_thread.is_alive():
        messagebox.showwarning("Busy", "Video creation is already in progress.")
        return
    valid_selected_paths = [info['path'] for info in selected_files_info.values() if info.get('path') and os.path.exists(info['path'])]
    if not valid_selected_paths:
        messagebox.showerror("Error", "No images selected or downloaded files are missing.")
        return
    if len(valid_selected_paths) < 2:
        messagebox.showwarning("Not Enough Images", "Please select at least 2 images to create a video.")
        return
    confirm = messagebox.askyesno("Confirm Video Creation", f"Create video from {len(valid_selected_paths)} selected images?\nOutput: {OUTPUT_VIDEO_FILENAME}")
    if confirm:
        make_video_button.config(state=tk.DISABLED)
        status_queue.put("Preparing video creation...")
        if not os.path.exists(DOWNLOAD_FOLDER):
            os.makedirs(DOWNLOAD_FOLDER)
        # Gather viral options from GUI widgets
        trans_style = transition_style_var.get()
        try:
            trans_duration = float(transition_duration_var.get())
        except:
            trans_duration = 0.5
        overlay = text_overlay_var.get()
        filt = filter_var.get()
        bg_music = bg_music_path_var.get()
        video_thread = threading.Thread(target=create_tiktok_video_threaded,
                                         args=(valid_selected_paths, OUTPUT_VIDEO_FILENAME, TARGET_DURATION_SEC, VIDEO_RESOLUTION,
                                               trans_style, trans_duration, overlay, filt, bg_music),
                                         daemon=True)
        video_thread.start()
        check_video_thread()

def check_video_thread():
    if video_thread and video_thread.is_alive():
        root.after(500, check_video_thread)
    else:
        make_video_button.config(state=tk.NORMAL)

# --- Main GUI Setup ---
def setup_gui():
    global root, search_entry, selection_counter_var, status_var, image_frame_inner, image_canvas
    global log_text, search_button, make_video_button, search_engine_var
    global transition_style_var, transition_duration_var, text_overlay_var, filter_var, bg_music_path_var

    root = tk.Tk()
    root.title("Viral TikTok Video Maker")
    root.geometry("800x800")

    # --- Top Frame: Search ---
    search_frame = ttk.Frame(root, padding="10")
    search_frame.pack(fill=tk.X, side=tk.TOP)
    ttk.Label(search_frame, text="Search Keywords:").pack(side=tk.LEFT, padx=5)
    search_entry = ttk.Entry(search_frame, width=40)
    search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
    search_entry.bind("<Return>", lambda event: start_search())
    search_button = ttk.Button(search_frame, text="Search Images", command=start_search)
    search_button.pack(side=tk.LEFT, padx=5)
    ttk.Label(search_frame, text="Engine:").pack(side=tk.LEFT, padx=5)
    search_engine_var = tk.StringVar(value="DuckDuckGo")
    engine_combobox = ttk.Combobox(search_frame, textvariable=search_engine_var,
                                   values=["DuckDuckGo", "Bing", "Brave"], state="readonly", width=12)
    engine_combobox.pack(side=tk.LEFT, padx=5)

    # --- Viral Options Frame ---
    options_frame = ttk.LabelFrame(root, text="Viral Options", padding="10")
    options_frame.pack(fill=tk.X, padx=10, pady=5)
    ttk.Label(options_frame, text="Transition Style:").grid(row=0, column=0, padx=5, pady=2, sticky=tk.W)
    transition_style_var = tk.StringVar(value="Crossfade")
    trans_style_combo = ttk.Combobox(options_frame, textvariable=transition_style_var,
                                     values=["Crossfade", "None"], state="readonly", width=12)
    trans_style_combo.grid(row=0, column=1, padx=5, pady=2)
    ttk.Label(options_frame, text="Transition Duration (sec):").grid(row=0, column=2, padx=5, pady=2, sticky=tk.W)
    transition_duration_var = tk.StringVar(value="0.5")
    ttk.Entry(options_frame, textvariable=transition_duration_var, width=5).grid(row=0, column=3, padx=5, pady=2)
    ttk.Label(options_frame, text="Text Overlay:").grid(row=1, column=0, padx=5, pady=2, sticky=tk.W)
    text_overlay_var = tk.StringVar(value="Your Viral Video!")
    ttk.Entry(options_frame, textvariable=text_overlay_var, width=30).grid(row=1, column=1, padx=5, pady=2, columnspan=2)
    ttk.Label(options_frame, text="Filter:").grid(row=1, column=3, padx=5, pady=2, sticky=tk.W)
    filter_var = tk.StringVar(value="None")
    filter_combo = ttk.Combobox(options_frame, textvariable=filter_var,
                                values=["None", "Vintage", "Bright"], state="readonly", width=10)
    filter_combo.grid(row=1, column=4, padx=5, pady=2)
    ttk.Label(options_frame, text="Background Music:").grid(row=2, column=0, padx=5, pady=2, sticky=tk.W)
    bg_music_path_var = tk.StringVar(value="")
    ttk.Entry(options_frame, textvariable=bg_music_path_var, width=30).grid(row=2, column=1, padx=5, pady=2, columnspan=2)
    ttk.Button(options_frame, text="Browse", command=choose_bg_music).grid(row=2, column=3, padx=5, pady=2)

    # --- Middle Frame: Image Display (Scrollable) ---
    image_display_frame = ttk.Frame(root, padding="5")
    image_display_frame.pack(fill=tk.BOTH, expand=True)
    image_canvas = tk.Canvas(image_display_frame)
    scrollbar = ttk.Scrollbar(image_display_frame, orient="vertical", command=image_canvas.yview)
    image_frame_inner = ttk.Frame(image_canvas)
    image_canvas.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
    image_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    canvas_window = image_canvas.create_window((0, 0), window=image_frame_inner, anchor="nw")
    def on_frame_configure(event):
        image_canvas.configure(scrollregion=image_canvas.bbox("all"))
    def on_canvas_configure(event):
        image_canvas.itemconfig(canvas_window, width=event.width)
    image_frame_inner.bind("<Configure>", on_frame_configure)
    image_canvas.bind("<Configure>", on_canvas_configure)

    # --- Control Frame: Counter & Convert Button ---
    control_frame = ttk.Frame(root, padding="10")
    control_frame.pack(fill=tk.X, side=tk.BOTTOM)
    selection_counter_var = tk.StringVar()
    ttk.Label(control_frame, textvariable=selection_counter_var).pack(side=tk.LEFT, padx=10)
    update_selection_counter()
    make_video_button = ttk.Button(control_frame, text="Convert Video", command=start_video_creation)
    make_video_button.pack(side=tk.RIGHT, padx=10)

    # --- Log Frame ---
    log_frame = ttk.Frame(root, padding="0 5 0 5")
    log_frame.pack(fill=tk.X, side=tk.BOTTOM)
    log_text = scrolledtext.ScrolledText(log_frame, height=6, state=tk.DISABLED, wrap=tk.WORD)
    log_text.pack(fill=tk.X, expand=True)

    # --- Status Bar ---
    status_var = tk.StringVar()
    status_bar = ttk.Label(root, textvariable=status_var, relief=tk.SUNKEN, anchor=tk.W, padding="2 5")
    status_bar.pack(side=tk.BOTTOM, fill=tk.X)
    status_var.set("Ready. Enter keywords and click Search.")

    root.after(100, update_status_bar)
    root.after(100, update_image_display)

    if not os.path.exists(DOWNLOAD_FOLDER):
        try:
            os.makedirs(DOWNLOAD_FOLDER)
            log_message(f"Created download folder: {DOWNLOAD_FOLDER}")
        except OSError as e:
            messagebox.showerror("Folder Error", f"Could not create download folder '{DOWNLOAD_FOLDER}': {e}")
            root.destroy()
            return

    root.mainloop()

# --- Run the Application ---
if __name__ == "__main__":
    setup_gui()
