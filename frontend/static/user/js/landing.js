document.addEventListener('DOMContentLoaded', () => {





    const header = document.getElementById('header');

    window.addEventListener('scroll', () => {
        if (window.scrollY > 50) {
            header.classList.add('scrolled');
        } else {
            header.classList.remove('scrolled');
        }
    });


    const mobileMenuToggle = document.getElementById('mobile-menu-toggle');
    const mainNav = document.querySelector('.main-nav');

    if (mobileMenuToggle && mainNav) {
        mobileMenuToggle.addEventListener('click', () => {
            const isOpen = mainNav.classList.toggle('is-open');
            document.body.classList.toggle('sl-menu-open', isOpen);
            mobileMenuToggle.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
        });

        mainNav.querySelectorAll('a').forEach(link => {
            link.addEventListener('click', () => {
                mainNav.classList.remove('is-open');
                document.body.classList.remove('sl-menu-open');
                mobileMenuToggle.setAttribute('aria-expanded', 'false');
            });
        });


        window.addEventListener('resize', () => {
            if (window.innerWidth >= 992) {
                mainNav.classList.remove('is-open');
                document.body.classList.remove('sl-menu-open');
                mobileMenuToggle.setAttribute('aria-expanded', 'false');
            }
        });
    }


    const tabBtns = document.querySelectorAll('.tab-btn');
    const tabPanes = document.querySelectorAll('.tab-pane');

    if (tabBtns.length > 0 && tabPanes.length > 0) {
        tabBtns.forEach(btn => {
            btn.addEventListener('click', () => {

                tabBtns.forEach(b => b.classList.remove('active'));
                tabPanes.forEach(p => p.classList.remove('active'));


                btn.classList.add('active');


                const targetTab = btn.getAttribute('data-tab');
                document.getElementById(targetTab).classList.add('active');
            });
        });
    }


    const lightbox = document.getElementById('lightbox');
    const lightboxImg = document.getElementById('lightbox-img');
    const lightboxClose = document.getElementById('lightbox-close');
    const lightboxTriggers = document.querySelectorAll('.lightbox-trigger');

    if (lightbox && lightboxImg && lightboxClose) {
        lightboxTriggers.forEach(trigger => {
            trigger.addEventListener('click', function() {
                lightbox.classList.add('active');
                lightboxImg.src = this.src;
                document.body.style.overflow = 'hidden';
            });
        });


        const closeLightbox = () => {
            lightbox.classList.remove('active');
            setTimeout(() => {
                lightboxImg.removeAttribute('src');
                document.body.style.overflow = '';
            }, 300);
        };

        lightboxClose.addEventListener('click', closeLightbox);


        lightbox.addEventListener('click', (e) => {
            if (e.target === lightbox) {
                closeLightbox();
            }
        });


        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && lightbox.classList.contains('active')) {
                closeLightbox();
            }
        });
    }


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


    const projectsSlider = document.getElementById('projects-slider');
    const sliderPrev = document.getElementById('slider-prev');
    const sliderNext = document.getElementById('slider-next');

    if (projectsSlider && sliderPrev && sliderNext) {

        let projectsData = (Array.isArray(window.__projects__) && window.__projects__.length)
            ? window.__projects__
            : [];
        if (!projectsData.length) {
            const dataEl = document.getElementById('projects-data');
            if (dataEl) {
                try { projectsData = JSON.parse(dataEl.textContent); } catch (e) {}
            }
        }

        const getSliderLang = () => {
            try {
                const l = localStorage.getItem('sl_lang');
                if (l === 'zh' || l === 'vi') return l;
                const m = document.cookie.match(/lang_pref=([^;]+)/);
                if (m && (m[1] === 'zh' || m[1] === 'vi')) return m[1];
                if ((navigator.language || '').toLowerCase().startsWith('zh')) return 'zh';
            } catch (e) {}
            return 'vi';
        };

        const defaultProjectsVi = [
            {
                name: 'Nhà xưởng KCN Trung Hà (Lô E5)',
                location: 'KCN Trung Hà, Tam Nông, Phú Thọ',
                area: '4.032 m² & 2.730 m²',
                height: '10.85 m',
                bays: 'Cửa container',
                load: '3.0 Tấn/m²',
                image: '/static/brain/tsl/hero_factory.png',
                description: 'Nhà xưởng xây sẵn tiêu chuẩn quốc tế, trạm biến áp 5000 KVA, PCCC tự động đã nghiệm thu, sẵn sàng bàn giao ngay.',
                detail_url: '/nhaxuong'
            },
            {
                name: 'Nhà xưởng KCN Cổ Tiết',
                location: 'Khu Đồng Dâu, Vạn Xuân, Phú Thọ',
                area: 'Từ 1.000 m² – 10.000 m²',
                height: '9.5 m',
                bays: 'Tiêu chuẩn KCN',
                load: '3.0 Tấn/m²',
                image: '/static/brain/tsl/z7785901889175_7c9d549d0be200ce538456d0451a61cd.jpg',
                description: 'Hạ tầng đồng bộ, xử lý nước thải đạt chuẩn, trạm biến áp 110KV, pháp lý hoàn chỉnh với thời hạn đất còn hơn 45 năm.',
                detail_url: '/nha-xuong-co-tiet'
            }
        ];

        const defaultProjectsZh = [
            {
                name: '中河工业区标准厂房 (E5地块)',
                location: '富寿省三农县中河工业区',
                area: '4,032 m² 及 2,730 m²',
                height: '10.85 m',
                bays: '重型货柜装卸门',
                load: '3.0 吨/m²',
                image: '/static/brain/tsl/hero_factory.png',
                description: '国际标准现成厂房，配备 5000 KVA 专用变电站、公安部认证自动消防系统，随时整租或分租交付入驻。',
                detail_url: '/nhaxuong'
            },
            {
                name: '古节工业区高标准厂房',
                location: '富寿省万春社同油工业区',
                area: '1,000 m² – 10,000 m² 灵活可选',
                height: '9.5 m',
                bays: '工业区标准闸口',
                load: '3.0 吨/m²',
                image: '/static/brain/tsl/z7785901889175_7c9d549d0be200ce538456d0451a61cd.jpg',
                description: '基础设施完备，合规污水处理站、110KV 电网与5000 KVA 配电，工业土地剩余使用年限45年以上。',
                detail_url: '/nha-xuong-co-tiet'
            }
        ];

        const useFallbackData = !projectsData || projectsData.length === 0;
        if (useFallbackData) {
            projectsData = defaultProjectsVi;
        }

        let activeIndex = 0;

        const renderSlider = () => {
            const lang = getSliderLang();
            const isZh = (lang === 'zh');
            const activeList = useFallbackData ? (isZh ? defaultProjectsZh : defaultProjectsVi) : projectsData;
            const labels = isZh
                ? {
                    badge: '现可入驻',
                    area: '总面积',
                    height: '厂房净高',
                    bays: '装卸通道',
                    load: '地面承重',
                    contact: '立即联系'
                  }
                : {
                    badge: 'Sẵn sàng bàn giao',
                    area: 'Diện tích tổng',
                    height: 'Chiều cao',
                    bays: 'Cửa xuất nhập',
                    load: 'Sức chịu tải',
                    contact: 'Liên hệ ngay'
                  };

            projectsSlider.innerHTML = activeList.map((project, index) => {
                const isActive = index === activeIndex ? 'active' : '';
                const localImage = project.image || '/static/brain/tsl/hero_factory.png';
                const detailUrl = project.detail_url || '/nhaxuong';

                return `
                <div class="slider-item ${isActive}">
                    <div class="slider-image-col">
                        <img src="${localImage}" class="slider-img" alt="${project.name ? 'Dự án ' + project.name : 'Dự án nhà xưởng THÉP SƠN LÂM'}" decoding="async">
                        <div class="slider-img-overlay"></div>
                        <div class="slider-badge-container">
                            <span class="badge badge-primary uppercase tracking-widest">${labels.badge}</span>
                            <p class="location-text"><span class="material-symbols-outlined">location_on</span> ${project.location || ''}</p>
                        </div>
                    </div>
                    <div class="slider-info-col">
                        <div class="info-top">
                            <h3 class="info-title">${project.name || ''}</h3>
                            <p class="info-desc">${project.description || ''}</p>
                            <div class="specs-grid">
                                <div class="spec-item">
                                    <span class="spec-label">${labels.area}</span>
                                    <strong class="spec-value">${project.area || '-'}</strong>
                                </div>
                                <div class="spec-item">
                                    <span class="spec-label">${labels.height}</span>
                                    <strong class="spec-value">${project.height || '-'}</strong>
                                </div>
                                <div class="spec-item">
                                    <span class="spec-label">${labels.bays}</span>
                                    <strong class="spec-value">${project.bays || '-'}</strong>
                                </div>
                                <div class="spec-item">
                                    <span class="spec-label">${labels.load}</span>
                                    <strong class="spec-value">${project.load || '-'}</strong>
                                </div>
                            </div>
                        </div>
                        <div class="info-actions">
                            <a href="/#lien-he" class="btn btn-primary btn-full flex-center gap-2">
                                <span class="material-symbols-outlined">call</span> ${labels.contact}
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
        window.addEventListener('sl-lang-changed', renderSlider);

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
