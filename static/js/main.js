$(document).ready(function() {
    let currentVideoInfo = null;
    
    // Get video info when URL is entered
    $('#get-info-btn').click(function() {
        const url = $('#youtube-url').val().trim();
        
        if (!url) {
            showAlert('Vui lòng nhập URL YouTube', 'error');
            return;
        }
        
        if (!isValidYouTubeUrl(url)) {
            showAlert('URL không hợp lệ. Vui lòng nhập URL YouTube', 'error');
            return;
        }
        
        getVideoInfo(url);
    });
    
    // Allow Enter key to trigger get info
    $('#youtube-url').keypress(function(e) {
        if (e.which === 13) {
            $('#get-info-btn').click();
        }
    });
    
    // Download button handlers
    $('.btn-download').click(function() {
        const format = $(this).data('format');
        const url = $('#youtube-url').val().trim();
        const sourceLanguage = $('#source-language').val();
        
        if (!currentVideoInfo) {
            showAlert('Vui lòng lấy thông tin video trước', 'error');
            return;
        }
        
        downloadSubtitle(url, format, sourceLanguage);
    });

    // Preview button handler
    $('#preview-btn').click(function() {
        const url = $('#youtube-url').val().trim();
        const sourceLanguage = $('#source-language').val();
        
        if (!currentVideoInfo) {
            showAlert('Vui lòng lấy thông tin video trước', 'error');
            return;
        }
        
        previewSubtitle(url, sourceLanguage);
    });
    
    // Modal close handlers
    $('.close').click(function() {
        $(this).closest('.modal').hide();
    });
    
    $(window).click(function(event) {
        if ($(event.target).hasClass('modal')) {
            $('.modal').hide();
        }
    });
    
    // Functions
    function isValidYouTubeUrl(url) {
        const youtubeRegex = /^(https?:\/\/)?(www\.)?(youtube\.com|youtu\.be)\/.+$/;
        return youtubeRegex.test(url);
    }
    
    function getVideoInfo(url) {
        showLoading();
        
        $.ajax({
            url: '/get-video-info',
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ url: url }),
            success: function(response) {
                hideLoading();
                
                if (response.success) {
                    currentVideoInfo = response.video_info;
                    displayVideoInfo(response.video_info);
                    updateLanguageOptions(response.available_subtitles);
                    $('#video-preview').show().addClass('fade-in');
                } else {
                    showAlert(response.message, 'error');
                }
            },
            error: function(xhr, status, error) {
                hideLoading();
                showAlert('Lỗi kết nối: ' + error, 'error');
            }
        });
    }
    
    function displayVideoInfo(videoInfo) {
        $('#video-thumbnail').attr('src', videoInfo.thumbnail);
        $('#video-title').text(videoInfo.title);
        $('#video-uploader').text(videoInfo.uploader || 'Unknown');
        $('#video-duration').text(formatDuration(videoInfo.duration));
        $('#video-views').text(formatNumber(videoInfo.view_count));
    }
    
    function updateLanguageOptions(availableSubtitles) {
        const sourceSelect = $('#source-language');
        
        // Clear existing options except default English
        sourceSelect.find('option').slice(1).remove();
        
        // Add available subtitle languages
        availableSubtitles.forEach(function(subtitle) {
            const option = $('<option>', {
                value: subtitle.code,
                text: `${subtitle.name} (${subtitle.type})`
            });
            sourceSelect.append(option);
        });
        
        // Keep English as default selected
        sourceSelect.val('en');
    }
    
    function downloadSubtitle(url, format, sourceLanguage) {
        showLoading();
        
        const data = {
            url: url,
            format: format,
            language: sourceLanguage
        };
        
        fetch('/download-subtitle', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(data)
        })
        .then(response => {
            hideLoading();
            
            if (!response.ok) {
                return response.json().then(err => {
                    throw new Error(err.message || 'Download failed');
                });
            }
            
            // Get filename from header
            const contentDisposition = response.headers.get('Content-Disposition');
            let filename = 'subtitle.' + format;
            
            if (contentDisposition) {
                const matches = contentDisposition.match(/filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/);
                if (matches && matches[1]) {
                    filename = matches[1].replace(/['"]/g, '');
                }
            }
            
            return response.blob().then(blob => ({ blob, filename }));
        })
        .then(({ blob, filename }) => {
            // Create download link
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            window.URL.revokeObjectURL(url);
            
            showAlert('Tải xuống thành công!', 'success');
        })
        .catch(error => {
            hideLoading();
            showAlert('Lỗi tải xuống: ' + error.message, 'error');
        });
    }
    
    function previewSubtitle(url, sourceLanguage) {
        showLoading();
        
        const data = {
            url: url,
            language: sourceLanguage
        };
        
        $.ajax({
            url: '/preview-subtitle',
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify(data),
            success: function(response) {
                hideLoading();
                
                if (response.success) {
                    $('#preview-content').text(response.content);
                    $('#preview-modal').show();
                } else {
                    showAlert(response.message, 'error');
                }
            },
            error: function(xhr, status, error) {
                hideLoading();
                showAlert('Lỗi xem trước: ' + error, 'error');
            }
        });
    }
    
    function showLoading() {
        $('#loading-overlay').show();
    }
    
    function hideLoading() {
        $('#loading-overlay').hide();
    }
    
    function showAlert(message, type) {
        $('.alert').remove();
        
        const alertClass = type === 'error' ? 'alert-error' : 
                          type === 'success' ? 'alert-success' : 'alert-info';
        
        const icon = type === 'error' ? 'fas fa-exclamation-circle' : 
                    type === 'success' ? 'fas fa-check-circle' : 'fas fa-info-circle';
        
        const alert = $(`
            <div class="alert ${alertClass} fade-in">
                <i class="${icon}"></i>
                ${message}
            </div>
        `);
        
        $('.download-form').prepend(alert);
        
        setTimeout(function() {
            alert.fadeOut(function() {
                $(this).remove();
            });
        }, 5000);
    }
    
    function formatDuration(seconds) {
        if (!seconds) return 'N/A';
        
        const hours = Math.floor(seconds / 3600);
        const minutes = Math.floor((seconds % 3600) / 60);
        const secs = seconds % 60;
        
        if (hours > 0) {
            return `${hours}:${minutes.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
        } else {
            return `${minutes}:${secs.toString().padStart(2, '0')}`;
        }
    }
    
    function formatNumber(num) {
        if (!num) return 'N/A';
        
        if (num >= 1000000) {
            return (num / 1000000).toFixed(1) + 'M';
        } else if (num >= 1000) {
            return (num / 1000).toFixed(1) + 'K';
        } else {
            return num.toString();
        }
    }
    
    // Real-time URL validation
    $('#youtube-url').on('input', function() {
        const url = $(this).val().trim();
        const button = $('#get-info-btn');
        
        if (url && isValidYouTubeUrl(url)) {
            button.prop('disabled', false).removeClass('disabled');
            $(this).removeClass('error');
        } else {
            button.prop('disabled', true).addClass('disabled');
            if (url) {
                $(this).addClass('error');
            } else {
                $(this).removeClass('error');
            }
        }
    });
    
    // Language selection change handler
    $('#source-language').change(function() {
        const sourceLanguage = $('#source-language').val();
        
        if (sourceLanguage) {
            $('.btn-download, #preview-btn').prop('disabled', false);
        } else {
            $('.btn-download, #preview-btn').prop('disabled', true);
        }
    });
    
    // Keyboard shortcuts
    $(document).keydown(function(e) {
        if (e.ctrlKey && e.which === 13) {
            $('#get-info-btn').click();
        }
        
        if (e.which === 27) {
            $('.modal').hide();
        }
    });
    
    // Auto-save form data
    const formData = JSON.parse(localStorage.getItem('subtitleFormData') || '{}');
    
    if (formData.url) $('#youtube-url').val(formData.url);
    if (formData.sourceLanguage) $('#source-language').val(formData.sourceLanguage);
    
    $('#youtube-url, #source-language').change(function() {
        const data = {
            url: $('#youtube-url').val(),
            sourceLanguage: $('#source-language').val()
        };
        localStorage.setItem('subtitleFormData', JSON.stringify(data));
    });
    
    // Clear form data
    $('.clear-form').click(function() {
        $('#youtube-url').val('');
        $('#source-language').val('en');
        $('#video-preview').hide();
        currentVideoInfo = null;
        localStorage.removeItem('subtitleFormData');
        showAlert('Đã xóa dữ liệu form', 'info');
    });
    
    console.log('YouTube Subtitle Downloader initialized');
});
