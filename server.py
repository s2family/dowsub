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

app = Flask(__name__)
app.secret_key = 'downsub-secret-key-change-in-production'

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
        ('admin_password', 'admin123', 'Admin password'),
        ('max_subtitle_cache', '100', 'Maximum subtitle files to cache'),
        ('cleanup_interval_hours', '24', 'Hours to keep subtitle files'),
        ('auto_cleanup_enabled', 'true', 'Enable automatic cleanup'),
        ('site_title', 'YouTube Subtitle Downloader', 'Site title'),
        ('site_description', 'Tải phụ đề YouTube miễn phí - Hỗ trợ định dạng SRT và TXT chất lượng cao', 'Site description')
    ]
    
    for key, value, desc in default_settings:
        cursor.execute('INSERT OR IGNORE INTO settings (key, value, description) VALUES (?, ?, ?)', 
                      (key, value, desc))
    
    conn.commit()
    conn.close()

# Initialize database
init_db()

# ===== SUBTITLE CACHE DIRECTORY =====
SUBTITLE_CACHE_DIR = "subtitle_cache"
os.makedirs(SUBTITLE_CACHE_DIR, exist_ok=True)

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
                print(f"Visitor cleanup error: {e}")

visitor_tracker = VisitorTracker()

# ===== YOUTUBE SUBTITLE EXTRACTOR =====
class YouTubeSubtitleExtractor:
    def __init__(self):
        self.setup_ytdlp()
    
    def setup_ytdlp(self):
        """Setup yt-dlp for subtitle extraction"""
        try:
            import yt_dlp
            self.ytdlp_available = True
            print("✅ yt-dlp is available")
        except ImportError:
            print("⚠️ Installing yt-dlp...")
            try:
                subprocess.check_call([sys.executable, "-m", "pip", "install", "yt-dlp"])
                import yt_dlp
                self.ytdlp_available = True
                print("✅ yt-dlp installed successfully")
            except Exception as e:
                print(f"❌ Failed to install yt-dlp: {e}")
                self.ytdlp_available = False
    
    def extract_video_id(self, url):
        """Extract YouTube video ID from URL"""
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
        """Get video information and available subtitles"""
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
                    'upload_date': info.get('upload_date'),
                    'uploader': info.get('uploader'),
                    'view_count': info.get('view_count'),
                    'thumbnail': info.get('thumbnail'),
                }
                
                # Get available subtitles
                subtitles = info.get('subtitles', {})
                auto_subtitles = info.get('automatic_captions', {})
                
                available_subs = {}
                
                # Manual subtitles (higher priority)
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
        """Download subtitle for specific language and format"""
        if not self.ytdlp_available:
            return None, "yt-dlp not available"
        
        try:
            import yt_dlp
            
            video_id = self.extract_video_id(video_url)
            if not video_id:
                return None, "Invalid YouTube URL"
            
            # Create unique filename
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
                'subtitlesformat': 'vtt',  # Download as VTT first, then convert
                'outtmpl': output_path,
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_url])
            
            # Find the downloaded VTT file
            vtt_file = f"{output_path}.{language}.vtt"
            if not os.path.exists(vtt_file):
                # Try automatic captions
                vtt_file = f"{output_path}.{language}.{language}.vtt"
            
            if not os.path.exists(vtt_file):
                return None, f"Subtitle not found for language: {language}"
            
            # Convert VTT to requested format
            if format == 'srt':
                srt_content = self.convert_vtt_to_srt(vtt_file)
                srt_file = f"{output_path}.srt"
                with open(srt_file, 'w', encoding='utf-8') as f:
                    f.write(srt_content)
                
                # Clean up VTT file
                if os.path.exists(vtt_file):
                    os.remove(vtt_file)
                
                return srt_file, None
                
            elif format == 'txt':
                txt_content = self.convert_vtt_to_txt(vtt_file)
                txt_file = f"{output_path}.txt"
                with open(txt_file, 'w', encoding='utf-8') as f:
                    f.write(txt_content)
                
                # Clean up VTT file
                if os.path.exists(vtt_file):
                    os.remove(vtt_file)
                
                return txt_file, None
                
        except Exception as e:
            return None, str(e)
    
    def convert_vtt_to_srt(self, vtt_file):
        """Convert VTT subtitle to SRT format - Fixed duplicate issue"""
        try:
            with open(vtt_file, 'r', encoding='utf-8') as f:
                vtt_content = f.read()
            
            # Parse VTT and convert to SRT
            lines = vtt_content.split('\n')
            srt_entries = []
            
            i = 0
            while i < len(lines):
                line = lines[i].strip()
                
                # Skip header and empty lines
                if not line or line.startswith('WEBVTT') or line.startswith('Kind:') or line.startswith('Language:') or line.startswith('NOTE'):
                    i += 1
                    continue
                
                # Check if line contains timestamp
                if '-->' in line and ':' in line:
                    # Clean timestamp line - remove VTT styling
                    timestamp_line = re.sub(r' align:\w+', '', line)
                    timestamp_line = re.sub(r' position:\d+%', '', timestamp_line)
                    timestamp_line = re.sub(r' size:\d+%', '', timestamp_line)
                    timestamp_line = re.sub(r' line:[^\s]+', '', timestamp_line)
                    timestamp_line = timestamp_line.strip()
                    
                    # Convert dots to commas for SRT format
                    timestamp_line = timestamp_line.replace('.', ',')
                    
                    # Collect all text for this subtitle block
                    text_lines = []
                    i += 1
                    
                    # Read text lines until we hit empty line or next timestamp
                    while i < len(lines):
                        text_line = lines[i].strip()
                        
                        # Stop at empty line or next timestamp
                        if not text_line or ('-->' in text_line and ':' in text_line):
                            break
                        
                        # Remove HTML tags
                        clean_text = re.sub(r'<[^>]+>', '', text_line).strip()
                        
                        if clean_text:
                            text_lines.append(clean_text)
                        
                        i += 1
                    
                    # Only add if we have text and timestamp is valid
                    if text_lines and '-->' in timestamp_line:
                        # Join all text lines and remove duplicates
                        full_text = ' '.join(text_lines)
                        
                        # Skip if this subtitle block already exists
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
            
            # Generate SRT content
            srt_lines = []
            for index, entry in enumerate(srt_entries, 1):
                srt_lines.append(str(index))
                srt_lines.append(entry['timestamp'])
                srt_lines.append(entry['text'])
                srt_lines.append('')  # Empty line
            
            return '\n'.join(srt_lines)
            
        except Exception as e:
            print(f"VTT to SRT conversion error: {e}")
            return ""

    def convert_vtt_to_txt(self, vtt_file):
        """Convert VTT subtitle to plain text format - Fixed duplicate issue"""
        try:
            with open(vtt_file, 'r', encoding='utf-8') as f:
                vtt_content = f.read()
            
            # Extract unique sentences
            lines = vtt_content.split('\n')
            sentences = []
            seen_sentences = set()
            
            for line in lines:
                line = line.strip()
                
                # Skip headers, timestamps, and empty lines
                if (not line or 
                    line.startswith('WEBVTT') or 
                    line.startswith('Kind:') or 
                    line.startswith('Language:') or 
                    line.startswith('NOTE') or
                    '-->' in line or
                    ':' in line and len(line.split(':')) >= 3):  # Skip timestamp lines
                    continue
                
                # Clean HTML tags
                clean_line = re.sub(r'<[^>]+>', '', line).strip()
                
                if clean_line and clean_line not in seen_sentences:
                    seen_sentences.add(clean_line)
                    sentences.append(clean_line)
            
            # Join sentences and split by common sentence endings
            full_text = ' '.join(sentences)
            
            # Split into proper sentences
            sentence_endings = re.split(r'[.!?]+\s+', full_text)
            final_sentences = []
            
            for sentence in sentence_endings:
                sentence = sentence.strip()
                if sentence and sentence not in final_sentences:
                    final_sentences.append(sentence)
            
            return '\n'.join(final_sentences)
            
        except Exception as e:
            print(f"VTT to TXT conversion error: {e}")
            return ""


# Initialize subtitle extractor
subtitle_extractor = YouTubeSubtitleExtractor()

# ===== ADMIN AUTHENTICATION =====
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

def get_banners(position=None):
    """Get banners from database"""
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
        
        print(f"📢 Loaded {len(banner_list)} banners for position: {position}")
        return banner_list
        
    except Exception as e:
        print(f"❌ Error getting banners: {e}")
        return []

# ===== MIDDLEWARE =====
@app.before_request
def track_visitors():
    """Track visitors for all requests"""
    if request.endpoint and not request.endpoint.startswith('static'):
        visitor_tracker.track_visitor(request)

@app.route('/')
def index():
    """Main page - YouTube Subtitle Downloader"""
    try:
        # Get banners from database
        left_banners = get_banners('left')
        right_banners = get_banners('right')
        
        print(f"🏠 Homepage loaded: {len(left_banners)} left banners, {len(right_banners)} right banners")
        
        return render_template('index.html', 
                             left_banners=left_banners, 
                             right_banners=right_banners)
    except Exception as e:
        print(f"❌ Homepage error: {e}")
        return render_template('index.html', 
                             left_banners=[], 
                             right_banners=[])

@app.route('/get_video_info', methods=['POST'])
def get_video_info():
    """Get YouTube video information and available subtitles"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'Invalid JSON data'})
        
        video_url = data.get('url', '').strip()
        
        if not video_url:
            return jsonify({'success': False, 'message': 'URL không được để trống'})
        
        # Validate YouTube URL
        video_id = subtitle_extractor.extract_video_id(video_url)
        if not video_id:
            return jsonify({'success': False, 'message': 'URL YouTube không hợp lệ'})
        
        print(f"🔍 Getting info for video: {video_id}")
        
        # Get video info and subtitles
        video_info, error = subtitle_extractor.get_video_info(video_url)
        
        if error:
            return jsonify({'success': False, 'message': f'Lỗi: {error}'})
        
        if not video_info:
            return jsonify({'success': False, 'message': 'Không thể lấy thông tin video'})
        
        # Check if subtitles are available
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
        print(f"Get video info error: {e}")
        return jsonify({'success': False, 'message': f'Lỗi server: {str(e)}'})

@app.route('/download_subtitle', methods=['POST'])
def download_subtitle():
    """Download subtitle in specified format"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'Invalid JSON data'})
        
        video_url = data.get('url', '').strip()
        language = data.get('language', 'vi')
        format = data.get('format', 'srt')
        
        if not video_url:
            return jsonify({'success': False, 'message': 'URL không được để trống'})
        
        # Validate format - Only SRT and TXT allowed
        if format not in ['srt', 'txt']:
            return jsonify({'success': False, 'message': 'Format không hỗ trợ (chỉ hỗ trợ SRT và TXT)'})
        
        video_id = subtitle_extractor.extract_video_id(video_url)
        if not video_id:
            return jsonify({'success': False, 'message': 'URL YouTube không hợp lệ'})
        
        print(f"📥 Downloading subtitle: {video_id}, lang: {language}, format: {format}")
        
        # Download subtitle
        subtitle_file, error = subtitle_extractor.download_subtitle(video_url, language, format)
        
        if error:
            return jsonify({'success': False, 'message': f'Lỗi: {error}'})
        
        if not subtitle_file or not os.path.exists(subtitle_file):
            return jsonify({'success': False, 'message': 'Không thể tải phụ đề'})
        
        # Track download in database
        try:
            # Get video info for tracking
            video_info, _ = subtitle_extractor.get_video_info(video_url)
            
            conn = sqlite3.connect('subtitle_app.db')
            cursor = conn.cursor()
            
            # Check if already exists
            cursor.execute('''
                SELECT id, download_count FROM subtitle_downloads 
                WHERE video_id = ? AND language = ? AND format = ?
            ''', (video_id, language, format))
            
            existing = cursor.fetchone()
            
            if existing:
                # Update download count
                cursor.execute('''
                    UPDATE subtitle_downloads 
                    SET download_count = download_count + 1, last_downloaded = CURRENT_TIMESTAMP
                    WHERE id = ?
                ''', (existing[0],))
            else:
                # Insert new record
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
            print(f"Database tracking error: {e}")
        
        # Return file info for download
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
        print(f"Download subtitle error: {e}")
        return jsonify({'success': False, 'message': f'Lỗi server: {str(e)}'})

@app.route('/download_file/<filename>')
def download_file(filename):
    """Serve downloaded subtitle files"""
    try:
        file_path = os.path.join(SUBTITLE_CACHE_DIR, filename)
        
        if not os.path.exists(file_path):
            return "File not found", 404
        
        # Determine original filename
        if filename.endswith('.srt'):
            download_name = filename.replace(filename.split('_')[-1], '').rstrip('_') + '.srt'
        elif filename.endswith('.txt'):
            download_name = filename.replace(filename.split('_')[-1], '').rstrip('_') + '.txt'
        else:
            download_name = filename
        
        return send_file(file_path, as_attachment=True, download_name=download_name)
        
    except Exception as e:
        print(f"File download error: {e}")
        return f"Error: {e}", 500

# ===== BANNER ROUTES =====
@app.route('/banner/click/<int:banner_id>')
def banner_click(banner_id):
    """Track banner clicks and redirect"""
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
        print(f"Banner click error: {e}")
        return redirect(url_for('index'))

# ===== ADMIN ROUTES =====
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    """Admin login page"""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        conn = sqlite3.connect('subtitle_app.db')
        cursor = conn.cursor()
        cursor.execute('SELECT value FROM settings WHERE key = ?', ('admin_username',))
        db_username = cursor.fetchone()[0]
        cursor.execute('SELECT value FROM settings WHERE key = ?', ('admin_password',))
        db_password = cursor.fetchone()[0]
        conn.close()
        
        if username == db_username and password == db_password:
            session['admin_logged_in'] = True
            session['admin_username'] = username
            return redirect(url_for('admin_dashboard'))
        else:
            return render_template('admin_login.html', error='Sai tên đăng nhập hoặc mật khẩu')
    
    return render_template('admin_login.html')

@app.route('/admin/logout')
def admin_logout():
    """Admin logout"""
    session.pop('admin_logged_in', None)
    session.pop('admin_username', None)
    return redirect(url_for('admin_login'))

@app.route('/admin')
@admin_required
def admin_dashboard():
    """Admin dashboard"""
    return render_template('admin.html')

# ===== ADMIN API ROUTES =====
@app.route('/admin/api/visitors')
@admin_required
def admin_visitors():
    """Get visitor details"""
    try:
        conn = sqlite3.connect('subtitle_app.db')
        cursor = conn.cursor()
        
        # Recent visitors
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
                'session_id': row[0][:8] + '...',  # Truncate for privacy
                'ip_address': row[1],
                'user_agent': row[2][:50] + '...' if len(row[2]) > 50 else row[2],
                'first_visit': row[3],
                'last_activity': row[4],
                'page_views': row[5],
                'is_active': bool(row[6]),
                'duration': str(datetime.fromisoformat(row[4]) - datetime.fromisoformat(row[3])) if row[4] and row[3] else 'N/A'
            })
        
        conn.close()
        
        return jsonify({
            'success': True,
            'visitors': visitors,
            'active_count': visitor_tracker.get_active_count()
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# Thêm route này nếu chưa có
@app.route('/admin/api/banners', methods=['GET', 'POST', 'PUT', 'DELETE'])
@admin_required
def admin_banners():
    """Banner CRUD operations"""
    try:
        conn = sqlite3.connect('subtitle_app.db')
        cursor = conn.cursor()
        
        if request.method == 'GET':
            # Get all banners
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
            # Create new banner
            data = request.get_json()
            if not data:
                return jsonify({'success': False, 'error': 'No JSON data received'})
            
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
            # Update banner
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
            # Delete banner
            data = request.get_json()
            if not data or not data.get('id'):
                return jsonify({'success': False, 'error': 'Missing banner ID'})
            
            banner_id = data.get('id')
            
            cursor.execute('DELETE FROM banners WHERE id = ?', (banner_id,))
            conn.commit()
            conn.close()
            
            return jsonify({
                'success': True, 
                'message': 'Banner đã được xóa thành công'
            })
            
    except Exception as e:
        print(f"Banner API error: {e}")  # Debug log
        return jsonify({'success': False, 'error': str(e)})

# Sửa lại route upload
@app.route('/admin/upload', methods=['POST'])
@admin_required  
def admin_upload():
    """Upload banner images"""
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
        
        # Create upload directory
        upload_dir = os.path.join('static', 'uploads', 'banners')
        os.makedirs(upload_dir, exist_ok=True)
        
        # Generate unique filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}_{file.filename}"
        file_path = os.path.join(upload_dir, filename)
        
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
        print(f"Upload error: {e}")  # Debug log
        return jsonify({'success': False, 'error': str(e)})


@app.route('/admin/api/audio/cleanup', methods=['POST'])
@admin_required
def admin_audio_cleanup():
    """Audio cleanup operations"""
    try:
        data = request.get_json()
        action = data.get('action')
        
        if action == 'cleanup_old':
            max_age_hours = data.get('max_age_hours', 24)
            # Simple cleanup - remove files older than max_age_hours
            import glob
            current_time = time.time()
            deleted_count = 0
            
            for file_path in glob.glob(os.path.join(SUBTITLE_CACHE_DIR, "*")):
                if os.path.isfile(file_path):
                    file_age = current_time - os.path.getctime(file_path)
                    if file_age > (max_age_hours * 3600):
                        try:
                            os.remove(file_path)
                            deleted_count += 1
                        except:
                            pass
            
            message = f'Đã xóa {deleted_count} file cache cũ hơn {max_age_hours} giờ'
            
        elif action == 'clear_all':
            # Remove all files in cache directory
            import glob
            deleted_count = 0
            
            for file_path in glob.glob(os.path.join(SUBTITLE_CACHE_DIR, "*")):
                if os.path.isfile(file_path):
                    try:
                        os.remove(file_path)
                        deleted_count += 1
                    except:
                        pass
            
            message = f'Đã xóa tất cả {deleted_count} file cache'
            
        else:
            return jsonify({'success': False, 'error': 'Invalid action'})
        
        return jsonify({
            'success': True,
            'message': message,
            'deleted_count': deleted_count
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/api/settings', methods=['GET', 'POST'])
@admin_required
def admin_settings():
    """Get/Update settings"""
    try:
        conn = sqlite3.connect('subtitle_app.db')
        cursor = conn.cursor()
        
        if request.method == 'GET':
            cursor.execute('SELECT key, value, description FROM settings')
            settings = []
            for row in cursor.fetchall():
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
                cursor.execute('UPDATE settings SET value = ? WHERE key = ?', (value, key))
            
            conn.commit()
            conn.close()
            
            return jsonify({'success': True, 'message': 'Cài đặt đã được cập nhật'})
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

if __name__ == '__main__':
    print("🎬 YouTube Subtitle Downloader")
    print("📍 Server: http://localhost:5008")
    print("🎯 Admin Panel: http://localhost:5008/admin")
    print("🔑 Default Login: admin / admin123")
    print("✨ Features: YouTube subtitle extraction, SRT/TXT download only")
    print("📚 Supported: Vietnamese & multiple languages")
    print("🚫 Removed: VTT format, video descriptions")
    print("-" * 70)
    
    try:
        app.run(debug=False, host='0.0.0.0', port=5008, threaded=True)
    except Exception as e:
        print(f"❌ Server error: {e}")