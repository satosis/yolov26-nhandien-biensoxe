document.addEventListener('DOMContentLoaded', () => {
    // --- Active Navigation Link ---
    // NOTE: active state is rendered server-side (Jinja2) using clean URLs,
    // so the legacy client-side .html path matching is disabled here.

    // --- Sticky Header ---
    const header = document.getElementById('header');
    
    window.addEventListener('scroll', () => {
        if (window.scrollY > 50) {
            header.classList.add('scrolled');
        } else {
            header.classList.remove('scrolled');
        }
    });

    // --- Mobile Menu Toggle ---
    const mobileMenuToggle = document.getElementById('mobile-menu-toggle');
    const mainNav = document.querySelector('.main-nav');
    
    if (mobileMenuToggle && mainNav) {
        mobileMenuToggle.addEventListener('click', () => {
            const isOpen = mainNav.classList.toggle('is-open');
            mobileMenuToggle.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
        });

        mainNav.querySelectorAll('a').forEach(link => {
            link.addEventListener('click', () => {
                mainNav.classList.remove('is-open');
                mobileMenuToggle.setAttribute('aria-expanded', 'false');
            });
        });
        
        // Reset mobile menu on resize
        window.addEventListener('resize', () => {
            if (window.innerWidth >= 992) {
                mainNav.classList.remove('is-open');
                mobileMenuToggle.setAttribute('aria-expanded', 'false');
            }
        });
    }

    // --- Tabs Functionality (for Projects page) ---
    const tabBtns = document.querySelectorAll('.tab-btn');
    const tabPanes = document.querySelectorAll('.tab-pane');

    if (tabBtns.length > 0 && tabPanes.length > 0) {
        tabBtns.forEach(btn => {
            btn.addEventListener('click', () => {
                // Remove active class from all buttons and panes
                tabBtns.forEach(b => b.classList.remove('active'));
                tabPanes.forEach(p => p.classList.remove('active'));

                // Add active class to clicked button
                btn.classList.add('active');

                // Add active class to corresponding pane
                const targetTab = btn.getAttribute('data-tab');
                document.getElementById(targetTab).classList.add('active');
            });
        });
    }

    // --- Lightbox Functionality ---
    const lightbox = document.getElementById('lightbox');
    const lightboxImg = document.getElementById('lightbox-img');
    const lightboxClose = document.getElementById('lightbox-close');
    const lightboxTriggers = document.querySelectorAll('.lightbox-trigger');

    if (lightbox && lightboxImg && lightboxClose) {
        lightboxTriggers.forEach(trigger => {
            trigger.addEventListener('click', function() {
                lightbox.classList.add('active');
                lightboxImg.src = this.src;
                document.body.style.overflow = 'hidden'; // Prevent background scrolling
            });
        });

        // Close lightbox
        const closeLightbox = () => {
            lightbox.classList.remove('active');
            setTimeout(() => {
                lightboxImg.src = ''; // Clear image source after animation
                document.body.style.overflow = ''; // Restore scrolling
            }, 300); // Wait for transition
        };

        lightboxClose.addEventListener('click', closeLightbox);
        
        // Close on background click
        lightbox.addEventListener('click', (e) => {
            if (e.target === lightbox) {
                closeLightbox();
            }
        });

        // Close on Escape key
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && lightbox.classList.contains('active')) {
                closeLightbox();
            }
        });
    }

    // --- Warehouse Gallery Slider ---
    const warehouseGallery = document.getElementById('warehouse-gallery');
    const warehouseSlides = document.querySelectorAll('.warehouse-slide');
    const warehouseDots = document.querySelectorAll('.warehouse-dot');
    const warehousePrev = document.getElementById('warehouse-slider-prev');
    const warehouseNext = document.getElementById('warehouse-slider-next');

    if (warehouseGallery && warehouseSlides.length > 0 && warehouseDots.length > 0 && warehousePrev && warehouseNext) {
        let warehouseActiveIndex = 0;
        let galleryTimer;

        const showWarehouseSlide = (index) => {
            warehouseActiveIndex = (index + warehouseSlides.length) % warehouseSlides.length;

            warehouseSlides.forEach((slide, slideIndex) => {
                slide.classList.toggle('active', slideIndex === warehouseActiveIndex);
            });

            warehouseDots.forEach((dot, dotIndex) => {
                dot.classList.toggle('active', dotIndex === warehouseActiveIndex);
                dot.setAttribute('aria-current', dotIndex === warehouseActiveIndex ? 'true' : 'false');
            });
        };

        const nextWarehouseSlide = () => showWarehouseSlide(warehouseActiveIndex + 1);
        const prevWarehouseSlide = () => showWarehouseSlide(warehouseActiveIndex - 1);
        const startGalleryTimer = () => {
            galleryTimer = window.setInterval(nextWarehouseSlide, 10000);
        };
        const stopGalleryTimer = () => {
            window.clearInterval(galleryTimer);
        };

        warehouseNext.addEventListener('click', () => {
            stopGalleryTimer();
            nextWarehouseSlide();
            startGalleryTimer();
        });

        warehousePrev.addEventListener('click', () => {
            stopGalleryTimer();
            prevWarehouseSlide();
            startGalleryTimer();
        });

        warehouseDots.forEach((dot, index) => {
            dot.addEventListener('click', () => {
                stopGalleryTimer();
                showWarehouseSlide(index);
                startGalleryTimer();
            });
        });

        warehouseGallery.addEventListener('mouseenter', stopGalleryTimer);
        warehouseGallery.addEventListener('mouseleave', startGalleryTimer);
        warehouseGallery.addEventListener('focusin', stopGalleryTimer);
        warehouseGallery.addEventListener('focusout', startGalleryTimer);

        showWarehouseSlide(0);
        startGalleryTimer();
    }

    // --- Premium Projects Slider ---
    const projectsSlider = document.getElementById('projects-slider');
    const sliderPrev = document.getElementById('slider-prev');
    const sliderNext = document.getElementById('slider-next');

    if (projectsSlider && sliderPrev && sliderNext) {
        // Data injected server-side from DB via window.__projects__ (fallback to []).
        const projectsData = (Array.isArray(window.__projects__) && window.__projects__.length)
            ? window.__projects__
            : [];

        if (projectsData.length === 0) {
            projectsSlider.innerHTML = '<div class="slider-item active" style="padding:3rem;text-align:center;color:rgba(255,255,255,0.6);">Chưa có dự án nào được cập nhật.</div>';
            sliderPrev.style.display = 'none';
            sliderNext.style.display = 'none';
            return;
        }

        let activeIndex = 0;

        const renderSlider = () => {
            projectsSlider.innerHTML = projectsData.map((project, index) => {
                const isActive = index === activeIndex ? 'active' : '';
                const localImage = project.image || '/static/brain/tsl/hero_factory.png';
                const detailUrl = project.detail_url || '/nha-xuong';

                return `
                <div class="slider-item ${isActive}">
                    <div class="slider-image-col">
                        <img src="${localImage}" class="slider-img" alt="${project.name || ''}">
                        <div class="slider-img-overlay"></div>
                        <div class="slider-badge-container">
                            <span class="badge badge-primary uppercase tracking-widest">Available Now</span>
                            <p class="location-text"><span class="material-symbols-outlined">location_on</span> ${project.location || ''}</p>
                        </div>
                    </div>
                    <div class="slider-info-col">
                        <div class="info-top">
                            <h3 class="info-title">${project.name || ''}</h3>
                            <p class="info-desc">${project.description || ''}</p>
                            <div class="specs-grid">
                                <div class="spec-item">
                                    <span class="spec-label">Diện tích tổng</span>
                                    <strong class="spec-value">${project.area || '-'}</strong>
                                </div>
                                <div class="spec-item">
                                    <span class="spec-label">Chiều cao</span>
                                    <strong class="spec-value">${project.height || '-'}</strong>
                                </div>
                                <div class="spec-item">
                                    <span class="spec-label">Cửa xuất nhập</span>
                                    <strong class="spec-value">${project.bays || '-'}</strong>
                                </div>
                                <div class="spec-item">
                                    <span class="spec-label">Sức chịu tải</span>
                                    <strong class="spec-value">${project.load || '-'}</strong>
                                </div>
                            </div>
                        </div>
                        <div class="info-actions">
                            <a href="/#lien-he" class="btn btn-primary btn-full flex-center gap-2">
                                <span class="material-symbols-outlined">call</span> Liên hệ ngay
                            </a>
                            <a href="${detailUrl}" class="btn btn-outline icon-btn flex-center" aria-label="Xem chi tiết ${project.name || ''}">
                                <span class="material-symbols-outlined">info</span>
                            </a>
                        </div>
                    </div>
                </div>`;
            }).join('');
        };

        renderSlider();

        sliderNext.addEventListener('click', () => {
            activeIndex = (activeIndex + 1) % projectsData.length;
            renderSlider();
        });

        sliderPrev.addEventListener('click', () => {
            activeIndex = (activeIndex - 1 + projectsData.length) % projectsData.length;
            renderSlider();
        });
    }
});
