$(document).ready(function() {
    // Admin Panel JavaScript
    
    let currentSection = 'dashboard';
    let charts = {};
    
    // Initialize admin panel
    init();
    
    function init() {
        // Load initial data
        loadDashboardData();
        
        // Set up navigation
        setupNavigation();
        
        // Set up auto-refresh
        setInterval(function() {
            if (currentSection === 'dashboard') {
                loadDashboardData();
            } else if (currentSection === 'visitors') {
                loadVisitors();
            }
        }, 30000); // Refresh every 30 seconds
        
        console.log('Admin panel initialized');
    }
    
    function setupNavigation() {
        $('.nav-item').click(function(e) {
            e.preventDefault();
            
            const section = $(this).data('section');
            
            // Update active nav item
            $('.nav-item').removeClass('active');
            $(this).addClass('active');
            
            // Show section
            showSection(section);
        });
    }
    
    function showSection(section) {
        currentSection = section;
        
        // Hide all sections
        $('.content-section').removeClass('active');
        
        // Show selected section
        $(`#${section}-section`).addClass('active');
        
        // Update page title
        const titles = {
            dashboard: 'Dashboard',
            visitors: 'Quản lý Visitors',
            banners: 'Quản lý Banners',
            downloads: 'Thống kê Downloads',
            settings: 'Cài đặt hệ thống'
        };
        
        $('#page-title').text(titles[section] || 'Dashboard');
        
        // Load section data
        switch(section) {
            case 'dashboard':
                loadDashboardData();
                break;
            case 'visitors':
                loadVisitors();
                break;
            case 'banners':
                loadBanners();
                break;
            case 'downloads':
                loadDownloads();
                break;
            case 'settings':
                loadSettings();
                break;
        }
    }
    
    function loadDashboardData() {
        $.ajax({
            url: '/admin/api/stats',
            method: 'GET',
            success: function(response) {
                if (response.success) {
                    updateDashboardStats(response.stats);
                    updatePopularVideos(response.stats.downloads.popular_videos);
                    loadFormatChart();
                }
            },
            error: function(xhr, status, error) {
                console.error('Failed to load dashboard data:', error);
            }
        });
    }
    
    function updateDashboardStats(stats) {
        $('#active-visitors').text(stats.visitors.active_now);
        $('#today-visitors').text(stats.visitors.today_unique);
        $('#total-downloads').text(stats.downloads.total_downloads);
        $('#banner-clicks').text(stats.banners.total_clicks);
        
        // Add animation
        $('.stat-card').addClass('fade-in');
    }
    
    function updatePopularVideos(videos) {
        const container = $('#popular-videos');
        container.empty();
        
        if (videos.length === 0) {
            container.html('<p>Chưa có dữ liệu</p>');
            return;
        }
        
        videos.forEach(function(video) {
            const item = $(`
                <div class="popular-video-item">
                    <h4 class="popular-video-title" title="${video.title}">${video.title}</h4>
                    <span class="popular-video-count">${video.downloads}</span>
                </div>
            `);
            container.append(item);
        });
    }
    
    function loadFormatChart() {
        $.ajax({
            url: '/admin/api/downloads',
            method: 'GET',
            success: function(response) {
                if (response.success && response.format_stats) {
                    updateFormatChart(response.format_stats);
                }
            }
        });
    }
    
    function updateFormatChart(formatStats) {
        const ctx = document.getElementById('format-chart').getContext('2d');
        
        if (charts.formatChart) {
            charts.formatChart.destroy();
        }
        
        charts.formatChart = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: formatStats.map(f => f.format.toUpperCase()),
                datasets: [{
                    data: formatStats.map(f => f.count),
                    backgroundColor: ['#667eea', '#764ba2', '#f093fb', '#43e97b'],
                    borderWidth: 0
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        position: 'bottom'
                    }
                }
            }
        });
    }
    
    function loadVisitors() {
        $.ajax({
            url: '/admin/api/visitors',
            method: 'GET',
            success: function(response) {
                if (response.success) {
                    updateVisitorsTable(response.visitors);
                }
            },
            error: function(xhr, status, error) {
                console.error('Failed to load visitors:', error);
            }
        });
    }
    
    function updateVisitorsTable(visitors) {
        const tbody = $('#visitors-table tbody');
        tbody.empty();
        
        visitors.forEach(function(visitor) {
            const statusBadge = visitor.is_active ? 
                '<span class="status-badge status-active">Online</span>' : 
                '<span class="status-badge status-inactive">Offline</span>';
            
            const row = $(`
                <tr>
                    <td>${visitor.session_id}</td>
                    <td>${visitor.ip_address}</td>
                    <td>${visitor.user_agent}</td>
                    <td>${formatDateTime(visitor.first_visit)}</td>
                    <td>${formatDateTime(visitor.last_activity)}</td>
                    <td>${visitor.page_views}</td>
                    <td>${statusBadge}</td>
                </tr>
            `);
            tbody.append(row);
        });
    }
    
    function loadBanners() {
        $.ajax({
            url: '/admin/api/banners',
            method: 'GET',
            success: function(response) {
                if (response.success) {
                    updateBannersGrid(response.banners);
                }
            },
            error: function(xhr, status, error) {
                console.error('Failed to load banners:', error);
            }
        });
    }
    
    function updateBannersGrid(banners) {
        const container = $('#banners-list');
        container.empty();
        
        banners.forEach(function(banner) {
            const statusBadge = banner.status ? 
                '<span class="status-badge status-active">Active</span>' : 
                '<span class="status-badge status-inactive">Inactive</span>';
            
            const card = $(`
                <div class="banner-card">
                    ${banner.image_path ? `<img src="${banner.image_path}" alt="${banner.title}">` : ''}
                    <div class="banner-card-content">
                        <h4 class="banner-card-title">${banner.title}</h4>
                        <div class="banner-card-meta">
                            ${statusBadge} • ${banner.clicks} clicks • ${formatDateTime(banner.created_at)}
                        </div>
                        <p>${banner.description || ''}</p>
                        <div class="banner-card-actions">
                            <button class="btn btn-sm btn-primary edit-banner" data-id="${banner.id}">
                                <i class="fas fa-edit"></i> Sửa
                            </button>
                            <button class="btn btn-sm btn-danger delete-banner" data-id="${banner.id}">
                                <i class="fas fa-trash"></i> Xóa
                            </button>
                        </div>
                    </div>
                </div>
            `);
            container.append(card);
        });
    }
    
    function loadDownloads() {
        $.ajax({
            url: '/admin/api/downloads',
            method: 'GET',
            success: function(response) {
                if (response.success) {
                    updateDownloadsTable(response.recent_downloads);
                }
            },
            error: function(xhr, status, error) {
                console.error('Failed to load downloads:', error);
            }
        });
    }
    
    function updateDownloadsTable(downloads) {
        const tbody = $('#downloads-table tbody');
        tbody.empty();
        
        downloads.forEach(function(download) {
            const row = $(`
                <tr>
                    <td>${download.video_title}</td>
                    <td><span class="status-badge">${download.format_type.toUpperCase()}</span></td>
                    <td>${download.download_count}</td>
                    <td>${formatDateTime(download.last_download)}</td>
                    <td><a href="${download.video_url}" target="_blank">Xem video</a></td>
                </tr>
            `);
            tbody.append(row);
        });
    }
    
    function loadSettings() {
        $.ajax({
            url: '/admin/api/settings',
            method: 'GET',
            success: function(response) {
                if (response.success) {
                    updateSettingsForm(response.settings);
                }
            },
            error: function(xhr, status, error) {
                console.error('Failed to load settings:', error);
            }
        });
    }
    
    function updateSettingsForm(settings) {
        const container = $('#settings-form');
        container.empty();
        
        settings.forEach(function(setting) {
            const formGroup = $(`
                <div class="form-group">
                    <label for="setting-${setting.key}">${setting.description}</label>
                    <input type="text" id="setting-${setting.key}" name="${setting.key}" value="${setting.value}">
                </div>
            `);
            container.append(formGroup);
        });
    }
    
    // Banner management
    window.showBannerModal = function(bannerId = null) {
        const modal = $('#banner-modal');
        const title = $('#banner-modal-title');
        const form = $('#banner-form')[0];
        
        if (bannerId) {
            title.text('Chỉnh sửa Banner');
            loadBannerData(bannerId);
        } else {
            title.text('Thêm Banner');
            form.reset();
            $('#banner-id').val('');
        }
        
        modal.addClass('active');
    };
    
    window.closeBannerModal = function() {
        $('#banner-modal').removeClass('active');
    };
    
    function loadBannerData(bannerId) {
        // Load banner data for editing
        $.ajax({
            url: '/admin/api/banners',
            method: 'GET',
            success: function(response) {
                if (response.success) {
                    const banner = response.banners.find(b => b.id === bannerId);
                    if (banner) {
                        $('#banner-id').val(banner.id);
                        $('#banner-title').val(banner.title);
                        $('#banner-description').val(banner.description);
                        $('#banner-link').val(banner.link_url);
                        $('#banner-position').val(banner.position);
                        $('#banner-status').prop('checked', banner.status);
                        
                        if (banner.image_path) {
                            $('#image-preview').html(`<img src="${banner.image_path}" alt="Preview">`);
                        }
                    }
                }
            }
        });
    }
    
    // Banner form submission
    $('#banner-form').submit(function(e) {
        e.preventDefault();
        
        const bannerId = $('#banner-id').val();
        const isEdit = bannerId !== '';
        
        const formData = {
            title: $('#banner-title').val(),
            description: $('#banner-description').val(),
            link_url: $('#banner-link').val(),
            position: $('#banner-position').val(),
            status: $('#banner-status').is(':checked')
        };
        
        if (isEdit) {
            formData.id = bannerId;
        }
        
        // Handle image upload first if needed
        const imageFile = $('#banner-image')[0].files[0];
        if (imageFile) {
            uploadBannerImage(imageFile, function(imagePath) {
                formData.image_path = imagePath;
                saveBanner(formData, isEdit);
            });
        } else {
            saveBanner(formData, isEdit);
        }
    });
    
    function uploadBannerImage(file, callback) {
        const formData = new FormData();
        formData.append('image', file);
        
        $.ajax({
            url: '/admin/upload',
            method: 'POST',
            data: formData,
            processData: false,
            contentType: false,
            success: function(response) {
                if (response.success) {
                    callback(response.image_path);
                } else {
                    showAlert(response.error, 'error');
                }
            },
            error: function(xhr, status, error) {
                showAlert('Lỗi upload ảnh: ' + error, 'error');
            }
        });
    }
    
    function saveBanner(data, isEdit) {
        const method = isEdit ? 'PUT' : 'POST';
        
        $.ajax({
            url: '/admin/api/banners',
            method: method,
            contentType: 'application/json',
            data: JSON.stringify(data),
            success: function(response) {
                if (response.success) {
                    showAlert(response.message, 'success');
                    closeBannerModal();
                    loadBanners();
                } else {
                    showAlert(response.error, 'error');
                }
            },
            error: function(xhr, status, error) {
                showAlert('Lỗi lưu banner: ' + error, 'error');
            }
        });
    }
    
    // Banner actions
    $(document).on('click', '.edit-banner', function() {
        const bannerId = $(this).data('id');
        showBannerModal(bannerId);
    });
    
    $(document).on('click', '.delete-banner', function() {
        const bannerId = $(this).data('id');
        
        if (confirm('Bạn có chắc chắn muốn xóa banner này?')) {
            deleteBanner(bannerId);
        }
    });
    
    function deleteBanner(bannerId) {
        $.ajax({
            url: '/admin/api/banners',
            method: 'DELETE',
            contentType: 'application/json',
            data: JSON.stringify({ id: bannerId }),
            success: function(response) {
                if (response.success) {
                    showAlert(response.message, 'success');
                    loadBanners();
                } else {
                    showAlert(response.error, 'error');
                }
            },
            error: function(xhr, status, error) {
                showAlert('Lỗi xóa banner: ' + error, 'error');
            }
        });
    }
    
    // Settings management
    window.saveSettings = function() {
        const settings = {};
        
        $('#settings-form input').each(function() {
            const name = $(this).attr('name');
            const value = $(this).val();
            settings[name] = value;
        });
        
        $.ajax({
            url: '/admin/api/settings',
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ settings: settings }),
            success: function(response) {
                if (response.success) {
                    showAlert(response.message, 'success');
                } else {
                    showAlert(response.error, 'error');
                }
            },
            error: function(xhr, status, error) {
                showAlert('Lỗi lưu cài đặt: ' + error, 'error');
            }
        });
    };
    
    // Refresh functions
    window.refreshVisitors = function() {
        loadVisitors();
        showAlert('Đã làm mới danh sách visitors', 'info');
    };
    
    window.refreshDownloads = function() {
        loadDownloads();
        showAlert('Đã làm mới thống kê downloads', 'info');
    };
    
    // Image preview for banner upload
    $('#banner-image').change(function(e) {
        const file = e.target.files[0];
        if (file) {
            const reader = new FileReader();
            reader.onload = function(e) {
                $('#image-preview').html(`<img src="${e.target.result}" alt="Preview">`);
            };
            reader.readAsDataURL(file);
        }
    });
    
    // Modal handlers
    $('.close').click(function() {
        $(this).closest('.modal').removeClass('active');
    });
    
    $(window).click(function(event) {
        if ($(event.target).hasClass('modal')) {
            $('.modal').removeClass('active');
        }
    });
    
    // Utility functions
    function formatDateTime(dateString) {
        if (!dateString) return 'N/A';
        
        const date = new Date(dateString);
        return date.toLocaleString('vi-VN', {
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit'
        });
    }
    
    function showAlert(message, type) {
        // Remove existing alerts
        $('.alert').remove();
        
        const alertClass = type === 'error' ? 'alert-error' : 
                          type === 'success' ? 'alert-success' : 
                          type === 'info' ? 'alert-info' : 'alert-info';
        
        const icon = type === 'error' ? 'fas fa-exclamation-circle' : 
                    type === 'success' ? 'fas fa-check-circle' : 
                    'fas fa-info-circle';
        
        const alert = $(`
            <div class="alert ${alertClass} fade-in" style="position: fixed; top: 20px; right: 20px; z-index: 9999; max-width: 400px;">
                <i class="${icon}"></i>
                ${message}
            </div>
        `);
        
        $('body').append(alert);
        
        // Auto remove after 5 seconds
        setTimeout(function() {
            alert.fadeOut(function() {
                $(this).remove();
            });
        }, 5000);
    }
    
    // Search functionality
    $('.search-input').on('input', function() {
        const searchTerm = $(this).val().toLowerCase();
        const targetTable = $(this).data('target');
        
        $(`${targetTable} tbody tr`).each(function() {
            const rowText = $(this).text().toLowerCase();
            if (rowText.includes(searchTerm)) {
                $(this).show();
            } else {
                $(this).hide();
            }
        });
    });
    
    // Export data functionality
    window.exportData = function(type) {
        let data = [];
        let filename = '';
        
        switch(type) {
            case 'visitors':
                data = getTableData('#visitors-table');
                filename = 'visitors_export.csv';
                break;
            case 'downloads':
                data = getTableData('#downloads-table');
                filename = 'downloads_export.csv';
                break;
            case 'banners':
                // Get banner data from current display
                filename = 'banners_export.csv';
                break;
        }
        
        if (data.length > 0) {
            downloadCSV(data, filename);
        }
    };
    
    function getTableData(tableSelector) {
        const data = [];
        const headers = [];
        
        // Get headers
        $(tableSelector + ' thead th').each(function() {
            headers.push($(this).text());
        });
        data.push(headers);
        
        // Get rows
        $(tableSelector + ' tbody tr:visible').each(function() {
            const row = [];
            $(this).find('td').each(function() {
                row.push($(this).text().replace(/,/g, ';')); // Replace commas to avoid CSV conflicts
            });
            data.push(row);
        });
        
        return data;
    }
    
    function downloadCSV(data, filename) {
        const csvContent = data.map(row => row.join(',')).join('\n');
        const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
        
        const link = document.createElement('a');
        const url = URL.createObjectURL(blob);
        link.setAttribute('href', url);
        link.setAttribute('download', filename);
        link.style.visibility = 'hidden';
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        
        showAlert(`Đã xuất file ${filename}`, 'success');
    }
    
    // Real-time updates
    let wsConnection = null;
    
    function initWebSocket() {
        // In a real application, you would connect to a WebSocket server
        // for real-time updates
        console.log('WebSocket connection would be initialized here');
    }
    
    // Keyboard shortcuts
    $(document).keydown(function(e) {
        // Ctrl+S to save settings
        if (e.ctrlKey && e.which === 83 && currentSection === 'settings') {
            e.preventDefault();
            saveSettings();
        }
        
        // Ctrl+N to add new banner
        if (e.ctrlKey && e.which === 78 && currentSection === 'banners') {
            e.preventDefault();
            showBannerModal();
        }
        
        // Escape to close modals
        if (e.which === 27) {
            $('.modal').removeClass('active');
        }
        
        // F5 to refresh current section
        if (e.which === 116) {
            e.preventDefault();
            switch(currentSection) {
                case 'visitors':
                    refreshVisitors();
                    break;
                case 'downloads':
                    refreshDownloads();
                    break;
                default:
                    location.reload();
            }
        }
    });
    
    // Dark mode toggle (future feature)
    window.toggleDarkMode = function() {
        $('body').toggleClass('dark-mode');
        localStorage.setItem('darkMode', $('body').hasClass('dark-mode'));
    };
    
    // Initialize dark mode from localStorage
    if (localStorage.getItem('darkMode') === 'true') {
        $('body').addClass('dark-mode');
    }
    
    // Responsive sidebar toggle
    $('.mobile-menu-toggle').click(function() {
        $('.sidebar').toggleClass('active');
    });
    
    // Auto-save form data
    let autoSaveTimeout;
    
    $('#settings-form').on('input', 'input', function() {
        clearTimeout(autoSaveTimeout);
        autoSaveTimeout = setTimeout(function() {
            showAlert('Tự động lưu cài đặt...', 'info');
            saveSettings();
        }, 2000);
    });
    
    // Performance monitoring
    const performanceData = {
        pageLoad: performance.now(),
        apiCalls: 0,
        errors: 0,
        lastUpdate: Date.now()
    };
    
    // Track API calls
    $(document).ajaxSend(function() {
        performanceData.apiCalls++;
    });
    
    $(document).ajaxError(function() {
        performanceData.errors++;
    });
    
    // Monitor memory usage
    setInterval(function() {
        if (performance.memory) {
            const memoryInfo = {
                used: Math.round(performance.memory.usedJSHeapSize / 1048576),
                total: Math.round(performance.memory.totalJSHeapSize / 1048576)
            };
            
            // Log memory usage if it's getting high
            if (memoryInfo.used > 100) { // 100MB threshold
                console.warn('High memory usage detected:', memoryInfo);
            }
        }
    }, 30000);
    
    // Session timeout warning
    let sessionWarningShown = false;
    
    setInterval(function() {
        // Check if session is still valid
        $.ajax({
            url: '/admin/api/stats',
            method: 'GET',
            success: function() {
                sessionWarningShown = false;
            },
            error: function(xhr) {
                if (xhr.status === 401 && !sessionWarningShown) {
                    sessionWarningShown = true;
                    showAlert('Phiên đăng nhập sắp hết hạn. Vui lòng làm mới trang.', 'error');
                }
            }
        });
    }, 300000); // Check every 5 minutes
    
    // Bulk operations
    window.bulkDeleteBanners = function() {
        const selectedBanners = $('.banner-checkbox:checked').map(function() {
            return $(this).val();
        }).get();
        
        if (selectedBanners.length === 0) {
            showAlert('Vui lòng chọn banner để xóa', 'error');
            return;
        }
        
        if (confirm(`Bạn có chắc chắn muốn xóa ${selectedBanners.length} banner(s)?`)) {
            // Implement bulk delete
            showAlert('Tính năng đang phát triển', 'info');
        }
    };
    
    // Data validation
    function validateBannerForm() {
        const title = $('#banner-title').val().trim();
        const position = $('#banner-position').val();
        
        if (!title) {
            showAlert('Vui lòng nhập tiêu đề banner', 'error');
            return false;
        }
        
        if (!position) {
            showAlert('Vui lòng chọn vị trí banner', 'error');
            return false;
        }
        
        return true;
    }
    
    // Add validation to banner form
    $('#banner-form').submit(function(e) {
        if (!validateBannerForm()) {
            e.preventDefault();
            return false;
        }
    });
    
    // Initialize tooltips for admin panel
    $('[title]').hover(function() {
        const title = $(this).attr('title');
        $(this).attr('data-original-title', title);
        $(this).removeAttr('title');
        
        const tooltip = $(`<div class="tooltip">${title}</div>`);
        $('body').append(tooltip);
        
        const rect = this.getBoundingClientRect();
        tooltip.css({
            position: 'fixed',
            top: rect.bottom + 5,
            left: rect.left + (rect.width / 2) - (tooltip.outerWidth() / 2),
            zIndex: 10000
        }).fadeIn(200);
    }, function() {
        const originalTitle = $(this).attr('data-original-title');
        if (originalTitle) {
            $(this).attr('title', originalTitle);
            $(this).removeAttr('data-original-title');
        }
        $('.tooltip').fadeOut(200, function() {
            $(this).remove();
        });
    });
    
    // Initialize admin panel
    console.log('Admin panel fully loaded');
    
    // Load initial dashboard
    loadDashboardData();
});

// Global admin functions
window.adminFunctions = {
    showBannerModal: showBannerModal,
    closeBannerModal: closeBannerModal,
    saveSettings: saveSettings,
    refreshVisitors: refreshVisitors,
    refreshDownloads: refreshDownloads,
    exportData: exportData,
    toggleDarkMode: toggleDarkMode,
    bulkDeleteBanners: bulkDeleteBanners
};
