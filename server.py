from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for, session
import requests
import re
import time
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
import threading
import json
import sqlite3
from functools import wraps
import uuid
import hashlib
from urllib.parse import urlparse, parse_qs
import secrets
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import logging
from contextlib import contextmanager
import mimetypes
from PIL import Image
import io

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)

# ===== LOGGING SETUP =====
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('app.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ===== CONFIGURATION =====
UPLOAD_FOLDER = os.path.join('static', 'uploads', 'banners')
MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
SUBTITLE_CACHE_DIR = "subtitle_cache"

# Create directories
os.makedirs(SUBTITLE_CACHE_DIR, exist_ok=True)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ===== DATABASE SETUP =====
def init_db():
    conn = sqlite3.connect('subtitle_app.db')
    cursor = conn.cursor()
    
    # Visitors table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS visitors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT UNIQUE,
            ip_address TEXT,
            user_agent TEXT,
            first_visit DATETIME,
            last_activity DATETIME,
            page_views INTEGER DEFAULT 1,
            is_active BOOLEAN DEFAULT 1
        )
    ''')
    
    # Banners table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS banners (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            image_path TEXT,
            link_url TEXT,
            position TEXT CHECK(position IN ('left', 'right')),
            clicks INTEGER DEFAULT 0,
            status BOOLEAN DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Settings table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            description TEXT
        )
    ''')
    
    # Subtitle downloads tracking
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS subtitle_downloads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id TEXT,
            video_title TEXT,
            video_url TEXT,
            language TEXT,
            format TEXT CHECK(format IN ('srt', 'txt')),
            file_size INTEGER,
            download_count INTEGER DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_downloaded DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Insert default settings
    default_settings = [
        ('admin_username', 'admin', 'Admin username'),
        ('admin_password_hash', generate_password_hash('admin123'), 'Admin password hash'),
        ('site_title', 'YouTube Subtitle Downloader', 'Site title'),
        ('maintenance_mode', 'false', 'Maintenance mode')
    ]
    
    for key, value, desc in default_settings:
        cursor.execute('INSERT OR IGNORE INTO settings (key, value, description) VALUES (?, ?, ?)', 
                      (key, value, desc))
    
    conn.commit()
    conn.close()

# Initialize database
init_db()

# ===== VISITOR TRACKING =====
class VisitorTracker:
    def __init__(self):
        self.active_visitors = {}
        self.cleanup_thread = threading.Thread(target=self.cleanup_inactive_visitors, daemon=True)
        self.cleanup_thread.start()
    
    def track_visitor(self, request):
        session_id = session.get('session_id')
        if not session_id:
            session_id = str(uuid.uuid4())
            session['session_id'] = session_id
        
        ip_address = request.remote_addr
        user_agent = request.headers.get('User-Agent', '')
        current_time = datetime.now()
        
        self.active_visitors[session_id] = current_time
        
        conn = sqlite3.connect('subtitle_app.db')
        cursor = conn.cursor()
        
        cursor.execute('SELECT id, page_views FROM visitors WHERE session_id = ?', (session_id,))
        visitor = cursor.fetchone()
        
        if visitor:
            cursor.execute('''
                UPDATE visitors 
                SET last_activity = ?, page_views = page_views + 1, is_active = 1
                WHERE session_id = ?
            ''', (current_time, session_id))
        else:
            cursor.execute('''
                INSERT INTO visitors (session_id, ip_address, user_agent, first_visit, last_activity)
                VALUES (?, ?, ?, ?, ?)
            ''', (session_id, ip_address, user_agent, current_time, current_time))
        
        conn.commit()
        conn.close()
        
        return session_id
    
    def get_active_count(self):
        current_time = datetime.now()
        cutoff_time = current_time - timedelta(minutes=5)
        
        self.active_visitors = {
            sid: last_activity for sid, last_activity in self.active_visitors.items()
            if last_activity > cutoff_time
        }
        
        return len(self.active_visitors)
    
    def cleanup_inactive_visitors(self):
        while True:
            try:
                time.sleep(300)  # Clean up every 5 minutes
                current_time = datetime.now()
                cutoff_time = current_time - timedelta(minutes=10)
                
                conn = sqlite3.connect('subtitle_app.db')
                cursor = conn.cursor()
                cursor.execute('UPDATE visitors SET is_active = 0 WHERE last_activity < ?', (cutoff_time,))
                conn.commit()
                conn.close()
                
            except Exception as e:
                logger.error(f"Visitor cleanup error: {e}")

visitor_tracker = VisitorTracker()

# ===== YOUTUBE SUBTITLE EXTRACTOR =====
class YouTubeSubtitleExtractor:
    def __init__(self):
        self.setup_ytdlp()
    
    def setup_ytdlp(self):
        try:
            import yt_dlp
            self.ytdlp_available = True
            logger.info("✅ yt-dlp is available")
        except ImportError:
            logger.info("⚠️ Installing yt-dlp...")
            try:
                subprocess.check_call([sys.executable, "-m", "pip", "install", "yt-dlp"])
                import yt_dlp
                self.ytdlp_available = True
                logger.info("✅ yt-dlp installed successfully")
            except Exception as e:
                logger.error(f"❌ Failed to install yt-dlp: {e}")
                self.ytdlp_available = False
    
    def extract_video_id(self, url):
        patterns = [
            r'(?:youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/embed\/)([^&\n?#]+)',
            r'youtube\.com\/watch\?.*v=([^&\n?#]+)'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        
        return None
    
    def get_video_info(self, video_url):
        if not self.ytdlp_available:
            return None, "yt-dlp not available"
        
        try:
            import yt_dlp
            
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'writesubtitles': True,
                'writeautomaticsub': True,
                'skip_download': True,
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=False)
                
                video_info = {
                    'id': info.get('id'),
                    'title': info.get('title'),
                    'duration': info.get('duration'),
                    'uploader': info.get('uploader'),
                    'view_count': info.get('view_count'),
                    'thumbnail': info.get('thumbnail'),
                }
                
                # Get available subtitles
                subtitles = info.get('subtitles', {})
                auto_subtitles = info.get('automatic_captions', {})
                
                available_subs = {}
                
                # Manual subtitles
                for lang, subs in subtitles.items():
                    available_subs[lang] = {
                        'type': 'manual',
                        'language': lang,
                        'formats': [sub.get('ext', 'vtt') for sub in subs]
                    }
                
                # Automatic subtitles
                for lang, subs in auto_subtitles.items():
                    if lang not in available_subs:
                        available_subs[lang] = {
                            'type': 'auto',
                            'language': lang,
                            'formats': [sub.get('ext', 'vtt') for sub in subs]
                        }
                
                video_info['subtitles'] = available_subs
                
                return video_info, None
                
        except Exception as e:
            return None, str(e)
    
    def download_subtitle(self, video_url, language='vi', format='srt'):
        if not self.ytdlp_available:
            return None, "yt-dlp not available"
        
        try:
            import yt_dlp
            
            video_id = self.extract_video_id(video_url)
            if not video_id:
                return None, "Invalid YouTube URL"
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{video_id}_{language}_{format}_{timestamp}"
            output_path = os.path.join(SUBTITLE_CACHE_DIR, filename)
            
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'writesubtitles': True,
                'writeautomaticsub': True,
                'skip_download': True,
                'subtitleslangs': [language],
                'subtitlesformat': 'vtt',
                'outtmpl': output_path,
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_url])
            
            # Find the downloaded VTT file
            vtt_file = f"{output_path}.{language}.vtt"
            if not os.path.exists(vtt_file):
                vtt_file = f"{output_path}.{language}.{language}.vtt"
            
            if not os.path.exists(vtt_file):
                return None, f"Subtitle not found for language: {language}"
            
            # Convert VTT to requested format
            if format == 'srt':
                srt_content = self.convert_vtt_to_srt(vtt_file)
                srt_file = f"{output_path}.srt"
                with open(srt_file, 'w', encoding='utf-8') as f:
                    f.write(srt_content)
                
                if os.path.exists(vtt_file):
                    os.remove(vtt_file)
                
                return srt_file, None
                
            elif format == 'txt':
                txt_content = self.convert_vtt_to_txt(vtt_file)
                txt_file = f"{output_path}.txt"
                with open(txt_file, 'w', encoding='utf-8') as f:
                    f.write(txt_content)
                
                if os.path.exists(vtt_file):
                    os.remove(vtt_file)
                
                return txt_file, None
                
        except Exception as e:
            return None, str(e)
    
    def convert_vtt_to_srt(self, vtt_file):
        try:
            with open(vtt_file, 'r', encoding='utf-8') as f:
                vtt_content = f.read()
            
            lines = vtt_content.split('\n')
            srt_entries = []
            
            i = 0
            while i < len(lines):
                line = lines[i].strip()
                
                if not line or line.startswith('WEBVTT') or line.startswith('Kind:') or line.startswith('Language:') or line.startswith('NOTE'):
                    i += 1
                    continue
                
                if '-->' in line and ':' in line:
                    timestamp_line = re.sub(r' align:\w+| position:\d+%| size:\d+%| line:[^\s]+', '', line).strip()
                    timestamp_line = timestamp_line.replace('.', ',')
                    
                    text_lines = []
                    i += 1
                    
                    while i < len(lines):
                        text_line = lines[i].strip()
                        
                        if not text_line or ('-->' in text_line and ':' in text_line):
                            break
                        
                        clean_text = re.sub(r'<[^>]+>', '', text_line).strip()
                        if clean_text:
                            text_lines.append(clean_text)
                        
                        i += 1
                    
                    if text_lines and '-->' in timestamp_line:
                        full_text = ' '.join(text_lines)
                        
                        duplicate = False
                        for existing_entry in srt_entries:
                            if existing_entry['text'] == full_text:
                                duplicate = True
                                break
                        
                        if not duplicate:
                            srt_entries.append({
                                'timestamp': timestamp_line,
                                'text': full_text
                            })
                    
                    continue
                else:
                    i += 1
            
            srt_lines = []
            for index, entry in enumerate(srt_entries, 1):
                srt_lines.append(str(index))
                srt_lines.append(entry['timestamp'])
                srt_lines.append(entry['text'])
                srt_lines.append('')
            
            return '\n'.join(srt_lines)
            
        except Exception as e:
            logger.error(f"VTT to SRT conversion error: {e}")
            return ""

    def convert_vtt_to_txt(self, vtt_file):
        try:
            with open(vtt_file, 'r', encoding='utf-8') as f:
                vtt_content = f.read()
            
            lines = vtt_content.split('\n')
            sentences = []
            seen_sentences = set()
            
            for line in lines:
                line = line.strip()
                
                if (not line or 
                    line.startswith('WEBVTT') or 
                    line.startswith('Kind:') or 
                    line.startswith('Language:') or 
                    line.startswith('NOTE') or
                    '-->' in line or
                    ':' in line and len(line.split(':')) >= 3):
                    continue
                
                clean_line = re.sub(r'<[^>]+>', '', line).strip()
                
                if clean_line and clean_line not in seen_sentences:
                    seen_sentences.add(clean_line)
                    sentences.append(clean_line)
            
            full_text = ' '.join(sentences)
            sentence_endings = re.split(r'[.!?]+\s+', full_text)
            final_sentences = []
            
            for sentence in sentence_endings:
                sentence = sentence.strip()
                if sentence and sentence not in final_sentences:
                    final_sentences.append(sentence)
            
            return '\n'.join(final_sentences)
            
        except Exception as e:
            logger.error(f"VTT to TXT conversion error: {e}")
            return ""

subtitle_extractor = YouTubeSubtitleExtractor()

# ===== ADMIN AUTHENTICATION =====
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

# ===== UTILITY FUNCTIONS =====
def get_banners(position=None):
    try:
        conn = sqlite3.connect('subtitle_app.db')
        cursor = conn.cursor()
        
        if position:
            cursor.execute('SELECT * FROM banners WHERE position = ? AND status = 1 ORDER BY id DESC', (position,))
        else:
            cursor.execute('SELECT * FROM banners WHERE status = 1 ORDER BY id DESC')
        
        banners = cursor.fetchall()
        conn.close()
        
        banner_list = []
        for banner in banners:
            banner_list.append({
                'id': banner[0],
                'title': banner[1],
                'description': banner[2],
                'image_path': banner[3],
                'link_url': banner[4],
                'position': banner[5],
                'clicks': banner[6],
                'status': banner[7],
                'created_at': banner[8]
            })
        
        return banner_list
        
    except Exception as e:
        logger.error(f"Error getting banners: {e}")
        return []

def get_cache_info():
    try:
        total_files = 0
        total_size = 0
        
        for filename in os.listdir(SUBTITLE_CACHE_DIR):
            file_path = os.path.join(SUBTITLE_CACHE_DIR, filename)
            if os.path.isfile(file_path):
                total_files += 1
                total_size += os.path.getsize(file_path)
        
        return {
            'total_files': total_files,
            'total_size_mb': round(total_size / (1024 * 1024), 2)
        }
        
    except Exception as e:
        logger.error(f"Cache info error: {e}")
        return {'total_files': 0, 'total_size_mb': 0}

def cleanup_cache(max_age_hours=24):
    try:
        current_time = time.time()
        deleted_count = 0
        
        for filename in os.listdir(SUBTITLE_CACHE_DIR):
            file_path = os.path.join(SUBTITLE_CACHE_DIR, filename)
            if os.path.isfile(file_path):
                file_age = current_time - os.path.getctime(file_path)
                if file_age > (max_age_hours * 3600):
                    try:
                        os.remove(file_path)
                        deleted_count += 1
                    except:
                        pass
        
        return deleted_count
        
    except Exception as e:
        logger.error(f"Cache cleanup error: {e}")
        return 0

def clear_all_cache():
    try:
        deleted_count = 0
        
        for filename in os.listdir(SUBTITLE_CACHE_DIR):
            file_path = os.path.join(SUBTITLE_CACHE_DIR, filename)
            if os.path.isfile(file_path):
                try:
                    os.remove(file_path)
                    deleted_count += 1
                except:
                    pass
        
        return deleted_count
        
    except Exception as e:
        logger.error(f"Cache clear error: {e}")
        return 0

# ===== MIDDLEWARE =====
@app.before_request
def track_visitors():
    if request.endpoint and not request.endpoint.startswith('static'):
        visitor_tracker.track_visitor(request)

# ===== MAIN ROUTES =====
@app.route('/')
def index():
    try:
        left_banners = get_banners('left')
        right_banners = get_banners('right')
        
        return render_template('index.html', 
                             left_banners=left_banners, 
                             right_banners=right_banners)
    except Exception as e:
        logger.error(f"Homepage error: {e}")
        return render_template('index.html', 
                             left_banners=[], 
                             right_banners=[])

@app.route('/get_video_info', methods=['POST'])
def get_video_info():
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'Invalid JSON data'})
        
        video_url = data.get('url', '').strip()
        
        if not video_url:
            return jsonify({'success': False, 'message': 'URL không được để trống'})
        
        video_id = subtitle_extractor.extract_video_id(video_url)
        if not video_id:
            return jsonify({'success': False, 'message': 'URL YouTube không hợp lệ'})
        
        video_info, error = subtitle_extractor.get_video_info(video_url)
        
        if error:
            return jsonify({'success': False, 'message': f'Lỗi: {error}'})
        
        if not video_info:
            return jsonify({'success': False, 'message': 'Không thể lấy thông tin video'})
        
        if not video_info.get('subtitles'):
            return jsonify({
                'success': False, 
                'message': 'Video này không có phụ đề khả dụng',
                'video_info': video_info
            })
        
        return jsonify({
            'success': True,
            'message': 'Lấy thông tin video thành công',
            'video_info': video_info,
            'available_languages': list(video_info['subtitles'].keys())
        })
        
    except Exception as e:
        logger.error(f"Get video info error: {e}")
        return jsonify({'success': False, 'message': f'Lỗi server: {str(e)}'})

@app.route('/download_subtitle', methods=['POST'])
def download_subtitle():
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'Invalid JSON data'})
        
        video_url = data.get('url', '').strip()
        language = data.get('language', 'vi')
        format = data.get('format', 'srt')
        
        if not video_url:
            return jsonify({'success': False, 'message': 'URL không được để trống'})
        
        if format not in ['srt', 'txt']:
            return jsonify({'success': False, 'message': 'Format không hỗ trợ (chỉ hỗ trợ SRT và TXT)'})
        
        video_id = subtitle_extractor.extract_video_id(video_url)
        if not video_id:
            return jsonify({'success': False, 'message': 'URL YouTube không hợp lệ'})
        
        subtitle_file, error = subtitle_extractor.download_subtitle(video_url, language, format)
        
        if error:
            return jsonify({'success': False, 'message': f'Lỗi: {error}'})
        
        if not subtitle_file or not os.path.exists(subtitle_file):
            return jsonify({'success': False, 'message': 'Không thể tải phụ đề'})
        
        # Track download in database
        try:
            video_info, _ = subtitle_extractor.get_video_info(video_url)
            
            conn = sqlite3.connect('subtitle_app.db')
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT id, download_count FROM subtitle_downloads 
                WHERE video_id = ? AND language = ? AND format = ?
            ''', (video_id, language, format))
            
            existing = cursor.fetchone()
            
            if existing:
                cursor.execute('''
                    UPDATE subtitle_downloads 
                    SET download_count = download_count + 1, last_downloaded = CURRENT_TIMESTAMP
                    WHERE id = ?
                ''', (existing[0],))
            else:
                cursor.execute('''
                    INSERT INTO subtitle_downloads 
                    (video_id, video_title, video_url, language, format, file_size)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (
                    video_id,
                    video_info.get('title', '') if video_info else '',
                    video_url,
                    language,
                    format,
                    os.path.getsize(subtitle_file)
                ))
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            logger.error(f"Database tracking error: {e}")
        
        filename = f"{video_id}_{language}.{format}"
        
        return jsonify({
            'success': True,
            'message': f'Tải phụ đề {format.upper()} thành công',
            'download_url': f'/download_file/{os.path.basename(subtitle_file)}',
            'filename': filename,
            'language': language,
            'format': format,
            'file_size': os.path.getsize(subtitle_file)
        })
        
    except Exception as e:
        logger.error(f"Download subtitle error: {e}")
        return jsonify({'success': False, 'message': f'Lỗi server: {str(e)}'})

@app.route('/download_file/<filename>')
def download_file(filename):
    try:
        file_path = os.path.join(SUBTITLE_CACHE_DIR, filename)
        
        if not os.path.exists(file_path):
            return "File not found", 404
        
        if filename.endswith('.srt'):
            download_name = filename.replace(filename.split('_')[-1], '').rstrip('_') + '.srt'
        elif filename.endswith('.txt'):
            download_name = filename.replace(filename.split('_')[-1], '').rstrip('_') + '.txt'
        else:
            download_name = filename
        
        return send_file(file_path, as_attachment=True, download_name=download_name)
        
    except Exception as e:
        logger.error(f"File download error: {e}")
        return f"Error: {e}", 500

# ===== BANNER ROUTES =====
@app.route('/banner/click/<int:banner_id>')
def banner_click(banner_id):
    try:
        conn = sqlite3.connect('subtitle_app.db')
        cursor = conn.cursor()
        
        cursor.execute('UPDATE banners SET clicks = clicks + 1 WHERE id = ?', (banner_id,))
        cursor.execute('SELECT link_url FROM banners WHERE id = ?', (banner_id,))
        result = cursor.fetchone()
        
        conn.commit()
        conn.close()
        
        if result and result[0]:
            return redirect(result[0])
        else:
            return redirect(url_for('index'))
            
    except Exception as e:
        logger.error(f"Banner click error: {e}")
        return redirect(url_for('index'))

# ===== ADMIN ROUTES =====
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        conn = sqlite3.connect('subtitle_app.db')
        cursor = conn.cursor()
        cursor.execute('SELECT value FROM settings WHERE key = ?', ('admin_username',))
        db_username = cursor.fetchone()[0]
        cursor.execute('SELECT value FROM settings WHERE key = ?', ('admin_password_hash',))
        db_password_hash = cursor.fetchone()[0]
        conn.close()
        
        if username == db_username and check_password_hash(db_password_hash, password):
            session['admin_logged_in'] = True
            session['admin_username'] = username
            return redirect(url_for('admin_dashboard'))
        else:
            return render_template('admin_login.html', error='Sai tên đăng nhập hoặc mật khẩu')
    
    return render_template('admin_login.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    session.pop('admin_username', None)
    return redirect(url_for('admin_login'))

@app.route('/admin')
@admin_required
def admin_dashboard():
    return render_template('admin.html')

# ===== ADMIN API ROUTES =====
@app.route('/admin/api/stats')
@admin_required
def admin_stats():
    try:
        conn = sqlite3.connect('subtitle_app.db')
        cursor = conn.cursor()
        
        # Active visitors
        cutoff_time = datetime.now() - timedelta(minutes=5)
        cursor.execute('SELECT COUNT(*) FROM visitors WHERE is_active = 1 AND last_activity > ?', (cutoff_time,))
        active_visitors = cursor.fetchone()[0]
        
        # Total downloads
        cursor.execute('SELECT SUM(download_count) FROM subtitle_downloads')
        total_downloads = cursor.fetchone()[0] or 0
        
        # Total videos
        cursor.execute('SELECT COUNT(DISTINCT video_id) FROM subtitle_downloads')
        unique_videos = cursor.fetchone()[0]
        
        # Active banners
        cursor.execute('SELECT COUNT(*) FROM banners WHERE status = 1')
        active_banners = cursor.fetchone()[0]
        
        conn.close()
        
        return jsonify({
            'success': True,
            'stats': {
                'visitors': {'active_now': active_visitors},
                'downloads': {'total_downloads': total_downloads, 'unique_videos': unique_videos},
                'banners': {'active_banners': active_banners}
            }
        })
        
    except Exception as e:
        logger.error(f"Stats API error: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/api/visitors')
@admin_required
def admin_visitors():
    try:
        conn = sqlite3.connect('subtitle_app.db')
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT session_id, ip_address, user_agent, first_visit, last_activity, 
                   page_views, is_active
            FROM visitors 
            ORDER BY last_activity DESC 
            LIMIT 50
        ''')
        
        visitors = []
        for row in cursor.fetchall():
            visitors.append({
                'session_id': row[0][:8] + '...',
                'ip_address': row[1],
                'user_agent': row[2][:50] + '...' if len(row[2]) > 50 else row[2],
                'first_visit': row[3],
                'last_activity': row[4],
                'page_views': row[5],
                'is_active': bool(row[6])
            })
        
        conn.close()
        
        return jsonify({
            'success': True,
            'visitors': visitors,
            'active_count': visitor_tracker.get_active_count()
        })
        
    except Exception as e:
        logger.error(f"Visitors API error: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/api/downloads')
@admin_required
def admin_downloads():
    try:
        conn = sqlite3.connect('subtitle_app.db')
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT video_id, video_title, language, format, 
                   SUM(download_count) as total_downloads,
                   MAX(last_downloaded) as last_download,
                   video_url
            FROM subtitle_downloads 
            GROUP BY video_id, language, format
            ORDER BY total_downloads DESC 
            LIMIT 50
        ''')
        
        downloads = []
        for row in cursor.fetchall():
            downloads.append({
                'video_id': row[0],
                'video_title': row[1] or 'Unknown Video',
                'language': row[2],
                'format': row[3],
                'download_count': row[4],
                'last_download': row[5],
                'video_url': row[6]
            })
        
        conn.close()
        
        return jsonify({'success': True, 'downloads': downloads})
        
    except Exception as e:
        logger.error(f"Downloads API error: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/api/banners', methods=['GET', 'POST', 'PUT', 'DELETE'])
@admin_required
def admin_banners():
    try:
        conn = sqlite3.connect('subtitle_app.db')
        cursor = conn.cursor()
        
        if request.method == 'GET':
            cursor.execute('SELECT * FROM banners ORDER BY id DESC')
            
            banners = []
            for row in cursor.fetchall():
                banners.append({
                    'id': row[0],
                    'title': row[1],
                    'description': row[2],
                    'image_path': row[3],
                    'link_url': row[4],
                    'position': row[5],
                    'clicks': row[6],
                    'status': bool(row[7]),
                    'created_at': row[8]
                })
            
            conn.close()
            return jsonify({'success': True, 'banners': banners})
            
        elif request.method == 'POST':
            data = request.get_json()
            if not data:
                return jsonify({'success': False, 'error': 'No data received'})
            
            cursor.execute('''
                INSERT INTO banners (title, description, image_path, link_url, position, status)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                data.get('title', ''),
                data.get('description', ''),
                data.get('image_path', ''),
                data.get('link_url', ''),
                data.get('position', 'left'),
                1 if data.get('status', True) else 0
            ))
            
            banner_id = cursor.lastrowid
            conn.commit()
            conn.close()
            
            return jsonify({
                'success': True, 
                'banner_id': banner_id, 
                'message': 'Banner đã được tạo thành công'
            })
            
        elif request.method == 'PUT':
            data = request.get_json()
            if not data or not data.get('id'):
                return jsonify({'success': False, 'error': 'Missing banner ID'})
            
            banner_id = data.get('id')
            
            cursor.execute('''
                UPDATE banners 
                SET title = ?, description = ?, image_path = ?, link_url = ?, 
                    position = ?, status = ?
                WHERE id = ?
            ''', (
                data.get('title', ''),
                data.get('description', ''),
                data.get('image_path', ''),
                data.get('link_url', ''),
                data.get('position', 'left'),
                1 if data.get('status', True) else 0,
                banner_id
            ))
            
            conn.commit()
            conn.close()
            
            return jsonify({
                'success': True, 
                'message': 'Banner đã được cập nhật thành công'
            })
            
        elif request.method == 'DELETE':
            data = request.get_json()
            if not data or not data.get('id'):
                return jsonify({'success': False, 'error': 'Missing banner ID'})
            
            banner_id = data.get('id')
            
            # Get image path to delete file
            cursor.execute('SELECT image_path FROM banners WHERE id = ?', (banner_id,))
            result = cursor.fetchone()
            
            if result and result[0]:
                image_path = result[0]
                full_path = os.path.join(app.root_path, image_path.lstrip('/'))
                if os.path.exists(full_path):
                    try:
                        os.remove(full_path)
                    except:
                        pass
            
            cursor.execute('DELETE FROM banners WHERE id = ?', (banner_id,))
            conn.commit()
            conn.close()
            
            return jsonify({
                'success': True, 
                'message': 'Banner đã được xóa thành công'
            })
            
    except Exception as e:
        logger.error(f"Banner API error: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/upload', methods=['POST'])
@admin_required  
def admin_upload():
    try:
        if 'image' not in request.files:
            return jsonify({'success': False, 'error': 'Không có file hình ảnh'})
        
        file = request.files['image']
        if file.filename == '':
            return jsonify({'success': False, 'error': 'Chưa chọn file'})
        
        # Validate file extension
        allowed_extensions = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
        file_extension = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
        
        if file_extension not in allowed_extensions:
            return jsonify({'success': False, 'error': 'Định dạng file không được hỗ trợ'})
        
        # Generate unique filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}_{secure_filename(file.filename)}"
        file_path = os.path.join(UPLOAD_FOLDER, filename)
        
        # Save file
        file.save(file_path)
        
        # Return relative path for web use
        web_path = f"/static/uploads/banners/{filename}"
        
        return jsonify({
            'success': True,
            'image_path': web_path,
            'message': 'Hình ảnh đã được upload thành công'
        })
        
    except Exception as e:
        logger.error(f"Upload error: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/api/cache', methods=['GET', 'POST'])
@admin_required
def admin_cache():
    try:
        if request.method == 'GET':
            cache_info = get_cache_info()
            return jsonify({'success': True, 'cache_info': cache_info})
            
        elif request.method == 'POST':
            data = request.get_json()
            action = data.get('action')
            
            if action == 'cleanup_old':
                max_age_hours = data.get('max_age_hours', 24)
                deleted_count = cleanup_cache(max_age_hours)
                message = f'Đã xóa {deleted_count} file cache cũ hơn {max_age_hours} giờ'
                
            elif action == 'clear_all':
                deleted_count = clear_all_cache()
                message = f'Đã xóa tất cả {deleted_count} file cache'
                
            else:
                return jsonify({'success': False, 'error': 'Invalid action'})
            
            return jsonify({
                'success': True,
                'message': message,
                'deleted_count': deleted_count
            })
            
    except Exception as e:
        logger.error(f"Cache API error: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/api/settings', methods=['GET', 'POST'])
@admin_required
def admin_settings():
    try:
        conn = sqlite3.connect('subtitle_app.db')
        cursor = conn.cursor()
        
        if request.method == 'GET':
            cursor.execute('SELECT key, value, description FROM settings')
            settings = []
            for row in cursor.fetchall():
                # Don't expose password hash
                if row[0] == 'admin_password_hash':
                    continue
                settings.append({
                    'key': row[0],
                    'value': row[1],
                    'description': row[2]
                })
            
            conn.close()
            return jsonify({'success': True, 'settings': settings})
            
        elif request.method == 'POST':
            data = request.get_json()
            settings_to_update = data.get('settings', {})
            
            for key, value in settings_to_update.items():
                if key == 'admin_password':
                    if len(value) < 6:
                        conn.close()
                        return jsonify({'success': False, 'error': 'Mật khẩu phải có ít nhất 6 ký tự'})
                    
                    password_hash = generate_password_hash(value)
                    cursor.execute('UPDATE settings SET value = ? WHERE key = ?', 
                                 (password_hash, 'admin_password_hash'))
                else:
                    cursor.execute('UPDATE settings SET value = ? WHERE key = ?', (value, key))
            
            conn.commit()
            conn.close()
            
            return jsonify({'success': True, 'message': 'Cài đặt đã được cập nhật'})
            
    except Exception as e:
        logger.error(f"Settings API error: {e}")
        return jsonify({'success': False, 'error': str(e)})

if __name__ == '__main__':
    print("🎬 YouTube Subtitle Downloader - Simplified Admin Panel")
    print("📍 Server: http://localhost:5008")
    print("🎯 Admin Panel: http://localhost:5008/admin")
    print("🔑 Default Login: admin / admin123")
    print("✨ Features:")
    print("  - Dashboard with basic stats")
    print("  - User data (visitors)")
    print("  - Download statistics")
    print("  - Cache management")
    print("  - Banner management")
    print("  - Settings (password change)")
    print("-" * 50)
    
    try:
        app.run(debug=False, host='0.0.0.0', port=5008, threaded=True)
    except Exception as e:
        logger.error(f"❌ Server error: {e}")