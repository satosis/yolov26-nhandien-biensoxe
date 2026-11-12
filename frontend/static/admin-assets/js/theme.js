/**
 * Theme: Opatix - Admin & Dashboard Template
 * Author: Myra Studio
 * File: Main Js
 */


(function ($) {

    'use strict';

    function initMetisMenu() {
        // Toggle each sidebar group independently of the others.
        $(document).on("click", "#side-menu a.has-arrow", function (e) {
            e.preventDefault();
            var $a = $(this);
            var $li = $a.parent();
            var $sub = $a.next("ul");
            var isOpen = $li.hasClass("mm-active");
            if (isOpen) {
                $li.removeClass("mm-active");
                $sub.removeClass("mm-show").attr("aria-expanded", false);
                $a.attr("aria-expanded", false);
            } else {
                $li.addClass("mm-active");
                $sub.addClass("mm-show").attr("aria-expanded", true);
                $a.attr("aria-expanded", true);
            }
        });
    }

    function initLeftMenuCollapse() {
        // Left menu collapse
        $('#vertical-menu-btn').on('click', function (e) {
            e.preventDefault();
            if ($(window).width() < 992) {
                $('body').toggleClass('enable-vertical-menu');
            } else {
                $('body').toggleClass('sidebar-hidden');
            }
        });

        $('.menu-overlay').on('click', function () {
            $('body').removeClass('enable-vertical-menu');
            return;
        });
    }

    function initActiveMenu() {
        // === following js will activate the menu in left side bar based on url ====
        $("#sidebar-menu a").each(function () {
            var pageUrl = window.location.href.split(/[?#]/)[0];
            if (this.href == pageUrl) {
                $(this).addClass("active");
                $(this).parent().addClass("mm-active"); // add active to li of the current link
                $(this).parent().parent().addClass("mm-show");
                $(this).parent().parent().prev().addClass("mm-active"); // add active class to an anchor
                $(this).parent().parent().parent().addClass("mm-active");
                $(this).parent().parent().parent().parent().addClass("mm-show"); // add active to li of the current link
                $(this).parent().parent().parent().parent().parent().addClass("mm-active");
            }
        });
    }

    function initComponents() {
        $(function () {
            $('[data-toggle="tooltip"]').tooltip()
        })

        $(function () {
            $('[data-toggle="popover"]').popover()
        })
    }

    function init() {
        initMetisMenu();
        initLeftMenuCollapse();
        initActiveMenu();
        initComponents();
        Waves.init();
    }

    init();

})(jQuery)
